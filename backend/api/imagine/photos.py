import io
import logging
import urllib.request
from urllib.parse import urlparse

from PIL import Image

logger = logging.getLogger(__name__)

MAX_BYTES = 15 * 1024 * 1024
TIMEOUT_S = 15
USER_AGENT = 'Mozilla/5.0 (compatible; layout-imagine/1.0)'


def reencode_jpeg(image, quality=88, max_side=1024):
    """A loaded PIL image as JPEG bytes: transparency flattened onto white,
    RGB, shrunk to fit max_side x max_side."""
    if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, 'white')
        background.paste(rgba, mask=rgba.getchannel('A'))
        image = background
    else:
        image = image.convert('RGB')
    image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=quality)
    return buf.getvalue()


def fetch_photo(url):
    """Download a product photo and re-encode it as a small JPEG.

    Returns (bytes, 'image/jpeg') or None on any failure."""
    try:
        if urlparse(url).scheme not in ('http', 'https'):
            return None
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            logger.warning('Photo too large, skipping: %s', url)
            return None
        image = Image.open(io.BytesIO(data))
        image.load()
        return reencode_jpeg(image, quality=88), 'image/jpeg'
    except Exception as exc:  # noqa: BLE001 - any failure just means no photo
        logger.warning('Could not fetch photo %s: %s', url, exc)
        return None
