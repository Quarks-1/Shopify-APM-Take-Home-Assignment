import json

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Placement, RunStatus, Space, SpaceObject, default_search
from .searcher.runner import cancel_run
from .serializers import (
    ValidationFailed,
    generate_id,
    now,
    object_to_json,
    space_to_json,
    validate_import,
    validate_object,
    validate_placement,
    validate_space,
)


def _error(status, code, details=None):
    body = {'error': code}
    if details is not None:
        body['details'] = details
    return JsonResponse(body, status=status)


def _not_found():
    return _error(404, 'not_found')


def _body(request):
    try:
        return json.loads(request.body.decode('utf-8') or 'null'), None
    except (ValueError, UnicodeDecodeError):
        return None, _error(400, 'invalid_json')


def _next_position(model):
    top = model.objects.aggregate(m=Max('position'))['m']
    return 0 if top is None else top + 1


def _json_view(fn):
    """Wrap a view: CSRF exempt and translate ValidationFailed to 400."""

    def wrapper(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except ValidationFailed as exc:
            return _error(400, 'validation_failed', exc.details)

    wrapper.__name__ = fn.__name__
    return csrf_exempt(wrapper)


@require_http_methods(['GET'])
def state_snapshot(_request):
    return JsonResponse(
        {
            'spaces': [space_to_json(s) for s in Space.objects.prefetch_related('placements')],
            'objects': [object_to_json(o) for o in SpaceObject.objects.all()],
        }
    )


# ---------------------------------------------------------------- spaces

@_json_view
@require_http_methods(['GET', 'POST'])
def spaces_collection(request):
    if request.method == 'GET':
        return JsonResponse(
            [space_to_json(s) for s in Space.objects.prefetch_related('placements')], safe=False
        )
    data, err = _body(request)
    if err:
        return err
    fields = validate_space(data)
    sid = fields.pop('id', None) or generate_id('space')
    if Space.objects.filter(pk=sid).exists():
        return _error(409, 'conflict')
    ts = now()
    try:
        with transaction.atomic():
            space = Space.objects.create(
                id=sid, position=_next_position(Space), created_at=ts, updated_at=ts, **fields
            )
    except IntegrityError:
        return _error(409, 'conflict')
    return JsonResponse(space_to_json(space), status=201)


@_json_view
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def space_detail(request, space_id):
    try:
        space = Space.objects.get(pk=space_id)
    except Space.DoesNotExist:
        return _not_found()
    if request.method == 'GET':
        return JsonResponse(space_to_json(space))
    if request.method == 'DELETE':
        space.delete()
        return HttpResponse(status=204)
    data, err = _body(request)
    if err:
        return err
    fields = validate_space(data, partial=True)
    for k, v in fields.items():
        setattr(space, k, v)
    space.updated_at = now()
    space.save()
    return JsonResponse(space_to_json(space))


@_json_view
@require_http_methods(['PUT', 'DELETE'])
def placement_detail(request, space_id, object_id):
    try:
        space = Space.objects.get(pk=space_id)
        obj = SpaceObject.objects.get(pk=object_id)
    except (Space.DoesNotExist, SpaceObject.DoesNotExist):
        return _not_found()
    if request.method == 'DELETE':
        deleted, _ = Placement.objects.filter(space=space, object=obj).delete()
        if not deleted:
            return _not_found()
    else:
        data, err = _body(request)
        if err:
            return err
        fields = validate_placement(data)
        Placement.objects.update_or_create(space=space, object=obj, defaults=fields)
    space.updated_at = now()
    space.save(update_fields=['updated_at'])
    return JsonResponse(space_to_json(space))


# --------------------------------------------------------------- objects

@_json_view
@require_http_methods(['GET', 'POST'])
def objects_collection(request):
    if request.method == 'GET':
        return JsonResponse([object_to_json(o) for o in SpaceObject.objects.all()], safe=False)
    data, err = _body(request)
    if err:
        return err
    fields = validate_object(data)
    oid = fields.pop('id', None) or generate_id('obj')
    if SpaceObject.objects.filter(pk=oid).exists():
        return _error(409, 'conflict')
    fields.setdefault('created_at', now())
    try:
        with transaction.atomic():
            obj = SpaceObject.objects.create(id=oid, position=_next_position(SpaceObject), **fields)
    except IntegrityError:
        return _error(409, 'conflict')
    return JsonResponse(object_to_json(obj), status=201)


@_json_view
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def object_detail(request, object_id):
    try:
        obj = SpaceObject.objects.get(pk=object_id)
    except SpaceObject.DoesNotExist:
        return _not_found()
    if request.method == 'GET':
        return JsonResponse(object_to_json(obj))
    if request.method == 'DELETE':
        for run in obj.search_runs.filter(status__in=(RunStatus.QUEUED, RunStatus.RUNNING)):
            cancel_run(run)  # kills its worker and agent before the rows go away
        obj.delete()
        return HttpResponse(status=204)
    data, err = _body(request)
    if err:
        return err
    fields = validate_object(data, partial=True)
    for k, v in fields.items():
        setattr(obj, k, v)
    obj.save()
    return JsonResponse(object_to_json(obj))


# ---------------------------------------------------------------- import

@_json_view
@require_http_methods(['POST'])
def state_import(request):
    data, err = _body(request)
    if err:
        return err
    spaces, objects, ignored = validate_import(data)

    payload_ids = {o['id'] for o in objects}
    known = payload_ids | set(
        SpaceObject.objects.filter(
            pk__in={p['object_id'] for s in spaces for p in s['placements']}
        ).values_list('pk', flat=True)
    )
    errors = {}
    for i, s in enumerate(spaces):
        for j, p in enumerate(s['placements']):
            if p['object_id'] not in known:
                errors[f'spaces[{i}].placements[{j}].objectId'] = 'references unknown object'
    if errors:
        raise ValidationFailed(errors)

    n_placements = 0
    with transaction.atomic():
        for i, fields in enumerate(objects):
            oid = fields.pop('id')
            # Import is a full snapshot: absent optional fields reset to defaults.
            defaults = {
                'weight': None, 'material': None, 'style': None, 'price_limit': None,
                'owned': None, 'selected_product_id': None, 'similar_items': [],
                'candidate_ids': [], 'search': default_search(), 'source': 'search',
            }
            defaults.update(fields)
            SpaceObject.objects.update_or_create(
                id=oid, defaults={**defaults, 'position': i}
            )
        for i, fields in enumerate(spaces):
            sid = fields.pop('id')
            placements = fields.pop('placements')
            space, _ = Space.objects.update_or_create(
                id=sid, defaults={'weight_limit': None, **fields, 'position': i}
            )
            space.placements.all().delete()
            Placement.objects.bulk_create(
                Placement(space=space, object_id=p['object_id'], x=p['x'], y=p['y'])
                for p in placements
            )
            n_placements += len(placements)
    return JsonResponse(
        {
            'imported': {
                'spaces': len(spaces),
                'objects': len(objects),
                'placements': n_placements,
            },
            'ignored': ignored,
        }
    )
