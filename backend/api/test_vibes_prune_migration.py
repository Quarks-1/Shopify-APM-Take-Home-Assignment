import importlib
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from api.models import SpaceInspiration, VibeImport
from api.test_fixtures.vibes_helpers import TempVibes, jpeg_bytes, make_space
from api.vibes import paths

migration = importlib.import_module('api.migrations.0015_prune_stale_board_pins')


class PruneStaleBoardPinsTests(TempVibes, TestCase):
    def make_import(self, space, age_days, status='done'):
        imp = VibeImport.objects.create(space=space, board_url='https://pin.it/x', status=status)
        VibeImport.objects.filter(pk=imp.pk).update(
            created_at=timezone.now() - timedelta(days=age_days))
        return imp

    def add_pins(self, space, imp, prefix, n=2):
        rows = []
        for i in range(n):
            rel = f'{space.id}/{prefix}{i}.jpg'
            f = paths.vibes_dir() / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(jpeg_bytes())
            rows.append(SpaceInspiration.objects.create(
                space=space, vibe_import=imp, pin_id=f'{prefix}{i}',
                pin_url='https://x/p', image_path=rel))
        return rows

    def exists(self, row):
        return (paths.vibes_dir() / row.image_path).exists()

    def test_keeps_only_newest_import_with_rows(self):
        s = make_space('s1')
        old = self.add_pins(s, self.make_import(s, 3), 'a')
        mid = self.add_pins(s, self.make_import(s, 2), 'b')
        new = self.add_pins(s, self.make_import(s, 1), 'c')
        orphan = SpaceInspiration.objects.create(space=s, pin_id='o', pin_url='u', image_path='')
        migration.prune(apps, None)
        self.assertEqual(
            set(SpaceInspiration.objects.filter(space=s).values_list('pk', flat=True)),
            {r.pk for r in new})
        self.assertFalse(SpaceInspiration.objects.filter(pk=orphan.pk).exists())
        for r in old + mid:
            self.assertFalse(self.exists(r))
        for r in new:
            self.assertTrue(self.exists(r))

    def test_newer_failed_import_holding_rows_is_current(self):
        s = make_space('s1')
        old = self.add_pins(s, self.make_import(s, 2), 'a')
        failed = self.add_pins(s, self.make_import(s, 1, 'failed'), 'b')
        self.make_import(s, 0)  # newest import, no rows
        migration.prune(apps, None)
        self.assertEqual(SpaceInspiration.objects.filter(space=s).count(), len(failed))
        self.assertTrue(all(self.exists(r) for r in failed))
        self.assertFalse(any(self.exists(r) for r in old))

    def test_running_import_untouched(self):
        s = make_space('s1')
        self.add_pins(s, self.make_import(s, 3), 'a')
        self.add_pins(s, self.make_import(s, 2), 'b')
        self.make_import(s, 0, 'running')
        migration.prune(apps, None)
        self.assertEqual(SpaceInspiration.objects.filter(space=s).count(), 4)

    def test_single_import_untouched(self):
        s = make_space('s1')
        rows = self.add_pins(s, self.make_import(s, 1), 'a', 3)
        migration.prune(apps, None)
        self.assertEqual(SpaceInspiration.objects.filter(space=s).count(), 3)
        self.assertTrue(all(self.exists(r) for r in rows))

    def test_other_spaces_untouched(self):
        s1, s2 = make_space('s1'), make_space('s2')
        self.add_pins(s1, self.make_import(s1, 2), 'a')
        self.add_pins(s1, self.make_import(s1, 1), 'b')
        other = self.add_pins(s2, self.make_import(s2, 5), 'z')
        migration.prune(apps, None)
        self.assertEqual(SpaceInspiration.objects.filter(space=s1).count(), 2)
        self.assertEqual(SpaceInspiration.objects.filter(space=s2).count(), 2)
        self.assertTrue(all(self.exists(r) for r in other))
