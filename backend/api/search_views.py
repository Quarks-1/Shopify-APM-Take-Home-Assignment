"""Search-run endpoints: start, inspect, cancel, and select a candidate."""
import logging

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .cart.stores import store_key, touch_stores
from .models import CartLine, Candidate, RunStatus, SearchRun, SpaceObject, Verdict
from .searcher import runner
from .serializers import ValidationFailed, _is_number, object_to_json, run_to_json
from .state_views import _body, _error, _json_view, _not_found

logger = logging.getLogger(__name__)


def _include_all(request):
    return request.GET.get('include') == 'all'


def _get_object(object_id):
    return SpaceObject.objects.filter(pk=object_id).first()


def _fresh(run):
    """A polled active run gets a dispatch pass first: dead workers are reaped
    (e.g. after a reboot) and queued runs promoted, so nothing stalls."""
    if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        try:
            runner.dispatch()
        except Exception:  # never fail the poll because of housekeeping
            logger.exception('dispatch failed while polling run %s', run.pk)
        run.refresh_from_db()
    return run


def _search_request(request, obj):
    """(mode, like candidate) of a start request: ('like', candidate) for
    `{"mode": "like", "candidateId": n}`, ('refine', None) for exactly
    `{"mode": "refine"}`, else ('search', None) for a plain search (no body,
    `{}` or `{"mode": "search"}`)."""
    if not request.body.strip() or 'json' not in (request.content_type or ''):
        return 'search', None  # the plain "search" call has no (JSON) body
    data, err = _body(request)
    if err:
        raise ValidationFailed({'$': 'invalid JSON'})
    if data is None:
        return 'search', None
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    errors = {k: 'unknown key' for k in data if k not in ('mode', 'candidateId')}
    mode = data.get('mode', 'search')
    if mode not in ('search', 'like', 'refine'):
        errors['mode'] = 'must be "search", "like" or "refine"'
    if errors:
        raise ValidationFailed(errors)
    if mode in ('search', 'refine'):
        if 'candidateId' in data:
            raise ValidationFailed({'candidateId': 'only allowed with mode "like"'})
        return mode, None
    cid = data.get('candidateId')
    if not _is_number(cid) or int(cid) != cid:
        raise ValidationFailed({'candidateId': 'is required and must be an integer'})
    cand = Candidate.objects.filter(pk=int(cid), run__object_id=obj.pk).first()
    if cand is None:
        raise ValidationFailed({'candidateId': 'not a candidate of this object'})
    if cand.verdict != Verdict.ACCEPTED:
        raise ValidationFailed({'candidateId': 'candidate was not accepted'})
    return 'like', cand


@_json_view
@require_http_methods(['POST'])
def object_searches(request, object_id):
    obj = _get_object(object_id)
    if obj is None:
        return _not_found()
    mode, like_candidate = _search_request(request, obj)
    try:
        if mode == 'like':
            run, created = runner.start_like_search(obj, like_candidate)
        elif mode == 'refine':
            run, created = runner.start_refine_search(obj)
        else:
            run, created = runner.start_search(obj)
    except runner.NoFinishedSearch as exc:
        return _error(400, 'no_finished_search', {'mode': str(exc)})
    except runner.SearchRefused as exc:
        return _error(400, 'imported_object', {'source': str(exc)})
    return JsonResponse(run_to_json(run), status=202 if created else 200)


@_json_view
@require_http_methods(['GET'])
def object_search_latest(request, object_id):
    if _get_object(object_id) is None:
        return _not_found()
    run = SearchRun.objects.filter(object_id=object_id).order_by('-created_at', '-pk').first()
    if run is None:
        return _not_found()
    return JsonResponse(run_to_json(_fresh(run), include_all=_include_all(request)))


@_json_view
@require_http_methods(['GET'])
def search_detail(request, run_id):
    run = SearchRun.objects.filter(pk=run_id).first()
    if run is None:
        return _not_found()
    return JsonResponse(run_to_json(_fresh(run), include_all=_include_all(request)))


@_json_view
@require_http_methods(['POST'])
def search_cancel(request, run_id):
    run = SearchRun.objects.filter(pk=run_id).first()
    if run is None:
        return _not_found()
    run, _ = runner.cancel_run(run)
    return JsonResponse(run_to_json(run))


@_json_view
@require_http_methods(['POST'])
def object_select(request, object_id):
    obj = _get_object(object_id)
    if obj is None:
        return _not_found()
    data, err = _body(request)
    if err:
        return err
    if not isinstance(data, dict):
        raise ValidationFailed({'$': 'must be an object'})
    errors = {k: 'unknown key' for k in data if k != 'candidateId'}
    if 'candidateId' not in data:
        errors['candidateId'] = 'is required'
    if errors:
        raise ValidationFailed(errors)
    cid = data['candidateId']
    old = obj.selected_candidate
    if cid is None:
        obj.selected_candidate = None
    else:
        if not _is_number(cid) or int(cid) != cid:
            raise ValidationFailed({'candidateId': 'must be an integer or null'})
        cand = Candidate.objects.filter(pk=int(cid), run__object_id=obj.pk).first()
        if cand is None:
            raise ValidationFailed({'candidateId': 'not a candidate of this object'})
        if cand.verdict != Verdict.ACCEPTED:
            raise ValidationFailed({'candidateId': 'candidate was not accepted'})
        obj.selected_candidate = cand
    with transaction.atomic():
        obj.save(update_fields=['selected_candidate'])
        # A cart line follows the pick; clearing the pick removes the line.
        line = CartLine.objects.filter(object=obj).first()
        if line is not None:
            new = obj.selected_candidate
            touch_stores([store_key(c) for c in (old, new) if c is not None])
            if new is None:
                line.delete()
    return JsonResponse(object_to_json(obj))
