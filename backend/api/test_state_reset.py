from django.test import TestCase

from api.models import ProductVote, VibeImport
from api.test_fixtures.vibes_helpers import BOARD_URL, TempVibes, make_space
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
