import math
import re
import secrets
from datetime import datetime, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    LengthUnit,
    Material,
    ObjectSource,
    ObjectType,
    SpaceType,
    Style,
)

ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
SEARCH_STATUSES = {'idle', 'running', 'done', 'empty'}
SEARCH_STEPS = {'planning', 'building-query', 'querying-catalog', 'ranking'}

SPACE_TYPES = set(SpaceType.values)
UNITS = set(LengthUnit.values)
OBJECT_TYPES = set(ObjectType.values)
MATERIALS = set(Material.values)
STYLES = set(Style.values)
SOURCES = set(ObjectSource.values)

SPACE_KEYS = {'name', 'type', 'unit', 'dims', 'weightLimit'}
OBJECT_KEYS = {
    'name', 'dims', 'weight', 'type', 'material', 'style', 'priceLimit',
    'similarItems', 'source', 'owned', 'candidateIds', 'selectedProductId', 'search',
}


class ValidationFailed(Exception):
    def __init__(self, details):
        super().__init__('validation_failed')
        self.details = details


# ---------------------------------------------------------------- output

def iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def dims_to_json(dims):
    out = {'x': dims['x'], 'y': dims['y']}
    if dims.get('z') is not None:
        out['z'] = dims['z']
    return out


def space_to_json(space):
    out = {
        'id': space.id,
        'name': space.name,
        'type': space.type,
        'unit': space.unit,
        'dims': dims_to_json(space.dims),
    }
    if space.weight_limit is not None:
        out['weightLimit'] = space.weight_limit
    out['placements'] = [
        {'objectId': p.object_id, 'x': p.x, 'y': p.y}
        for p in space.placements.all()
    ]
    out['createdAt'] = iso(space.created_at)
    out['updatedAt'] = iso(space.updated_at)
    return out


def object_to_json(obj):
    out = {'id': obj.id, 'name': obj.name, 'dims': dims_to_json(obj.dims)}
    if obj.weight is not None:
        out['weight'] = obj.weight
    out['type'] = obj.type
    if obj.material is not None:
        out['material'] = obj.material
    if obj.style is not None:
        out['style'] = obj.style
    if obj.price_limit is not None:
        out['priceLimit'] = obj.price_limit
    out['similarItems'] = list(obj.similar_items)
    out['source'] = obj.source
    if obj.owned is not None:
        out['owned'] = obj.owned
    out['candidateIds'] = list(obj.candidate_ids)
    if obj.selected_product_id is not None:
        out['selectedProductId'] = obj.selected_product_id
    out['search'] = obj.search
    out['createdAt'] = iso(obj.created_at)
    return out


# ------------------------------------------------------------ validators

def generate_id(prefix):
    return f'{prefix}_{secrets.token_hex(4)}'


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num(v, path, errors, minimum=0, strict=False):
    if not _is_number(v):
        errors[path] = 'must be a finite number'
        return None
    if (strict and v <= minimum) or (not strict and v < minimum):
        errors[path] = f'must be {"greater than" if strict else "at least"} {minimum}'
        return None
    return float(v)


def _nullable_num(data, key, path, errors):
    """Returns (present, value)."""
    if key not in data:
        return False, None
    if data[key] is None:
        return True, None
    v = _num(data[key], path, errors)
    return v is not None, v


def _name(v, path, errors):
    if not isinstance(v, str):
        errors[path] = 'must be a string'
        return None
    v = v.strip()
    if not v:
        errors[path] = 'must not be empty'
        return None
    if len(v) > 120:
        errors[path] = 'must be at most 120 characters'
        return None
    return v


def _enum(v, allowed, path, errors, nullable=False):
    if v is None and nullable:
        return True, None
    if not isinstance(v, str) or v not in allowed:
        errors[path] = f'must be one of: {", ".join(sorted(allowed))}' + (' or null' if nullable else '')
        return False, None
    return True, v


