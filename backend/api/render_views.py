from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from .imagine import layout
from .imagine.runner import mark_if_stale, start_render
from .models import RenderStatus, SpaceRender, Space
from .serializers import iso
from .state_views import _body, _error, _json_view, _not_found


def render_to_json(render):
    items = render.items or []
    return {
        'id': str(render.id),
        'spaceId': render.space_id,
        'status': render.status,
        'error': render.error or None,
        'fingerprint': render.fingerprint,
        'provider': render.provider,
        'model': render.model,
        'items': items,
        'itemCount': len(items),
        'photoCount': sum(1 for i in items if i.get('hasPhoto')),
        'imageUrl': (
            f'/api/renders/{render.id}/image/' if render.status == RenderStatus.DONE else None
        ),
        'createdAt': iso(render.created_at),
        'finishedAt': iso(render.finished_at) if render.finished_at else None,
    }


@_json_view
@require_http_methods(['POST'])
def space_renders(request, space_id):
    try:
        space = Space.objects.get(pk=space_id)
    except Space.DoesNotExist:
        return _not_found()

    data, err = _body(request)
    if err:
        return err
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error(400, 'validation_failed', {'body': 'must be an object'})
    force = data.get('force', False)
    if not isinstance(force, bool):
        return _error(400, 'validation_failed', {'force': 'must be a boolean'})

    if not space.placements.exists():
        return _error(400, 'no_items')
    if settings.IMAGINE_PROVIDER == 'gemini' and not settings.GEMINI_API_KEY:
        return _error(503, 'provider_not_configured')

    render, created = start_render(space, force=force)
    return JsonResponse(
        {'render': render_to_json(render), 'reused': not created},
        status=202 if created else 200,
    )


@require_http_methods(['GET'])
def space_render_latest(_request, space_id):
    try:
        space = Space.objects.get(pk=space_id)
    except Space.DoesNotExist:
        return _not_found()
    render = SpaceRender.objects.filter(space=space).defer('image').order_by(
        '-created_at', '-id'
    ).first()
    if render is None:
        return _not_found()
    mark_if_stale(render)
    current_fp = layout.fingerprint(space, layout.render_items(space))
    return JsonResponse({
        'render': render_to_json(render),
        'current': render.fingerprint == current_fp,
    })


@require_http_methods(['GET'])
def render_detail(_request, render_id):
    render = SpaceRender.objects.defer('image').filter(pk=render_id).first()
    if render is None:
        return _not_found()
    mark_if_stale(render)
    return JsonResponse({'render': render_to_json(render)})


@require_http_methods(['GET'])
def render_image(_request, render_id):
    render = SpaceRender.objects.only('status', 'image', 'image_mime').filter(pk=render_id).first()
    if render is None or render.status != RenderStatus.DONE or render.image is None:
        return _not_found()
    response = HttpResponse(bytes(render.image), content_type=render.image_mime or 'image/png')
    response['Cache-Control'] = 'private, max-age=31536000, immutable'
    return response
