"""Repair data left behind by pruning.

* A space's current import (newest with pins) can carry a stale `pins_usable`
  from before older-board rows were pruned; recompute it.
* Cached `<stem>.crop.jpg` files outlived their pin image; delete the orphans.
"""
import logging

from django.db import migrations

from api.vibes import paths

log = logging.getLogger(__name__)


def repair(apps, schema_editor):
    Space = apps.get_model('api', 'Space')
    VibeImport = apps.get_model('api', 'VibeImport')
    Inspiration = apps.get_model('api', 'SpaceInspiration')

    fixed = 0
    for space_id in Space.objects.values_list('id', flat=True):
        imports = VibeImport.objects.filter(space_id=space_id)
        if imports.filter(status__in=['queued', 'running']).exists():
            continue
        current = (
            imports.filter(inspirations__isnull=False)
            .order_by('-created_at').first()
        )
        if current is None:
            continue
        usable = sum(1 for cats in Inspiration.objects.filter(
            space_id=space_id, categorized_at__isnull=False).values_list('categories', flat=True)
            if cats)
        if current.pins_usable != usable:
            VibeImport.objects.filter(pk=current.pk).update(pins_usable=usable)
            fixed += 1

    removed = 0
    root = paths.vibes_dir()
    if root.is_dir():
        for space_folder in root.iterdir():
            if space_folder.name == 'imports' or not space_folder.is_dir():
                continue
            for crop in space_folder.glob(f'*{paths.CROP_SUFFIX}'):
                if root not in crop.resolve().parents:
                    continue
                base = crop.with_name(crop.name[:-len(paths.CROP_SUFFIX)] + '.jpg')
                if base.exists():
                    continue
                try:
                    crop.unlink()
                    removed += 1
                except OSError:
                    pass
    if fixed or removed:
        log.info('repaired %d vibe pin counts and removed %d orphan crop files', fixed, removed)


class Migration(migrations.Migration):
    dependencies = [('api', '0015_prune_stale_board_pins')]
    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
