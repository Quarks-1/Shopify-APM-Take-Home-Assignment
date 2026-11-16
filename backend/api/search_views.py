"""Search-run endpoints: start, inspect, cancel, and select a candidate."""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import Candidate, RunStatus, SearchRun, SpaceObject, Verdict
from .searcher import runner
from .serializers import ValidationFailed, _is_number, object_to_json, run_to_json
from .state_views import _body, _error, _json_view, _not_found


def _include_all(request):
    return request.GET.get('include') == 'all'


def _get_object(object_id):
    return SpaceObject.objects.filter(pk=object_id).first()


def _fresh(run):
    """A polled run that claims to be running gets checked for a dead worker
    first (e.g. after a reboot), so it doesn't read 'running' forever."""
    if run.status == RunStatus.RUNNING:
        runner.reap_stale()
        run.refresh_from_db()
    return run


@_json_view
@require_http_methods(['POST'])
def object_searches(request, object_id):
    obj = _get_object(object_id)
    if obj is None:
        return _not_found()
    try:
        run, created = runner.start_search(obj)
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
    obj.save(update_fields=['selected_candidate'])
    return JsonResponse(object_to_json(obj))
