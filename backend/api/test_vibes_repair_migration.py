from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from api.test_fixtures.vibes_helpers import TempVibes, jpeg_bytes
from api.vibes import paths

BEFORE = [('api', '0015_prune_stale_board_pins')]
AFTER = [('api', '0016_repair_vibe_counts_and_crops')]


class RepairTests(TempVibes, TransactionTestCase):
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

    def make_import(self, space, age_days, status='done', usable=0):
        VibeImport = self.old_apps.get_model('api', 'VibeImport')
        imp = VibeImport.objects.create(
            space=space, board_url='https://pin.it/x', status=status, pins_usable=usable)
        VibeImport.objects.filter(pk=imp.pk).update(
            created_at=timezone.now() - timedelta(days=age_days))
        return imp

    def pins(self, space, imp, n, categorized=True):
        Inspiration = self.old_apps.get_model('api', 'SpaceInspiration')
        tag = 'c' if categorized else 'u'
        for i in range(n):
            Inspiration.objects.create(
                space=space, vibe_import=imp, pin_id=f'{imp.pk}{tag}{i}', pin_url='u',
                categories=['x'] if categorized else [],
                categorized_at=timezone.now() if categorized else None)

    def run_repair(self):
        executor = MigrationExecutor(connection)
        executor.migrate(AFTER)
        return executor.loader.project_state(AFTER).apps

    def usable(self, after_apps, imp):
        return after_apps.get_model('api', 'VibeImport').objects.get(pk=imp.pk).pins_usable

    def test_fixes_current_import_only(self):
        s = self.make_space('s1')
        old = self.make_import(s, 3, usable=7)
        cur = self.make_import(s, 1, usable=170)
        self.pins(s, old, 1)
        self.pins(s, cur, 2)
        self.pins(s, cur, 3, categorized=False)
        self.old_apps.get_model('api', 'SpaceInspiration').objects.create(
            space=s, vibe_import=cur, pin_id='e', pin_url='u', categories=[],
            categorized_at=timezone.now())
        after_apps = self.run_repair()
        self.assertEqual(self.usable(after_apps, cur), 3)
        self.assertEqual(self.usable(after_apps, old), 7)

    def test_running_import_space_untouched(self):
        s = self.make_space('s1')
        imp = self.make_import(s, 2, usable=50)
        self.pins(s, imp, 2)
        self.make_import(s, 0, 'running')
        after_apps = self.run_repair()
        self.assertEqual(self.usable(after_apps, imp), 50)

    def test_orphan_crops(self):
        d = paths.vibes_dir() / 's1'
        d.mkdir(parents=True)
        (d / 'keep.jpg').write_bytes(jpeg_bytes())
        (d / 'keep.crop.jpg').write_bytes(jpeg_bytes())
        (d / 'orphan.crop.jpg').write_bytes(jpeg_bytes())
        imp = paths.imports_dir() / '1'
        imp.mkdir(parents=True)
        (imp / 'x.crop.jpg').write_bytes(jpeg_bytes())
        self.run_repair()
        self.assertTrue((d / 'keep.crop.jpg').exists())
        self.assertFalse((d / 'orphan.crop.jpg').exists())
        self.assertTrue((imp / 'x.crop.jpg').exists())
