"""Vibe import HTTP API. No network, no workers: `_spawn_worker` is patched."""
import json
import signal
import subprocess
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from .models import SpaceInspiration, VibeImport, VibeImportStatus
from .test_fixtures.vibes_helpers import TempVibes, jpeg_bytes, make_space
from .vibes import importer, paths

URL = '/api/spaces/space1/vibe-imports/'
BOARD = 'https://www.pinterest.com/someuser/cozy-living/'
OTHER = 'https://www.pinterest.com/someuser/other-board/'


def dead_pid():
    proc = subprocess.Popen(['true'])
    proc.wait()
    return proc.pid


class VibeApiBase(TempVibes, TestCase):
    def setUp(self):
        super().setUp()
        self.space = make_space()

    def post(self, body, url=URL, raw=None):
        data = raw if raw is not None else json.dumps(body)
        return self.client.post(url, data=data, content_type='application/json')

    def start(self, board=BOARD, space_id='space1'):
        return self.post({'boardUrl': board}, f'/api/spaces/{space_id}/vibe-imports/')


class StartTests(VibeApiBase):
    def test_bad_urls_are_400_with_a_board_url_message(self):
        for bad in ('not a board', '', 'https://pinterest.com.evil.net/u/b/',
                    'https://evilpinterest.com/u/b/', 'http://www.pinterest.com/u/b/',
                    'https://www.pinterest.com/pin/123/', 'https://example.com/u/b/'):
            res = self.post({'boardUrl': bad})
            self.assertEqual(res.status_code, 400, bad)
            body = res.json()
            self.assertEqual(body['error'], 'validation_failed')
            self.assertEqual(list(body['details']), ['boardUrl'])
            self.assertTrue(body['details']['boardUrl'])
        self.assertEqual(VibeImport.objects.count(), 0)
        self.assertEqual(self.spawn.call_count, 0)

    def test_pin_link_message(self):
        res = self.post({'boardUrl': 'https://www.pinterest.com/pin/123/'})
        self.assertEqual(res.json()['details']['boardUrl'],
                         "That's a link to a single pin. Paste a board link instead.")

    def test_body_shape_errors(self):
        for body in ({}, {'boardUrl': 5}, {'boardUrl': BOARD, 'extra': 1}, [], None):
            res = self.post(body)
            self.assertEqual(res.status_code, 400, body)
            self.assertEqual(res.json()['error'], 'validation_failed')
        self.assertEqual(self.post(None, raw='{nope').json(), {'error': 'invalid_json'})

    def test_unknown_space_is_404(self):
        res = self.start(space_id='nope')
        self.assertEqual((res.status_code, res.json()), (404, {'error': 'not_found'}))

    def test_new_import_is_202_and_spawns_one_worker(self):
        res = self.start('pin.it/ABC123')
        self.assertEqual(res.status_code, 202)
        imp = res.json()['import']
        self.assertEqual(set(imp), {
            'id', 'spaceId', 'boardUrl', 'boardTitle', 'status', 'step', 'pins', 'batches', 'error',
            'errorCode', 'dismissed', 'createdAt', 'startedAt', 'finishedAt'})
        self.assertEqual(set(imp['pins']), {
            'onBoard', 'found', 'new', 'skipped', 'downloaded', 'failed', 'categorized', 'usable'})
        self.assertEqual(set(imp['batches']), {'total', 'done', 'failed'})
        self.assertEqual((imp['spaceId'], imp['boardUrl']), ('space1', 'https://pin.it/ABC123'))
        self.assertEqual((imp['status'], imp['step']), ('running', 'reading-board'))
        self.assertEqual((imp['error'], imp['errorCode'], imp['dismissed']), (None, None, False))
        self.assertTrue(imp['createdAt'].endswith('Z'))
        self.assertTrue(imp['startedAt'].endswith('Z'))
        self.assertIsNone(imp['finishedAt'])
        self.assertIsNone(imp['pins']['onBoard'])
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.spawn.call_args.args, (VibeImport.objects.get().pk,))

    def test_same_url_again_is_200_and_no_second_import(self):
        first = self.start().json()['import']
        res = self.start(BOARD + '?utm=1')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['import']['id'], first['id'])
        self.assertEqual(VibeImport.objects.count(), 1)
        self.assertEqual(self.spawn.call_count, 1)

    def test_different_url_while_active_is_409(self):
        first = self.start().json()['import']
        res = self.start(OTHER)
        self.assertEqual(res.status_code, 409)
        body = res.json()
        self.assertEqual(body['error'], 'import_in_progress')
        self.assertEqual(body['import']['id'], first['id'])
        self.assertEqual(VibeImport.objects.count(), 1)

    def test_new_import_dismisses_earlier_finished_ones(self):
        old = VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.DONE)
        res = self.start(OTHER)
        self.assertEqual(res.status_code, 202)
        old.refresh_from_db()
        self.assertTrue(old.dismissed)

    def test_pin_counts_new_is_found_minus_skipped(self):
        VibeImport.objects.create(space=self.space, board_url=BOARD, pins_found=40, pins_skipped=15,
                                  status=VibeImportStatus.DONE)
        body = self.client.get('/api/vibe-imports/?pending=1').json()
        self.assertEqual(body['imports'][0]['pins']['new'], 25)


