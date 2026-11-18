from django.test import TestCase

from api.models import ProductVote, SpaceInspiration, VibeBoard, VibeImport
from api.test_fixtures.vibes_helpers import BOARD_URL, TempVibes, jpeg_bytes, make_board, make_space
from api.vibes import paths


class StateResetTests(TempVibes, TestCase):
    def test_get_not_allowed(self):
        self.assertEqual(self.client.get('/api/state/reset/').status_code, 405)

    def test_deletes_votes_including_null_object(self):
        ProductVote.objects.create(object=None, product_gid='gid://1', vote='up')
        ProductVote.objects.create(object=None, product_gid='gid://2', vote='down')
        res = self.client.post('/api/state/reset/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['reset']['votes'], 2)
        self.assertEqual(ProductVote.objects.count(), 0)

    def test_orphan_import_dir_removed_existing_kept(self):
        imp = VibeImport.objects.create(space=make_space(), board_url=BOARD_URL)
        kept = paths.import_dir(imp.id)
        orphan = paths.import_dir('00000000-0000-0000-0000-000000000000')
        for d in (kept, orphan):
            d.mkdir(parents=True)
            (d / 'worker.log').write_text('x')
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/state/reset/')
        self.assertEqual(res.json()['reset']['importDirs'], 1)
        self.assertTrue(kept.exists())
        self.assertFalse(orphan.exists())

    def test_unattached_boards_and_board_dirs_removed_after_spaces_deleted(self):
        s1 = make_space('s1')
        s2 = make_space('s2')
        gone_board = make_board(s1, board_url='https://pin.it/a', title='Gone')
        kept_board = make_board(s2, board_url='https://pin.it/b', title='Kept')
        SpaceInspiration.objects.create(board=gone_board, pin_id='p1', pin_url='u')
        SpaceInspiration.objects.create(board=kept_board, pin_id='p2', pin_url='u')
        VibeImport.objects.create(space=s1, board=gone_board, board_url='https://pin.it/a')
        for b in (gone_board, kept_board):
            d = paths.board_dir(b.id)
            d.mkdir(parents=True)
            (d / 'pin.jpg').write_bytes(jpeg_bytes())
        s1.delete()
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/state/reset/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(VibeBoard.objects.filter(pk=gone_board.pk).exists())
        self.assertTrue(VibeBoard.objects.filter(pk=kept_board.pk).exists())
        self.assertFalse(paths.board_dir(gone_board.id).exists())
        self.assertTrue(paths.board_dir(kept_board.id).exists())
