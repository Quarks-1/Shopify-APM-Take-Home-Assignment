"""Pinterest "vibes" endpoints: start/inspect/dismiss board imports, list
imported boards, and list/delete inspiration pins."""
from collections import defaultdict
import logging

from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from .models import Space, SpaceInspiration, VibeBoard, VibeImport, VibeImportStatus
from .serializers import (
    inspiration_to_json,
    validate_vibe_import,
    vibe_board_to_json,
    vibe_import_to_json,
)
from .state_views import _body, _json_view, _not_found
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
@require_http_methods(['GET', 'POST'])
def vibe_imports(request):
    """GET (`?pending=1`): newest non-dismissed import per target (each space + library).
    POST: start a standalone library import (`space=None`)."""
    if request.method == 'POST':
        data, err = _body(request)
        if err:
            return err
        url = validate_vibe_import(data)
        try:
            imp, created = importer.start_import(None, url)
        except importer.ImportInProgress as busy:
            body = {'error': 'import_in_progress', **_import_json(busy.active)}
            return JsonResponse(body, status=409)
        return JsonResponse(_import_json(imp), status=202 if created else 200)
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
def vibe_boards(_request):
    """Every imported board with at least one sorted pin, newest first."""
    boards = (
        VibeBoard.objects.annotate(
            categorized_count=Count(
                'inspirations', filter=Q(inspirations__categorized_at__isnull=False)
            )
        )
        .filter(categorized_count__gt=0)
        .order_by('-created_at', '-pk')
    )
    return JsonResponse(
        {'boards': [vibe_board_to_json(b, pin_count=b.categorized_count) for b in boards]}
    )


@_json_view
@require_http_methods(['GET'])
def space_inspirations(_request, space_id):
    space = Space.objects.filter(pk=space_id).only('pk', 'vibe_board_id').first()
    if space is None:
        return _not_found()
    if space.vibe_board_id is None:
        return JsonResponse({'inspirations': []})
    rows = SpaceInspiration.objects.filter(board_id=space.vibe_board_id).order_by('board_rank', 'pk')
    return JsonResponse(
        {'inspirations': [inspiration_to_json(r, space_id=space.pk) for r in rows]}
    )


@_json_view
@require_http_methods(['DELETE'])
def space_inspiration_detail(_request, space_id, inspiration_id):
    """Remove one imported pin (row + image file) from the space's chosen board."""
    space = Space.objects.filter(pk=space_id).first()
    if space is None or space.vibe_board_id is None:
        return _not_found()
    board_id = space.vibe_board_id
    row = SpaceInspiration.objects.filter(pk=inspiration_id, board_id=board_id).first()
    if row is None:
        return _not_found()
    if (
        importer.active_import(space) is not None
        or importer.active_import_for_board(board_id) is not None
    ):
        return JsonResponse({'error': 'import_in_progress'}, status=409)
    image_path = row.image_path
    import_id = row.vibe_import_id
    row.delete()
    if image_path and not SpaceInspiration.objects.filter(image_path=image_path).exists():
        importer.remove_files([image_path])
    usable = importer.usable_count(board_id)
    VibeBoard.objects.filter(pk=board_id).update(pins_usable=usable)
    if import_id is not None:
        VibeImport.objects.filter(pk=import_id).update(pins_usable=usable)
    return HttpResponse(status=204)


@_json_view
@require_http_methods(['GET'])
def inspirations(_request):
    """Sorted pins organized per imported board (`boards`), plus a flat de-duped
    `inspirations` list in board order."""
    boards = list(VibeBoard.objects.order_by('-created_at', '-pk'))
    rows = list(
        SpaceInspiration.objects.filter(categorized_at__isnull=False).order_by('board_rank', 'pk')
    )
    pins_by_board = defaultdict(list)
    for row in rows:
        pins_by_board[row.board_id].append(inspiration_to_json(row))

    boards_out = [
        vibe_board_to_json(board, inspirations=pins_by_board[board.pk])
        for board in boards
        if pins_by_board.get(board.pk)
    ]

    seen = set()
    flat_out = []
    for row in rows:
        if row.pin_id in seen:
            continue
        seen.add(row.pin_id)
        flat_out.append(inspiration_to_json(row))

    return JsonResponse({'boards': boards_out, 'inspirations': flat_out})