class PollTests(VibeApiBase):
    def test_pending_lists_newest_non_dismissed_per_space(self):
        other = make_space('space2')
        VibeImport.objects.create(space=self.space, board_url=BOARD, status=VibeImportStatus.DONE)
        newest = VibeImport.objects.create(space=self.space, board_url=OTHER, status=VibeImportStatus.DONE)
        dismissed = VibeImport.objects.create(
            space=other, board_url=BOARD, status=VibeImportStatus.DONE, dismissed=True)
        res = self.client.get('/api/vibe-imports/?pending=1')
        self.assertEqual(res.status_code, 200)
        ids = [i['id'] for i in res.json()['imports']]
        self.assertEqual(ids, [str(newest.pk)])
        self.assertNotIn(str(dismissed.pk), ids)

    def test_detail_and_404(self):
        imp = VibeImport.objects.create(space=self.space, board_url=BOARD, status=VibeImportStatus.DONE,
                                        board_title='Cozy', error='')
        res = self.client.get(f'/api/vibe-imports/{imp.pk}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['import']['boardTitle'], 'Cozy')
        res = self.client.get('/api/vibe-imports/00000000-0000-0000-0000-000000000000/')
        self.assertEqual(res.status_code, 404)

    def test_dismiss(self):
        imp = VibeImport.objects.create(space=self.space, board_url=BOARD, status=VibeImportStatus.DONE)
        res = self.client.post(f'/api/vibe-imports/{imp.pk}/dismiss/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['import']['dismissed'])
        imp.refresh_from_db()
        self.assertTrue(imp.dismissed)
        self.assertEqual(self.client.get('/api/vibe-imports/?pending=1').json(), {'imports': []})
        res = self.client.post('/api/vibe-imports/00000000-0000-0000-0000-000000000000/dismiss/')
        self.assertEqual(res.status_code, 404)

    def test_failed_import_reports_error_fields(self):
        imp = VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.FAILED,
            error='This board is private. Make it public on Pinterest and try again.',
            error_code='private_board', finished_at=timezone.now())
        data = self.client.get(f'/api/vibe-imports/{imp.pk}/').json()['import']
        self.assertEqual(data['errorCode'], 'private_board')
        self.assertTrue(data['error'].startswith('This board is private'))
        self.assertTrue(data['finishedAt'].endswith('Z'))

    def test_dead_worker_is_reaped_on_poll(self):
        imp = VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.RUNNING, step='downloading',
            worker_pid=dead_pid(), started_at=timezone.now())
        data = self.client.get(f'/api/vibe-imports/{imp.pk}/').json()['import']
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['errorCode'], 'worker_died')
        self.assertEqual(data['error'], 'The import stopped unexpectedly. Try again.')
        self.assertIsNotNone(data['finishedAt'])

    def test_dead_worker_is_reaped_when_listing_pending(self):
        VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.RUNNING,
            worker_pid=dead_pid(), started_at=timezone.now())
        imports = self.client.get('/api/vibe-imports/?pending=1').json()['imports']
        self.assertEqual(imports[0]['errorCode'], 'worker_died')

    def test_live_worker_is_left_alone(self):
        import os
        imp = VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.RUNNING,
            worker_pid=os.getpid(), started_at=timezone.now())
        data = self.client.get(f'/api/vibe-imports/{imp.pk}/').json()['import']
        self.assertEqual(data['status'], 'running')

    def test_running_without_pid_is_reaped_only_after_the_grace_period(self):
        fresh = VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.RUNNING,
            started_at=timezone.now())
        self.assertEqual(importer.reap_stale(), [])
        VibeImport.objects.filter(pk=fresh.pk).update(
            started_at=timezone.now() - timezone.timedelta(seconds=120))
        self.assertEqual(importer.reap_stale(), [fresh.pk])

    def test_dispatch_cap_is_one(self):
        make_space('space2')
        first = self.start().json()['import']
        second = self.start(OTHER, 'space2').json()['import']
        self.assertEqual((first['status'], second['status']), ('running', 'queued'))
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.client.get(f'/api/vibe-imports/{second["id"]}/').json()['import']['status'],
                         'queued')
        VibeImport.objects.filter(pk=first['id']).update(status=VibeImportStatus.DONE)
        data = self.client.get(f'/api/vibe-imports/{second["id"]}/').json()['import']
        self.assertEqual(data['status'], 'running')
        self.assertEqual(self.spawn.call_count, 2)