def _dims(v, path, errors):
    if not isinstance(v, dict):
        errors[path] = 'must be an object'
        return None
    ok = True
    for k in v:
        if k not in ('x', 'y', 'z'):
            errors[f'{path}.{k}'] = 'unknown key'
            ok = False
    out = {}
    for k in ('x', 'y'):
        if k not in v:
            errors[f'{path}.{k}'] = 'is required'
            ok = False
        else:
            n = _num(v[k], f'{path}.{k}', errors, 0, strict=True)
            if n is None:
                ok = False
            else:
                out[k] = n
    if 'z' in v and v['z'] is not None:
        n = _num(v['z'], f'{path}.z', errors, 0, strict=True)
        if n is None:
            ok = False
        else:
            out['z'] = n
    return out if ok else None


def _str_list(v, path, errors):
    if not isinstance(v, list):
        errors[path] = 'must be a list of strings'
        return None
    out = []
    ok = True
    for i, s in enumerate(v):
        if not isinstance(s, str):
            errors[f'{path}[{i}]'] = 'must be a string'
            ok = False
            continue
        out.append(s)
    return out if ok else None


def _similar(v, path, errors):
    if not isinstance(v, list):
        errors[path] = 'must be a list of strings'
        return None
    ok = True
    items = []
    for i, s in enumerate(v):
        if not isinstance(s, str):
            errors[f'{path}[{i}]'] = 'must be a string'
            ok = False
            continue
        s = s.strip()
        if not s:
            continue
        if len(s) > 80:
            errors[f'{path}[{i}]'] = 'must be at most 80 characters'
            ok = False
            continue
        items.append(s)
    if ok and len(items) > 20:
        errors[path] = 'must have at most 20 items'
        ok = False
    return items if ok else None


def _owned(v, path, errors):
    if v is None:
        return True, None
    if not isinstance(v, dict):
        errors[path] = 'must be an object or null'
        return False, None
    ok = True
    out = {}
    for k in v:
        if k not in ('productId', 'orderName', 'purchasedAt'):
            errors[f'{path}.{k}'] = 'unknown key'
            ok = False
    for k in ('productId', 'orderName', 'purchasedAt'):
        if not isinstance(v.get(k), str):
            errors[f'{path}.{k}'] = 'must be a string'
            ok = False
        else:
            out[k] = v[k]
    return ok, (out if ok else None)


def _search(v, path, errors):
    if not isinstance(v, dict):
        errors[path] = 'must be an object'
        return None
    ok = True
    out = {}
    for k in v:
        if k not in ('status', 'step', 'lastRunAt'):
            errors[f'{path}.{k}'] = 'unknown key'
            ok = False
    if v.get('status') not in SEARCH_STATUSES:
        errors[f'{path}.status'] = f'must be one of: {", ".join(sorted(SEARCH_STATUSES))}'
        ok = False
    else:
        out['status'] = v['status']
    if v.get('step') is not None:
        if v['step'] not in SEARCH_STEPS:
            errors[f'{path}.step'] = f'must be one of: {", ".join(sorted(SEARCH_STEPS))}'
            ok = False
        else:
            out['step'] = v['step']
    if v.get('lastRunAt') is not None:
        if not isinstance(v['lastRunAt'], str):
            errors[f'{path}.lastRunAt'] = 'must be a string'
            ok = False
        else:
            out['lastRunAt'] = v['lastRunAt']
    return out if ok else None


def parse_iso(v):
    if not isinstance(v, str):
        return None
    try:
        dt = parse_datetime(v.strip())
    except ValueError:
        return None
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _timestamp(v, path, errors):
    dt = parse_iso(v)
    if dt is None:
        errors[path] = 'must be an ISO 8601 timestamp string'
    return dt


def _id(v, path, errors):
    if not isinstance(v, str) or not ID_RE.match(v):
        errors[path] = 'must match ^[A-Za-z0-9_-]{1,64}$'
        return None
    return v


def _check_unknown(data, allowed, path, errors):
    for k in data:
        if k not in allowed:
            errors[f'{path}.{k}' if path else k] = 'unknown key'


