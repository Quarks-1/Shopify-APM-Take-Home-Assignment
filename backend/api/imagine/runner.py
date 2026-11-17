import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from ..models import RenderStatus, SpaceRender
from . import layout, photos, prompt, providers

logger = logging.getLogger(__name__)

MAX_PHOTOS = 10


def mark_if_stale(render):
    """A running render older than the model timeout plus a minute was
    interrupted (the server restarted mid-run). Flip it to failed."""
    if render.status != RenderStatus.RUNNING:
        return render
    limit = timedelta(seconds=settings.IMAGINE_TIMEOUT + 60)
    if render.created_at and timezone.now() - render.created_at > limit:
        render.status = RenderStatus.FAILED
        render.error = 'interrupted'
        render.finished_at = timezone.now()
        render.save(update_fields=['status', 'error', 'finished_at'])
    return render


def _manifest(items, photo_numbers=frozenset()):
    return [
        {
            'number': i['number'],
            'objectId': i['objectId'],
            'name': i['name'],
            'productKey': i['productKey'],
            'productTitle': i['productTitle'],
            'hasPhoto': i['number'] in photo_numbers,
        }
        for i in items
    ]


def start_render(space, force=False):
    """Return (render, created). Reuses an identical running or done render
    unless force is set."""
    items = layout.render_items(space)
    fp = layout.fingerprint(space, items)

    if not force:
        candidates = (
            SpaceRender.objects.filter(
                space=space, fingerprint=fp,
                status__in=[RenderStatus.RUNNING, RenderStatus.DONE],
            )
            .defer('image')
            .order_by('-created_at', '-id')
        )
        for existing in candidates:
            mark_if_stale(existing)
            if existing.status in (RenderStatus.RUNNING, RenderStatus.DONE):
                return existing, False

    provider = settings.IMAGINE_PROVIDER
    render = SpaceRender.objects.create(
        space=space,
        fingerprint=fp,
        status=RenderStatus.RUNNING,
        provider=provider,
        model=settings.IMAGINE_MODEL if provider == 'gemini' else 'fake',
        items=_manifest(items),
    )
    render_id = render.id
    if settings.IMAGINE_RUN_INLINE:
        run_render(render_id)
        return SpaceRender.objects.defer('image').get(pk=render_id), True

    transaction.on_commit(
        lambda: threading.Thread(target=run_render, args=(render_id,), daemon=True).start()
    )
    return render, True


def _select_photo_items(items):
    """Items that will be sent as reference photos: those with an image URL,
    capped at MAX_PHOTOS by footprint area (largest first)."""
    with_urls = [i for i in items if i['imageUrl']]
    largest = sorted(with_urls, key=lambda i: i['w'] * i['h'], reverse=True)[:MAX_PHOTOS]
    keep = {i['number'] for i in largest}
    return [i for i in with_urls if i['number'] in keep]


def _run(render_id):
    render = SpaceRender.objects.select_related('space').get(pk=render_id)
    space = render.space
    items = layout.render_items(space)

    fetched = {}
    for item in _select_photo_items(items):
        result = photos.fetch_photo(item['imageUrl'])
        if result is not None:
            fetched[item['number']] = result

    render.items = _manifest(items, frozenset(fetched))
    render.save(update_fields=['items'])

    guide = layout.layout_guide_png(space, items)
    text, _index = prompt.build_prompt(space, items, set(fetched))
    aspect = layout.aspect_ratio(space)
    photo_list = [fetched[n] for n in sorted(fetched)]

    image, mime, provider_name, model_name = providers.generate(text, guide, photo_list, aspect)

    render.image = image
    render.image_mime = mime
    render.prompt = text
    render.provider = provider_name
    render.model = model_name
    render.status = RenderStatus.DONE
    render.error = ''
    render.finished_at = timezone.now()
    render.save()


def run_render(render_id):
    try:
        try:
            _run(render_id)
        except providers.ProviderNotConfigured:
            SpaceRender.objects.filter(pk=render_id).update(
                status=RenderStatus.FAILED,
                error='provider_not_configured',
                finished_at=timezone.now(),
            )
        except Exception as exc:  # noqa: BLE001 - record any failure on the row
            logger.exception('Render %s failed', render_id)
            SpaceRender.objects.filter(pk=render_id).update(
                status=RenderStatus.FAILED,
                error=str(exc)[:500],
                finished_at=timezone.now(),
            )
    finally:
        if not settings.IMAGINE_RUN_INLINE:
            connection.close()
