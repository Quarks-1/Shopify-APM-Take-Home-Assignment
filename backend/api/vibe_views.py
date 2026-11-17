"""Pinterest "vibes" endpoints: start/inspect/dismiss board imports and list
a space's inspiration pins."""
import logging

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import SpaceInspiration, Space, VibeImport, VibeImportStatus
from .serializers import inspiration_to_json, validate_vibe_import, vibe_import_to_json
from .state_views import _body, _error, _json_view, _not_found
from .vibes import importer

logger = logging.getLogger(__name__)


def _housekeeping():
    """Reap dead workers and promote queued imports before a read."""
    try:
        importer.dispatch()
    except Exception:  # never fail a poll because of housekeeping
        logger.exception('vibe import dispatch failed while polling')


def _import_json(imp):
    return {'import': vibe_import_to_json(imp)}


@_json_view
@require_http_methods(['GET', 'POST'])
def space_vibe_imports(request, space_id):
    space = Space.objects.filter(pk=space_id).first()
    if space is None:
        return _not_found()
    if request.method == 'GET':
        rows = VibeImport.objects.filter(space=space).order_by('-created_at', '-pk')
        return JsonResponse({'imports': [vibe_import_to_json(i) for i in rows]})
    data, err = _body(request)
    if err:
        return err
    url = validate_vibe_import(data)
    try:
        imp, created = importer.start_import(space, url)
    except importer.ImportInProgress as busy:
        body = {'error': 'import_in_progress', **_import_json(busy.active)}
        return JsonResponse(body, status=409)
    return JsonResponse(_import_json(imp), status=202 if created else 200)


@_json_view
@require_http_methods(['GET'])
def vibe_imports(request):
    """`?pending=1`: the newest non-dismissed import of each space."""
    _housekeeping()
    newest = {}
    for imp in VibeImport.objects.filter(dismissed=False).order_by('-created_at', '-pk'):
        newest.setdefault(imp.space_id, imp)
    imports = sorted(newest.values(), key=lambda i: i.created_at)
    return JsonResponse({'imports': [vibe_import_to_json(i) for i in imports]})


@_json_view
@require_http_methods(['GET'])
def vibe_import_detail(request, import_id):
    imp = VibeImport.objects.filter(pk=import_id).first()
    if imp is None:
        return _not_found()
    if imp.status in (VibeImportStatus.QUEUED, VibeImportStatus.RUNNING):
        _housekeeping()
        imp.refresh_from_db()
    return JsonResponse(_import_json(imp))


@_json_view
@require_http_methods(['POST'])
def vibe_import_dismiss(request, import_id):
    imp = VibeImport.objects.filter(pk=import_id).first()
    if imp is None:
        return _not_found()
    VibeImport.objects.filter(pk=imp.pk).update(dismissed=True)
    imp.refresh_from_db()
    return JsonResponse(_import_json(imp))


@_json_view
@require_http_methods(['GET'])
def space_inspirations(request, space_id):
    if not Space.objects.filter(pk=space_id).exists():
        return _not_found()
    rows = SpaceInspiration.objects.filter(space_id=space_id).order_by('board_rank', 'pk')
    return JsonResponse({'inspirations': [inspiration_to_json(r) for r in rows]})


@_json_view
@require_http_methods(['GET'])
def inspirations(request):
    """Every sorted pin across spaces, each pin_id once, in board order."""
    rows = SpaceInspiration.objects.filter(categorized_at__isnull=False).order_by('board_rank', 'pk')
    seen = set()
    out = []
    for row in rows:
        if row.pin_id in seen:
            continue
        seen.add(row.pin_id)
        out.append(inspiration_to_json(row))
    return JsonResponse({'inspirations': out})
