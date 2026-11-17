"""Re-encode downloaded pin images as small JPEGs (Pillow)."""
import io

from PIL import Image

from api.imagine.photos import reencode_jpeg

FORMATS = ('JPEG', 'PNG', 'WEBP', 'GIF')
MAX_SIDE = 1024
QUALITY = 85


def normalize_jpeg(data: bytes) -> tuple[bytes, str]:
    """`(jpeg bytes, 'jpg')` for any JPEG/PNG/WEBP/GIF (first frame, at most
    1024px on a side). Raises ValueError for anything else, including images
    big enough to be decompression bombs."""
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        image.seek(0)
        image.load()
        return reencode_jpeg(image, quality=QUALITY, max_side=MAX_SIDE), 'jpg'
    except ValueError:
        raise
    except Exception as exc:  # DecompressionBombError, UnidentifiedImageError, OSError, ...
        raise ValueError(f'unusable image: {exc}') from exc