def _validate(data, kind, *, partial, path='', allow_id=False, allow_timestamps=False,
              client_created_at=False, errors):
    """Returns a dict of model field values (only provided keys when partial)."""
    prefix = f'{path}.' if path else ''
    if not isinstance(data, dict):
        errors[path or '$'] = 'must be an object'
        return {}
    allowed = set(SPACE_KEYS if kind == 'space' else OBJECT_KEYS)
    if allow_id:
        allowed.add('id')
    if allow_timestamps:
        allowed.add('createdAt')
        if kind == 'space':
            allowed.add('updatedAt')
    elif client_created_at and kind == 'object':
        allowed.add('createdAt')
    _check_unknown(data, allowed, path, errors)

    out = {}
    required = {'name', 'type', 'dims'} | ({'unit'} if kind == 'space' else set())
    if not partial:
        for k in sorted(required):
            if k not in data:
                errors[prefix + k] = 'is required'

    def has(k):
        return k in data

    if has('id') and allow_id:
        v = _id(data['id'], prefix + 'id', errors)
        if v is not None:
            out['id'] = v
    if has('name'):
        v = _name(data['name'], prefix + 'name', errors)
        if v is not None:
            out['name'] = v
    if has('type'):
        ok, v = _enum(data['type'], SPACE_TYPES if kind == 'space' else OBJECT_TYPES,
                      prefix + 'type', errors)
        if ok:
            out['type'] = v
    if has('dims'):
        v = _dims(data['dims'], prefix + 'dims', errors)
        if v is not None:
            out['dims'] = v

    if kind == 'space':
        if has('unit'):
            ok, v = _enum(data['unit'], UNITS, prefix + 'unit', errors)
            if ok:
                out['unit'] = v
        present, v = _nullable_num(data, 'weightLimit', prefix + 'weightLimit', errors)
        if present:
            out['weight_limit'] = v
    else:
        present, v = _nullable_num(data, 'weight', prefix + 'weight', errors)
        if present:
            out['weight'] = v
        present, v = _nullable_num(data, 'priceLimit', prefix + 'priceLimit', errors)
        if present:
            out['price_limit'] = v
        for key, allowed_set in (('material', MATERIALS), ('style', STYLES)):
            if has(key):
                ok, v = _enum(data[key], allowed_set, prefix + key, errors, nullable=True)
                if ok:
                    out[key] = v
        if has('similarItems'):
            v = _similar(data['similarItems'], prefix + 'similarItems', errors)
            if v is not None:
                out['similar_items'] = v
        if has('source'):
            ok, v = _enum(data['source'], SOURCES, prefix + 'source', errors)
            if ok:
                out['source'] = v
        if has('owned'):
            ok, v = _owned(data['owned'], prefix + 'owned', errors)
            if ok:
                out['owned'] = v
        if has('candidateIds'):
            v = _str_list(data['candidateIds'], prefix + 'candidateIds', errors)
            if v is not None:
                out['candidate_ids'] = v
        if has('selectedProductId'):
            v = data['selectedProductId']
            if v is None or isinstance(v, str):
                out['selected_product_id'] = v
            else:
                errors[prefix + 'selectedProductId'] = 'must be a string or null'
        if has('search'):
            v = _search(data['search'], prefix + 'search', errors)
            if v is not None:
                out['search'] = v

    if has('createdAt') and (allow_timestamps or client_created_at):
        v = _timestamp(data['createdAt'], prefix + 'createdAt', errors)
        if v is not None:
            out['created_at'] = v
    if has('updatedAt') and allow_timestamps and kind == 'space':
        v = _timestamp(data['updatedAt'], prefix + 'updatedAt', errors)
        if v is not None:
            out['updated_at'] = v
    return out


def validate_space(data, *, partial=False):
    errors = {}
    out = _validate(data, 'space', partial=partial, allow_id=not partial, errors=errors)
    if errors:
        raise ValidationFailed(errors)
    return out


