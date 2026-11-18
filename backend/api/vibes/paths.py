"""On-disk layout for vibe imports. Nothing here creates directories."""
from pathlib import Path

from django.conf import settings

from api import serializers
from api.searcher import like_images

# Cached crop written next to a pin image: <stem>.crop.jpg.
CROP_SUFFIX = '.crop.jpg'


def vibes_dir() -> Path:
    configured = getattr(settings, 'VIBES_DIR', None)
    base = Path(configured) if configured else Path(settings.SEARCHER_VAR_DIR) / 'vibes'
    return base.resolve()


def space_dir(space_id: str) -> Path:
    if not isinstance(space_id, str) or not serializers.ID_RE.match(space_id):
        raise ValueError(f'invalid space id: {space_id!r}')
    return vibes_dir() / space_id


def imports_dir() -> Path:
    return vibes_dir() / 'imports'


def import_dir(import_id) -> Path:
    return imports_dir() / str(import_id)


def import_log_path(import_id) -> Path:
    return import_dir(import_id) / 'worker.log'


def inside_vibes_dir(rel) -> Path | None:
    """A stored relative path as an absolute path strictly inside vibes_dir
    (no existence or size checks), or None. For deleting files safely."""
    try:
        root = vibes_dir()
        target = (root / str(rel)).resolve()
        if root not in target.parents:
            return None
        return target
    except (OSError, ValueError):
        return None


def resolve_inside(rel) -> Path | None:
    """A stored relative path as an absolute file inside vibes_dir, or None."""
    try:
        root = vibes_dir()
        target = (root / str(rel)).resolve()
        if target != root and root not in target.parents:
            return None
        if not target.is_file():
            return None
        if target.stat().st_size > like_images.MAX_BYTES:
            return None
        return target
    except (OSError, ValueError):
        return None
