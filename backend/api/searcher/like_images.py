"""Photos of a "more like this" reference product.

The catalog's `like` search takes one image per round, so a like run caches
as many photos of the reference product as it can find. Everything here is
defensive: image URLs come from merchant data, so only https URLs on an
allowlisted host are fetched, redirects are re-checked, size is capped and
the file type is decided by its magic bytes, never by headers or extension.
"""
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings

TIMEOUT_SECONDS = 10
MAX_BYTES = 4 * 1024 * 1024
IMAGE_WIDTH = 768
MAX_REDIRECTS = 3
VIDEO_EXTS = ('.mp4', '.mov', '.webm', '.m3u8', '.glb', '.usdz')


class ImageError(Exception):
    pass


def _clean_url(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value.lower().startswith('https://') or len(value) > 2000:
        return ''
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        return ''
    return value


def _dedupe_key(url):
    parts = urlsplit(url)
    return f'{parts.netloc.lower()}{parts.path}'


def _media_urls(media):
    """URLs of the image items of one `media` list, in order."""
    for item in media or []:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        if kind is not None and kind != 'image':
            continue
        url = _clean_url(item.get('url'))
        if url and not urlsplit(url).path.lower().endswith(VIDEO_EXTS):
            yield url


def image_urls(product, cap=None):
    """Distinct image URLs of a get_product `product`: product media first,
    then each variant's media. De-duplicated ignoring the query string
    (`?v=...` / `?width=...` variants of one file), order kept, capped."""
    cap = settings.SEARCHER_LIKE_MAX_IMAGES if cap is None else cap
    product = product if isinstance(product, dict) else {}
    lists = [product.get('media')]
    lists += [v.get('media') for v in product.get('variants') or [] if isinstance(v, dict)]
    seen, out = set(), []
    for media in lists:
        for url in _media_urls(media):
            key = _dedupe_key(url)
            if key in seen:
                continue
            seen.add(key)
            out.append(url)
            if len(out) >= cap:
                return out
    return out


def allowed_url(url, hosts=None):
    """The URL, or ImageError unless it is https on an allowlisted host."""
    hosts = settings.SEARCHER_LIKE_IMAGE_HOSTS if hosts is None else hosts
    url = _clean_url(url)
    if not url:
        raise ImageError('not an https URL')
    host = (urlsplit(url).hostname or '').lower()
    if host not in {h.lower() for h in hosts}:
        raise ImageError(f'host not allowed: {host}')
    return url


def sized_url(url, width=IMAGE_WIDTH):
    """Ask the Shopify CDN for a smaller rendition (adds/replaces width=)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'width']
    query.append(('width', str(width)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def sniff(data):
    """(content_type, extension) from magic bytes, or None."""
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg', 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png', 'png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif', 'gif'
    return None


class _AllowlistRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect may only lead to another allowlisted https URL."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            allowed_url(newurl)
        except ImageError as exc:
            raise urllib.error.URLError(f'redirect refused: {exc}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(url):
    """GET the bytes of an already-validated URL (at most MAX_BYTES)."""
    opener = urllib.request.build_opener(_AllowlistRedirects)
    request = urllib.request.Request(url, headers={'User-Agent': 'shopify-planner/1.0',
                                                   'Accept': 'image/*'})
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as resp:
            data = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ImageError(f'download failed: {exc}')
    if len(data) > MAX_BYTES:
        raise ImageError('image larger than 4 MB')
    return data


def fetch_image(url):
    """Download one photo. Returns (bytes, content_type, extension) or raises
    ImageError."""
    data = _read(sized_url(allowed_url(url)))
    kind = sniff(data)
    if kind is None:
        raise ImageError('not a jpeg, png, webp or gif')
    return data, kind[0], kind[1]


def cache_images(urls, directory, log=None):
    """Download `urls` into `directory` as <n>.<ext>; failures are skipped.
    Returns [{index, path, contentType, bytes}] with consecutive indexes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    log = log or (lambda message: None)
    images = []
    for url in urls:
        try:
            data, content_type, ext = fetch_image(url)
        except ImageError as exc:
            log(f'like: skipped image {_dedupe_key(url)}: {exc}')
            continue
        index = len(images)
        path = directory / f'{index}.{ext}'
        try:
            path.write_bytes(data)
        except OSError as exc:
            log(f'like: could not cache image {index}: {exc}')
            continue
        images.append({'index': index, 'path': str(path), 'contentType': content_type,
                       'bytes': len(data)})
    return images