def validate_object(data, *, partial=False):
    errors = {}
    out = _validate(data, 'object', partial=partial, allow_id=not partial,
                    client_created_at=not partial, errors=errors)
    if errors:
        raise ValidationFailed(errors)
    return out


def validate_placement(data):
    errors = {}
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    _check_unknown(data, {'x', 'y'}, '', errors)
    out = {}
    for k in ('x', 'y'):
        if k not in data:
            errors[k] = 'is required'
        elif not _is_number(data[k]):
            errors[k] = 'must be a finite number'
        else:
            out[k] = float(data[k])
    if errors:
        raise ValidationFailed(errors)
    return out


def validate_import(data):
    """Validate an AppState. Returns (spaces, objects, ignored_keys) where
    spaces/objects are lists of validated field dicts; spaces carry 'placements'."""
    errors = {}
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    if data.get('version') != 1 or isinstance(data.get('version'), bool):
        errors['version'] = 'must be 1'
    for k in ('spaces', 'objects'):
        if not isinstance(data.get(k), list):
            errors[k] = 'must be an array'
    ignored = [k for k in data if k not in ('version', 'spaces', 'objects')]

    spaces, objects = [], []
    if isinstance(data.get('objects'), list):
        seen = set()
        for i, raw in enumerate(data['objects']):
            p = f'objects[{i}]'
            out = _validate(raw, 'object', partial=False, path=p, allow_id=True,
                            allow_timestamps=True, errors=errors)
            if isinstance(raw, dict):
                if 'id' not in raw:
                    errors[f'{p}.id'] = 'is required'
                elif 'createdAt' not in raw:
                    errors[f'{p}.createdAt'] = 'is required'
            if 'id' in out:
                if out['id'] in seen:
                    errors[f'{p}.id'] = 'duplicate id in payload'
                seen.add(out['id'])
            objects.append(out)
    if isinstance(data.get('spaces'), list):
        seen = set()
        for i, raw in enumerate(data['spaces']):
            p = f'spaces[{i}]'
            allowed_extra = {'placements'}
            if isinstance(raw, dict):
                body = {k: v for k, v in raw.items() if k not in allowed_extra}
            else:
                body = raw
            out = _validate(body, 'space', partial=False, path=p, allow_id=True,
                            allow_timestamps=True, errors=errors)
            if isinstance(raw, dict):
                for k in ('id', 'createdAt', 'updatedAt'):
                    if k not in raw:
                        errors[f'{p}.{k}'] = 'is required'
                if 'id' in out:
                    if out['id'] in seen:
                        errors[f'{p}.id'] = 'duplicate id in payload'
                    seen.add(out['id'])
                placements = []
                raw_pl = raw.get('placements', [])
                if not isinstance(raw_pl, list):
                    errors[f'{p}.placements'] = 'must be an array'
                else:
                    pseen = set()
                    for j, pl in enumerate(raw_pl):
                        pp = f'{p}.placements[{j}]'
                        if not isinstance(pl, dict):
                            errors[pp] = 'must be an object'
                            continue
                        for k in pl:
                            if k not in ('objectId', 'x', 'y'):
                                errors[f'{pp}.{k}'] = 'unknown key'
                        if not isinstance(pl.get('objectId'), str):
                            errors[f'{pp}.objectId'] = 'must be a string'
                        elif pl['objectId'] in pseen:
                            errors[f'{pp}.objectId'] = 'duplicate placement for object'
                        else:
                            pseen.add(pl['objectId'])
                        for k in ('x', 'y'):
                            if not _is_number(pl.get(k)):
                                errors[f'{pp}.{k}'] = 'must be a finite number'
                        if not any(e.startswith(pp) for e in errors):
                            placements.append(
                                {'object_id': pl['objectId'], 'x': float(pl['x']), 'y': float(pl['y'])}
                            )
                out['placements'] = placements
            spaces.append(out)
    if errors:
        raise ValidationFailed(errors)
    return spaces, objects, ignored


def now():
    return timezone.now()
