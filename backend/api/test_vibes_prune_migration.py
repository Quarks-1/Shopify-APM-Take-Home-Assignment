from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from api.test_fixtures.vibes_helpers import TempVibes, jpeg_bytes
from api.vibes import paths

BEFORE = [('api', '0014_inspiration_patterns')]
AFTER = [('api', '0015_prune_stale_board_pins')]


class PruneStaleBoardPinsTests(TempVibes, TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        self.old_apps = executor.loader.project_state(BEFORE).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def make_space(self, sid='s1'):
        Space = self.old_apps.get_model('api', 'Space')
        now = timezone.now()
        return Space.objects.create(
            id=sid, name='Den', type='room', unit='in', dims={'x': 120, 'y': 96, 'z': 96},
            created_at=now, updated_at=now,
        )

    def make_import(self, space, age_days, status='done'):
        VibeImport = self.old_apps.get_model('api', 'VibeImport')
        imp = VibeImport.objects.create(space=space, board_url='https://pin.it/x', status=status)
        VibeImport.objects.filter(pk=imp.pk).update(
            created_at=timezone.now() - timedelta(days=age_days))
        return imp

    def add_pins(self, space, imp, prefix, n=2):
        Inspiration = self.old_apps.get_model('api', 'SpaceInspiration')
        rows = []
        for i in range(n):
            rel = f'{space.id}/{prefix}{i}.jpg'
            f = paths.vibes_dir() / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(jpeg_bytes())
            rows.append(Inspiration.objects.create(
                space=space, vibe_import=imp, pin_id=f'{prefix}{i}',
                pin_url='https://x/p', image_path=rel))
        return rows

    def run_prune(self):
        executor = MigrationExecutor(connection)
        executor.migrate(AFTER)
        return executor.loader.project_state(AFTER).apps.get_model('api', 'SpaceInspiration')

    def exists(self, row):
        return (paths.vibes_dir() / row.image_path).exists()

    def test_keeps_only_newest_import_with_rows(self):
        s = self.make_space('s1')
        old = self.add_pins(s, self.make_import(s, 3), 'a')
        mid = self.add_pins(s, self.make_import(s, 2), 'b')
        new = self.add_pins(s, self.make_import(s, 1), 'c')
        OldInsp = self.old_apps.get_model('api', 'SpaceInspiration')
        orphan = OldInsp.objects.create(space=s, pin_id='o', pin_url='u', image_path='')
        Insp = self.run_prune()
        self.assertEqual(
            set(Insp.objects.filter(space_id=s.id).values_list('pk', flat=True)),
            {r.pk for r in new})
        self.assertFalse(Insp.objects.filter(pk=orphan.pk).exists())
        for r in old + mid:
            self.assertFalse(self.exists(r))
        for r in new:
            self.assertTrue(self.exists(r))

    def test_newer_failed_import_holding_rows_is_current(self):
        s = self.make_space('s1')
        old = self.add_pins(s, self.make_import(s, 2), 'a')
        failed = self.add_pins(s, self.make_import(s, 1, 'failed'), 'b')
        self.make_import(s, 0)  # newest import, no rows
        Insp = self.run_prune()
        self.assertEqual(Insp.objects.filter(space_id=s.id).count(), len(failed))
        self.assertTrue(all(self.exists(r) for r in failed))
        self.assertFalse(any(self.exists(r) for r in old))

    def test_running_import_untouched(self):
        s = self.make_space('s1')
        self.add_pins(s, self.make_import(s, 3), 'a')
        self.add_pins(s, self.make_import(s, 2), 'b')
        self.make_import(s, 0, 'running')
        Insp = self.run_prune()
        self.assertEqual(Insp.objects.filter(space_id=s.id).count(), 4)

    def test_single_import_untouched(self):
        s = self.make_space('s1')
        rows = self.add_pins(s, self.make_import(s, 1), 'a', 3)
        Insp = self.run_prune()
        self.assertEqual(Insp.objects.filter(space_id=s.id).count(), 3)
        self.assertTrue(all(self.exists(r) for r in rows))

    def test_other_spaces_untouched(self):
        s1, s2 = self.make_space('s1'), self.make_space('s2')
        self.add_pins(s1, self.make_import(s1, 2), 'a')
        self.add_pins(s1, self.make_import(s1, 1), 'b')
        other = self.add_pins(s2, self.make_import(s2, 5), 'z')
        Insp = self.run_prune()
        self.assertEqual(Insp.objects.filter(space_id=s1.id).count(), 2)
        self.assertEqual(Insp.objects.filter(space_id=s2.id).count(), 2)
        self.assertTrue(all(self.exists(r) for r in other))
