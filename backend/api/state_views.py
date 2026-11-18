import json
import shutil

from collections import defaultdict

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, Max, OuterRef, Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .cart.stores import line_store, touch_stores
from .cart.sync import cart_payload
from .models import (
    CartLine,
    Candidate,
    Placement,
    ProductVote,
    RunStatus,
    SearchRun,
    Space,
    SpaceObject,
    Verdict,
    VibeImport,
    default_search,
)
from .searcher.runner import cancel_run
from .vibes import importer
from .vibes import paths as vibe_paths
from .serializers import (
    ValidationFailed,
    sort_accepted_brand_diverse,
    theme_from_vibes,
    candidate_to_json,
    generate_id,
    now,
    object_to_json,
    preferences_by_object,
    votes_by_object,
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


def state_candidates(objects):
    """Per object, in object order: the accepted candidates of its newest run
    that has any (ordered like run JSON), then its selected candidate when that
    came from another run. Two queries however many objects or runs exist."""
    has_accepted = Candidate.objects.filter(run=OuterRef('pk'), verdict=Verdict.ACCEPTED)
    newest = {}
    themes = {}
    for object_id, run_id, vibes in (
        SearchRun.objects.filter(Exists(has_accepted))
        .order_by('-created_at', '-pk')
        .values_list('object_id', 'pk', 'vibes')
    ):
        if object_id not in newest:
            newest[object_id] = run_id
            themes[object_id] = theme_from_vibes(vibes)
    selected = {o.selected_candidate_id for o in objects if o.selected_candidate_id is not None}

    by_object = defaultdict(list)
    for c in Candidate.objects.filter(
        Q(run_id__in=list(newest.values()), verdict=Verdict.ACCEPTED) | Q(pk__in=selected)
    ).select_related('query').annotate(owner_id=F('run__object_id')):
        by_object[c.owner_id].append(c)

    out = []
    for obj in objects:
        mine = by_object.get(obj.pk, [])
        run_id = newest.get(obj.pk)
        accepted = sort_accepted_brand_diverse(
            [c for c in mine if c.run_id == run_id and c.verdict == Verdict.ACCEPTED],
            themes.get(obj.pk),
        )
        ids = {c.pk for c in accepted}
        extra = [c for c in mine if c.pk == obj.selected_candidate_id and c.pk not in ids]
        out.extend(candidate_to_json(c, obj.pk) for c in accepted + extra)
    return out


def _objects_json(objects):
    votes = votes_by_object(o.pk for o in objects)
    prefs = preferences_by_object(o.pk for o in objects)
    return [object_to_json(o, votes.get(o.pk, {}), prefs.get(o.pk)) for o in objects]


@require_http_methods(['GET'])
def state_snapshot(_request):
    objects = list(SpaceObject.objects.all())
    return JsonResponse(
        {
            'spaces': [space_to_json(s) for s in Space.objects.prefetch_related('placements')],
            'objects': _objects_json(objects),
            'candidates': state_candidates(objects),
            'cart': cart_payload(),
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
        importer.cancel_for_space(space_id)
        space.delete()
        transaction.on_commit(
            lambda: shutil.rmtree(vibe_paths.space_dir(space_id), ignore_errors=True))
        return HttpResponse(status=204)
    data, err = _body(request)
    if err:
        return err
    fields = validate_space(data, partial=True)
    for k, v in fields.items():
        setattr(space, k, v)
    space.updated_at = now()
    space.save(update_fields=[*fields, 'updated_at'])
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
        return JsonResponse(_objects_json(list(SpaceObject.objects.all())), safe=False)
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
        with transaction.atomic():
            line = CartLine.objects.select_related('object__selected_candidate').filter(
                object=obj).first()
            if line is not None:
                touch_stores([line_store(line)])  # its cart line goes with the object
            obj.delete()
        return HttpResponse(status=204)
    data, err = _body(request)
    if err:
        return err
    fields = validate_object(
        data, partial=True,
        current={'price_min': obj.price_min, 'price_limit': obj.price_limit,
                 'price_min_fuzzy': obj.price_min_fuzzy,
                 'price_limit_fuzzy': obj.price_limit_fuzzy})
    for k, v in fields.items():
        setattr(obj, k, v)
    if fields:
        obj.save(update_fields=list(fields))
    return JsonResponse(object_to_json(obj))


# ---------------------------------------------------------------- reset

@_json_view
@require_http_methods(['POST'])
def state_reset(request):
    """Clear what outlives spaces and objects: product votes (they steer searches
    globally) and vibe import work folders no import row owns any more."""
    with transaction.atomic():
        votes, _ = ProductVote.objects.all().delete()
        root = vibe_paths.imports_dir()
        known = {str(i) for i in VibeImport.objects.values_list('pk', flat=True)}
        stale = []
        if root.is_dir():
            stale = [d for d in root.iterdir() if d.name not in known]

        def purge():
            for d in stale:
                shutil.rmtree(d, ignore_errors=True)

        transaction.on_commit(purge)
    return JsonResponse({'reset': {'votes': votes, 'importDirs': len(stale)}})


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
                'description': '', 'weight': None, 'material': None, 'style': None,
                'price_min': None, 'price_limit': None, 'price_min_fuzzy': False,
                'price_limit_fuzzy': False, 'min_rating': None,
                'owned': None, 'selected_product_id': None, 'similar_items': [],
                'reference_pins': [], 'candidate_ids': [], 'search': default_search(),
                'source': 'search',
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
