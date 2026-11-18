"""One-time cleanup: a space maps 1:1 to a Pinterest board.

Spaces imported before the importer pruned on every import can still hold pins
from several older boards. Keep only the pins of the newest import that has
pins, and delete the rest (rows first, then their image files, best effort).
"""
import logging

from django.db import migrations

from api.vibes import paths

log = logging.getLogger(__name__)


def prune(apps, schema_editor):
    Space = apps.get_model('api', 'Space')
    VibeImport = apps.get_model('api', 'VibeImport')
    Inspiration = apps.get_model('api', 'SpaceInspiration')

    removed_rows = 0
    removed_files = 0
    for space_id in Space.objects.values_list('id', flat=True):
        imports = VibeImport.objects.filter(space_id=space_id)
        if imports.filter(status__in=['queued', 'running']).exists():
            continue
        current = (
            imports.filter(inspirations__isnull=False)
            .order_by('-created_at').values_list('id', flat=True).first()
        )
        if current is None:
            continue
        stale = Inspiration.objects.filter(space_id=space_id).exclude(vibe_import_id=current)
        stale_ids = list(stale.values_list('id', flat=True))
        if not stale_ids:
            continue
        rel_paths = list(stale.values_list('image_path', flat=True))
        stale.delete()
        removed_rows += len(stale_ids)
        for rel in rel_paths:
            if not rel:
                continue
            target = paths.inside_vibes_dir(rel)
            if target is None:
                continue
            try:
                target.unlink()
                removed_files += 1
            except OSError:
                pass
    if removed_rows:
        log.info('pruned %d stale board pins and %d image files', removed_rows, removed_files)


class Migration(migrations.Migration):
    dependencies = [('api', '0014_inspiration_patterns')]
    operations = [migrations.RunPython(prune, migrations.RunPython.noop)]
