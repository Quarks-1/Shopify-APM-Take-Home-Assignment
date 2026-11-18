import importlib
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from api.models import SpaceInspiration, VibeImport
from api.test_fixtures.vibes_helpers import TempVibes, jpeg_bytes, make_space
from api.vibes import paths

migration = importlib.import_module('api.migrations.0016_repair_vibe_counts_and_crops')


class RepairTests(TempVibes, TestCase):
    def make_import(self, space, age_days, status='done', usable=0):
        imp = VibeImport.objects.create(
            space=space, board_url='https://pin.it/x', status=status, pins_usable=usable)
        VibeImport.objects.filter(pk=imp.pk).update(
            created_at=timezone.now() - timedelta(days=age_days))
        return imp

    def pins(self, space, imp, n, categorized=True):
        tag = 'c' if categorized else 'u'
        for i in range(n):
            SpaceInspiration.objects.create(
                space=space, vibe_import=imp, pin_id=f'{imp.pk}{tag}{i}', pin_url='u',
                categories=['x'] if categorized else [],
                categorized_at=timezone.now() if categorized else None)

    def usable(self, imp):
        return VibeImport.objects.get(pk=imp.pk).pins_usable

    def test_fixes_current_import_only(self):
        s = make_space('s1')
        old = self.make_import(s, 3, usable=7)
        cur = self.make_import(s, 1, usable=170)
        self.pins(s, old, 1)
        self.pins(s, cur, 2)
        self.pins(s, cur, 3, categorized=False)
        SpaceInspiration.objects.create(
            space=s, vibe_import=cur, pin_id='e', pin_url='u', categories=[],
            categorized_at=timezone.now())
        migration.repair(apps, None)
        self.assertEqual(self.usable(cur), 3)  # 2 + the one from `old` row kept for the space
        self.assertEqual(self.usable(old), 7)

    def test_running_import_space_untouched(self):
        s = make_space('s1')
        imp = self.make_import(s, 2, usable=50)
        self.pins(s, imp, 2)
        self.make_import(s, 0, 'running')
        migration.repair(apps, None)
        self.assertEqual(self.usable(imp), 50)

    def test_orphan_crops(self):
        d = paths.vibes_dir() / 's1'
        d.mkdir(parents=True)
        (d / 'keep.jpg').write_bytes(jpeg_bytes())
        (d / 'keep.crop.jpg').write_bytes(jpeg_bytes())
        (d / 'orphan.crop.jpg').write_bytes(jpeg_bytes())
        imp = paths.imports_dir() / '1'
        imp.mkdir(parents=True)
        (imp / 'x.crop.jpg').write_bytes(jpeg_bytes())
        migration.repair(apps, None)
        self.assertTrue((d / 'keep.crop.jpg').exists())
        self.assertFalse((d / 'orphan.crop.jpg').exists())
        self.assertTrue((imp / 'x.crop.jpg').exists())