class SpaceImportListTests(VibeApiBase):
    def test_get_lists_the_spaces_imports_newest_first(self):
        other = make_space('space2')
        old = VibeImport.objects.create(space=self.space, board_url=BOARD, status=VibeImportStatus.DONE)
        new = VibeImport.objects.create(
            space=self.space, board_url=OTHER, status=VibeImportStatus.FAILED, dismissed=True)
        foreign = VibeImport.objects.create(space=other, board_url=BOARD, status=VibeImportStatus.DONE)
        res = self.client.get(URL)
        self.assertEqual(res.status_code, 200)
        ids = [i['id'] for i in res.json()['imports']]
        self.assertEqual(ids, [str(new.pk), str(old.pk)])
        self.assertNotIn(str(foreign.pk), ids)

    def test_get_unknown_space_is_404(self):
        res = self.client.get('/api/spaces/nope/vibe-imports/')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()['error'], 'not_found')


class SpaceDeleteAndListTests(VibeApiBase):
    def test_inspirations_list(self):
        imp = VibeImport.objects.create(space=self.space, board_url=BOARD, status=VibeImportStatus.DONE)
        SpaceInspiration.objects.create(
            space=self.space, vibe_import=imp, pin_id='p1', pin_url='https://www.pinterest.com/pin/p1/',
            image_url='https://i.pinimg.com/736x/p1.jpg', board_rank=1, categories=['furniture'],
            item='sofa', styles=['boho'], colors=['white'], materials=['linen'], description='a sofa',
            categorized_at=timezone.now())
        SpaceInspiration.objects.create(space=self.space, pin_id='p0', pin_url='u', board_rank=0)
        rows = self.client.get('/api/spaces/space1/inspirations/').json()['inspirations']
        self.assertEqual([r['pinId'] for r in rows], ['p0', 'p1'])
        self.assertEqual(set(rows[1]), {
            'id', 'pinId', 'pinUrl', 'imageUrl', 'categories', 'item', 'styles', 'colors', 'materials',
            'description', 'vote', 'source', 'importId', 'categorized', 'primaryType', 'spaceId'})
        self.assertEqual(rows[1]['categories'], ['furniture'])
        self.assertEqual((rows[1]['vote'], rows[1]['source']), ('up', 'pinterest'))
        self.assertEqual((rows[1]['importId'], rows[1]['categorized']), (str(imp.pk), True))
        self.assertEqual((rows[0]['importId'], rows[0]['categorized']), (None, False))
        self.assertEqual(self.client.get('/api/spaces/nope/inspirations/').status_code, 404)

    def test_delete_space_signals_worker_and_removes_folder(self):
        folder = paths.space_dir('space1')
        folder.mkdir(parents=True)
        (folder / 'p1.jpg').write_bytes(b'x')
        VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.RUNNING, worker_pid=424242,
            started_at=timezone.now())
        with mock.patch('api.claude_cli.signal_group') as sig, \
                self.captureOnCommitCallbacks(execute=True):
            res = self.client.delete('/api/spaces/space1/')
        self.assertEqual(res.status_code, 204)
        sig.assert_called_once_with(424242, signal.SIGTERM)
        self.assertFalse(folder.exists())
        self.assertEqual(VibeImport.objects.count(), 0)

    def test_delete_space_without_imports_is_fine(self):
        with mock.patch('api.claude_cli.signal_group') as sig, \
                self.captureOnCommitCallbacks(execute=True):
            res = self.client.delete('/api/spaces/space1/')
        self.assertEqual(res.status_code, 204)
        sig.assert_not_called()


