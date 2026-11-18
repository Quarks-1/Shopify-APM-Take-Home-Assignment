from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from api.test_fixtures.vibes_helpers import BOARD_URL, TempVibes, jpeg_bytes
from api.vibes import paths

BEFORE = [('api', '0016_repair_vibe_counts_and_crops')]
AFTER = [('api', '0017_vibe_boards')]
OTHER_URL = 'https://www.pinterest.com/someuser/japandi/'


class VibeBoardsMigrationTests(TempVibes, TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        self.old_apps = executor.loader.project_state(BEFORE).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def make_space(self, sid):
        Space = self.old_apps.get_model('api', 'Space')
        now = timezone.now()
        return Space.objects.create(
            id=sid, name=f'Space {sid}', type='floor', unit='in',
            dims={'x': 120, 'y': 96}, created_at=now, updated_at=now,
        )

    def add_pin(self, space, imp, pin_id, categories=('furniture',), crop=False):
        Insp = self.old_apps.get_model('api', 'SpaceInspiration')
        rel = f'{space.id}/{pin_id}.jpg'
        f = paths.vibes_dir() / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(jpeg_bytes())
        if crop:
            (f.with_name(f'{pin_id}.crop.jpg')).write_bytes(jpeg_bytes())
        return Insp.objects.create(
            space=space, vibe_import=imp, pin_id=pin_id, pin_url=f'https://x/{pin_id}',
            image_path=rel, categories=list(categories), item='sofa',
            categorized_at=timezone.now(),
        )

    def test_backfills_dedupes_shared_boards_and_moves_files(self):
        VibeImport = self.old_apps.get_model('api', 'VibeImport')
        s1 = self.make_space('s1')
        s2 = self.make_space('s2')
        s3 = self.make_space('s3')

        imp1 = VibeImport.objects.create(
            space=s1, board_url='https://pin.it/abc', resolved_url=BOARD_URL,
            board_title='Cozy living', status='done',
        )
        imp2 = VibeImport.objects.create(
            space=s2, board_url=BOARD_URL, resolved_url=BOARD_URL,
            board_title='Cozy living', status='done',
        )
        imp3 = VibeImport.objects.create(
            space=s3, board_url=OTHER_URL, resolved_url=OTHER_URL,
            board_title='Japandi', status='done',
        )

        self.add_pin(s1, imp1, 'p1', crop=True)
        self.add_pin(s1, imp1, 'p2')
        self.add_pin(s2, imp2, 'p1')  # duplicate pin across s1 & s2 sharing BOARD_URL
        self.add_pin(s2, imp2, 'p3')
        self.add_pin(s3, imp3, 'j1', categories=[])  # categorized but empty categories -> not usable
        self.add_pin(s3, imp3, 'j2')

        executor = MigrationExecutor(connection)
        executor.migrate(AFTER)
        after_apps = executor.loader.project_state(AFTER).apps

        Space = after_apps.get_model('api', 'Space')
        VibeBoard = after_apps.get_model('api', 'VibeBoard')
        Insp = after_apps.get_model('api', 'SpaceInspiration')

        self.assertEqual(VibeBoard.objects.count(), 2)
        s1_after = Space.objects.get(pk='s1')
        s2_after = Space.objects.get(pk='s2')
        s3_after = Space.objects.get(pk='s3')
        self.assertEqual(s1_after.vibe_board_id, s2_after.vibe_board_id)
        self.assertNotEqual(s1_after.vibe_board_id, s3_after.vibe_board_id)

        shared_board = VibeBoard.objects.get(pk=s1_after.vibe_board_id)
        other_board = VibeBoard.objects.get(pk=s3_after.vibe_board_id)
        self.assertEqual(shared_board.title, 'Cozy living')
        self.assertEqual(shared_board.pins_usable, 3)
        self.assertEqual(other_board.pins_usable, 1)

        self.assertEqual(
            sorted(Insp.objects.filter(board_id=shared_board.pk).values_list('pin_id', flat=True)),
            ['p1', 'p2', 'p3'],
        )
        p1 = Insp.objects.get(board_id=shared_board.pk, pin_id='p1')
        self.assertEqual(p1.image_path, f'boards/{shared_board.pk}/p1.jpg')
        self.assertTrue((paths.vibes_dir() / p1.image_path).is_file())
        self.assertTrue((paths.board_dir(shared_board.pk) / 'p1.crop.jpg').is_file())
        self.assertFalse((paths.vibes_dir() / 's1').exists())
        self.assertFalse((paths.vibes_dir() / 's2').exists())
