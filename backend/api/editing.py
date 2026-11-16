"""JSON payloads and updates for the space editor."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction

from .models import DimensionUnit, Material, Object, ObjectType, Space, Style

_DIMENSION_QUANT = Decimal('0.0001')
_PRICE_QUANT = Decimal('0.01')
_INCHES_PER_FOOT = Decimal('12')
_METERS_PER_INCH = Decimal('0.0254')

_OBJECT_FIELDS = frozenset(
    {
        'x',
        'y',
        'z',
        'weight',
        'object_type',
        'material',
        'style',
        'price_limit',
        'similar_items',
        'space_ids',
        'editing_space_id',
    }
)

_CHOICES = {
    'object_type': ObjectType.choices,
    'material': Material.choices,
    'style': Style.choices,
}


def snap_to_editing_grid(value: Decimal, space: Space) -> Decimal:
    """Snap a coordinate to the space's editing grid.

    Object x, y, and z are stored in the space's dimension unit (feet or
    meters). The grid increment is always in inches.
    """
    increment = space.editing_grid_increment_inches
    if increment <= 0:
        return value.quantize(_DIMENSION_QUANT)
    if space.dimension_unit == DimensionUnit.M:
        inches = value / _METERS_PER_INCH
    else:
        inches = value * _INCHES_PER_FOOT
    steps = (inches / increment).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    snapped_inches = steps * increment
    if space.dimension_unit == DimensionUnit.M:
        snapped = snapped_inches * _METERS_PER_INCH
    else:
        snapped = snapped_inches / _INCHES_PER_FOOT
    return snapped.quantize(_DIMENSION_QUANT)


def _pretty_number(value: Decimal) -> str:
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def space_label(space: Space) -> str:
    dims = f'{_pretty_number(space.x)} × {_pretty_number(space.y)}'
    if space.z is not None:
        dims = f'{dims} × {_pretty_number(space.z)}'
    unit = space.get_dimension_unit_display()
    return f'{space.get_space_type_display()} {dims} {unit}'


def serialize_space(space: Space, *, object_count: int | None = None) -> dict:
    payload = {
        'id': space.id,
        'x': space.x,
        'y': space.y,
        'z': space.z,
        'dimension_unit': space.dimension_unit,
        'space_type': space.space_type,
        'weight_limit': space.weight_limit,
        'editing_grid_increment_inches': space.editing_grid_increment_inches,
        'label': space_label(space),
    }
    if object_count is not None:
        payload['object_count'] = object_count
    return payload


def serialize_object(obj: Object) -> dict:
    return {
        'id': obj.id,
        'x': obj.x,
        'y': obj.y,
        'z': obj.z,
        'weight': obj.weight,
        'object_type': obj.object_type,
        'material': obj.material,
        'style': obj.style,
        'price_limit': obj.price_limit,
        'similar_items': [
            {'id': item.id, 'name': item.name, 'image': item.image}
            for item in obj.similar_items.all()
        ],
        'space_ids': list(obj.spaces.order_by('id').values_list('id', flat=True)),
    }


def _validation_errors(exc: ValidationError) -> dict[str, list[str]]:
    if hasattr(exc, 'error_dict'):
        return {
            field: [str(item.message) for item in items]
            for field, items in exc.error_dict.items()
        }
    return {'non_field_errors': exc.messages}


def _read_decimal(payload, field, errors, *, required):
    if field not in payload:
        return None, False
    value = payload[field]
    if value is None or value == '':
        if required:
            errors.setdefault(field, []).append('Enter a number.')
            return None, False
        return None, True
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        errors.setdefault(field, []).append('Enter a number.')
        return None, False
    if not number.is_finite() or number < 0:
        errors.setdefault(field, []).append('Must be 0 or greater.')
        return None, False
    return number, True


def _too_wide(number: Decimal, max_digits: int, decimal_places: int) -> bool:
    text = format(number.quantize(Decimal(10) ** -decimal_places), 'f').lstrip('-')
    whole, _, _fraction = text.partition('.')
    return len(whole) + decimal_places > max_digits


def _read_choice(payload, field, errors):
    if field not in payload:
        return None, False
    value = payload[field]
    allowed = {choice for choice, _label in _CHOICES[field]}
    if value not in allowed:
        errors.setdefault(field, []).append('Select a valid choice.')
        return None, False
    return value, True


def _read_similar_items(payload, obj: Object, errors):
    if 'similar_items' not in payload:
        return None
    raw = payload['similar_items']
    if not isinstance(raw, list):
        errors['similar_items'] = ['Expected a list of similar items.']
        return None
    existing = set(obj.similar_items.values_list('id', flat=True))
    seen: set[int] = set()
    items = []
    validate_url = URLValidator()
    for index, item in enumerate(raw):
        prefix = f'similar_items[{index}]'
        if not isinstance(item, dict):
            errors.setdefault(prefix, []).append('Expected an object with name and image.')
            continue
        unknown = set(item) - {'id', 'name', 'image'}
        if unknown:
            errors.setdefault(prefix, []).append('Unknown similar item fields.')
        name = item.get('name', '')
        image = item.get('image', '')
        if not isinstance(name, str) or not name.strip():
            errors.setdefault(f'{prefix}.name', []).append('Enter a name.')
            name = ''
        elif len(name.strip()) > 255:
            errors.setdefault(f'{prefix}.name', []).append('Name must be 255 characters or fewer.')
        if not isinstance(image, str) or not image.strip():
            errors.setdefault(f'{prefix}.image', []).append('Enter an image URL.')
            image = ''
        else:
            cleaned = image.strip()
            if len(cleaned) > 2048:
                errors.setdefault(f'{prefix}.image', []).append(
                    'Image URL must be 2048 characters or fewer.'
                )
            try:
                validate_url(cleaned)
            except ValidationError:
                errors.setdefault(f'{prefix}.image', []).append('Enter a valid image URL.')
            image = cleaned
        item_id = item.get('id', None)
        if item_id is not None:
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                errors.setdefault(f'{prefix}.id', []).append('Enter a similar item id.')
                item_id = None
            else:
                if item_id not in existing:
                    errors.setdefault(f'{prefix}.id', []).append(
                        'Similar item not found on this object.'
                    )
                elif item_id in seen:
                    errors.setdefault(f'{prefix}.id', []).append('Duplicate similar item.')
                else:
                    seen.add(item_id)
        items.append(
            {
                'id': item_id,
                'name': name.strip() if isinstance(name, str) else '',
                'image': image.strip() if isinstance(image, str) else '',
            }
        )
    return items


def _read_space_ids(payload, errors):
    if 'space_ids' not in payload:
        return None
    raw = payload['space_ids']
    if not isinstance(raw, list):
        errors['space_ids'] = ['Expected a list of space ids.']
        return None
    ids = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            errors['space_ids'] = ['Expected integer space ids.']
            return None
    unique = list(dict.fromkeys(ids))
    found = set(Space.objects.filter(pk__in=unique).values_list('pk', flat=True))
    missing = [item for item in unique if item not in found]
    if missing:
        errors['space_ids'] = ['Space not found.']
        return None
    return unique


def _replace_similar_items(obj: Object, items: list[dict]) -> None:
    kept: list[int] = []
    for item in items:
        if item['id']:
            row = obj.similar_items.get(pk=item['id'])
            row.name = item['name']
            row.image = item['image']
            row.save(update_fields=['name', 'image'])
            kept.append(row.id)
        else:
            row = obj.similar_items.create(name=item['name'], image=item['image'])
            kept.append(row.id)
    if kept:
        obj.similar_items.exclude(pk__in=kept).delete()
    else:
        obj.similar_items.all().delete()


def update_object_from_payload(obj: Object, payload: dict) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    unknown = sorted(set(payload) - _OBJECT_FIELDS)
    if unknown:
        errors['non_field_errors'] = [f"Unknown fields: {', '.join(unknown)}."]

    editing_space = None
    if 'editing_space_id' in payload and payload['editing_space_id'] not in (None, ''):
        try:
            editing_space_id = int(payload['editing_space_id'])
        except (TypeError, ValueError):
            errors['editing_space_id'] = ['Enter a space id.']
        else:
            editing_space = Space.objects.filter(pk=editing_space_id).first()
            if editing_space is None:
                errors['editing_space_id'] = ['Space not found.']

    updates: dict = {}

    def maybe_snap(number: Decimal) -> Decimal:
        if editing_space is None or 'editing_space_id' in errors:
            return number.quantize(_DIMENSION_QUANT)
        return snap_to_editing_grid(number, editing_space)

    for field in ('x', 'y', 'weight'):
        number, present = _read_decimal(payload, field, errors, required=True)
        if not present or field in errors or number is None:
            continue
        if _too_wide(number, 12, 4):
            errors.setdefault(field, []).append('Enter a smaller number.')
            continue
        updates[field] = maybe_snap(number) if field in ('x', 'y') else number.quantize(_DIMENSION_QUANT)

    number, present = _read_decimal(payload, 'z', errors, required=False)
    if present and 'z' not in errors:
        if number is None:
            updates['z'] = None
        elif _too_wide(number, 12, 4):
            errors.setdefault('z', []).append('Enter a smaller number.')
        else:
            updates['z'] = maybe_snap(number)

    number, present = _read_decimal(payload, 'price_limit', errors, required=True)
    if present and 'price_limit' not in errors and number is not None:
        if _too_wide(number, 14, 2):
            errors.setdefault('price_limit', []).append('Enter a smaller number.')
        else:
            updates['price_limit'] = number.quantize(_PRICE_QUANT)

    for field in ('object_type', 'material', 'style'):
        value, present = _read_choice(payload, field, errors)
        if present and field not in errors:
            updates[field] = value

    similar_items = _read_similar_items(payload, obj, errors)
    space_ids = _read_space_ids(payload, errors)

    if errors:
        return errors

    with transaction.atomic():
        for field, value in updates.items():
            setattr(obj, field, value)
        try:
            obj.full_clean()
        except ValidationError as exc:
            obj.refresh_from_db()
            return _validation_errors(exc)
        obj.save()
        if space_ids is not None:
            obj.spaces.set(space_ids)
        if similar_items is not None:
            _replace_similar_items(obj, similar_items)
    return {}
