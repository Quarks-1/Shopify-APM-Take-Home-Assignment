"""Cart endpoints: DB-only line edits, and per-store merchant sync/checkout."""
import re

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .cart.stores import current_lines, line_store, line_to_json, touch_stores
from .cart.sync import CheckoutUnavailable, cart_payload, checkout_store, sync_store
from .models import CartLine, SpaceObject
from .serializers import ValidationFailed, generate_id, now
from .state_views import _body, _error, _json_view, _not_found

LINE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}\Z')
MAX_QTY = 20
MAX_LINES = 200


def _qty(value, path, errors, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_QTY:
        errors[path] = f'must be an integer from {minimum} to {MAX_QTY}'
        return None
    return value


def _all_lines():
    return [line_to_json(*entry) for entry in current_lines()]


def _validate_lines(data):
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    errors = {k: 'unknown key' for k in data if k != 'lines'}
    raw = data.get('lines')
    if not isinstance(raw, list):
        errors['lines'] = 'must be an array'
        raise ValidationFailed(errors)
    if len(raw) > MAX_LINES:
        errors['lines'] = f'at most {MAX_LINES} lines'
    out = []
    for i, item in enumerate(raw[:MAX_LINES]):
        p = f'lines[{i}]'
        if not isinstance(item, dict):
            errors[p] = 'must be an object'
            continue
        for k in item:
            if k not in ('id', 'objectId', 'qty'):
                errors[f'{p}.{k}'] = 'unknown key'
        if 'id' in item and (not isinstance(item['id'], str) or not LINE_ID_RE.match(item['id'])):
            errors[f'{p}.id'] = 'must match [A-Za-z0-9_-]{1,64}'
        if not isinstance(item.get('objectId'), str) or not item['objectId']:
            errors[f'{p}.objectId'] = 'must be a string'
        qty = _qty(item['qty'], f'{p}.qty', errors, 1) if 'qty' in item else None
        out.append({'id': item.get('id'), 'object_id': item.get('objectId'), 'qty': qty})
    if errors:
        raise ValidationFailed(errors)
    return out


@_json_view
@require_http_methods(['GET', 'DELETE'])
def cart_root(request):
    if request.method == 'GET':
        return JsonResponse(cart_payload())
    with transaction.atomic():
        lines = list(CartLine.objects.select_related('object__selected_candidate'))
        dirty = touch_stores(line_store(line) for line in lines)
        CartLine.objects.filter(pk__in=[line.pk for line in lines]).delete()
    return JsonResponse({'dirtyStores': dirty})


@_json_view
@require_http_methods(['POST'])
def cart_lines(request):
    """Upsert lines by object (1:1). An existing line keeps its id, and its
    qty unless one is sent."""
    data, err = _body(request)
    if err:
        return err
    items = _validate_lines(data)
    skipped, touched = [], []
    with transaction.atomic():
        objects = SpaceObject.objects.select_related('selected_candidate').in_bulk(
            {i['object_id'] for i in items})
        existing = {line.object_id: line for line in
                    CartLine.objects.filter(object_id__in=list(objects))}
        taken = set(CartLine.objects.values_list('pk', flat=True))
        for item in items:
            obj = objects.get(item['object_id'])
            if obj is None:
                skipped.append({'objectId': item['object_id'], 'reason': 'not_found'})
                continue
            if obj.selected_candidate is None:
                skipped.append({'objectId': obj.pk, 'reason': 'no_selection'})
                continue
            line = existing.get(obj.pk)
            if line is None:
                lid = item['id'] if item['id'] and item['id'] not in taken else generate_id('line')
                while lid in taken:
                    lid = generate_id('line')
                line = CartLine.objects.create(
                    id=lid, object=obj, qty=item['qty'] or 1, created_at=now())
                existing[obj.pk] = line
                taken.add(lid)
            elif item['qty'] is not None and item['qty'] != line.qty:
                line.qty = item['qty']
                line.save(update_fields=['qty'])
            else:
                continue
            line.object = obj
            touched.append(line_store(line))
        dirty = touch_stores(touched)
    return JsonResponse({'lines': _all_lines(), 'skipped': skipped, 'dirtyStores': dirty})


@_json_view
@require_http_methods(['PATCH', 'DELETE'])
def cart_line_detail(request, line_id):
    line = CartLine.objects.select_related('object__selected_candidate').filter(pk=line_id).first()
    if line is None:
        return _not_found()
    qty = 0
    if request.method == 'PATCH':
        data, err = _body(request)
        if err:
            return err
        if not isinstance(data, dict):
            raise ValidationFailed({'$': 'must be an object'})
        errors = {k: 'unknown key' for k in data if k != 'qty'}
        if 'qty' not in data:
            errors['qty'] = 'is required'
        else:
            qty = _qty(data['qty'], 'qty', errors, 0)
        if errors:
            raise ValidationFailed(errors)
    store = line_store(line)
    dirty = []
    with transaction.atomic():
        if qty == 0:
            dirty = touch_stores([store])
            line.delete()
        elif qty != line.qty:
            dirty = touch_stores([store])
            line.qty = qty
            line.save(update_fields=['qty'])
    if request.method == 'DELETE':
        return JsonResponse({'dirtyStores': dirty})
    cand = line.object.selected_candidate
    body = line_to_json(line, cand, store) if qty and cand is not None else None
    return JsonResponse({'line': body, 'dirtyStores': dirty})


@_json_view
@require_http_methods(['POST'])
def store_sync(request, domain):
    data, err = _body(request)
    if err:
        return err
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    errors = {k: 'unknown key' for k in data if k != 'force'}
    if 'force' in data and not isinstance(data['force'], bool):
        errors['force'] = 'must be a boolean'
    if errors:
        raise ValidationFailed(errors)
    return JsonResponse(sync_store(domain, force=data.get('force', False)))


@_json_view
@require_http_methods(['POST'])
def store_checkout(request, domain):
    try:
        return JsonResponse(checkout_store(domain))
    except CheckoutUnavailable as exc:
        return JsonResponse(
            {'error': 'checkout_unavailable', 'details': {'reason': exc.reason}, 'store': exc.store},
            status=409,
        )