class GlobalInspirationsTests(VibeApiBase):
    def pin(self, space, pin_id, rank, categorized=True, **kw):
        return SpaceInspiration.objects.create(
            space=space, pin_id=pin_id, pin_url='u', board_rank=rank,
            categorized_at=timezone.now() if categorized else None, **kw)

    def test_empty(self):
        res = self.client.get('/api/inspirations/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {'inspirations': []})

    def test_dedupes_filters_orders_and_types(self):
        other = make_space('space2')
        self.pin(self.space, 'b', 2, item='table lamp', categories=['furniture', 'lighting'])
        self.pin(self.space, 'a', 1, item='sofa')
        self.pin(other, 'b', 2, item='table lamp')
        self.pin(other, 'c', 0, categorized=False)
        rows = self.client.get('/api/inspirations/').json()['inspirations']
        self.assertEqual([r['pinId'] for r in rows], ['a', 'b'])
        self.assertEqual([r['primaryType'] for r in rows], ['furniture', 'lighting'])
        self.assertEqual([r['spaceId'] for r in rows], ['space1', 'space1'])
        self.assertEqual(self.client.post('/api/inspirations/').status_code, 405)


class DeleteInspirationTests(VibeApiBase):
    def pin(self, space, pin_id, rank=0):
        rel = f'{space.id}/{pin_id}.jpg'
        target = paths.vibes_dir() / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(jpeg_bytes())
        return SpaceInspiration.objects.create(
            space=space, pin_id=pin_id, pin_url='u', image_path=rel, board_rank=rank,
            categorized_at=timezone.now())

    def url(self, row, space_id='space1'):
        return f'/api/spaces/{space_id}/inspirations/{row.pk}/'

    def test_delete_removes_row_file_and_listings(self):
        keep = self.pin(self.space, 'keep', 1)
        gone = self.pin(self.space, 'gone', 0)
        res = self.client.delete(self.url(gone))
        self.assertEqual(res.status_code, 204)
        self.assertEqual(res.content, b'')
        self.assertFalse(SpaceInspiration.objects.filter(pk=gone.pk).exists())
        self.assertFalse((paths.vibes_dir() / gone.image_path).exists())
        self.assertTrue((paths.vibes_dir() / keep.image_path).is_file())
        for url in ('/api/spaces/space1/inspirations/', '/api/inspirations/'):
            rows = self.client.get(url).json()['inspirations']
            self.assertEqual([r['pinId'] for r in rows], ['keep'])

    def test_other_spaces_pin_and_unknown_id_are_404(self):
        other = make_space('space2')
        theirs = self.pin(other, 'theirs')
        self.assertEqual(self.client.delete(self.url(theirs)).status_code, 404)
        self.assertTrue(SpaceInspiration.objects.filter(pk=theirs.pk).exists())
        self.assertTrue((paths.vibes_dir() / theirs.image_path).is_file())
        self.assertEqual(self.client.delete('/api/spaces/space1/inspirations/999999/').status_code, 404)

    def test_unknown_space_is_404(self):
        row = self.pin(self.space, 'p1')
        self.assertEqual(self.client.delete(self.url(row, 'nope')).status_code, 404)
        self.assertTrue(SpaceInspiration.objects.filter(pk=row.pk).exists())

    def test_active_import_is_409_and_keeps_the_pin(self):
        row = self.pin(self.space, 'p1')
        VibeImport.objects.create(
            space=self.space, board_url=BOARD, status=VibeImportStatus.RUNNING, started_at=timezone.now())
        res = self.client.delete(self.url(row))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json(), {'error': 'import_in_progress'})
        self.assertTrue(SpaceInspiration.objects.filter(pk=row.pk).exists())
        self.assertTrue((paths.vibes_dir() / row.image_path).is_file())
