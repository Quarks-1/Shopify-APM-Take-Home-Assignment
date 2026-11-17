""""More like this" runs: endpoint, photo gathering, the like ladder, payloads
and a deterministic run end to end. No network: image downloads are mocked
and `ucp` is the fake CLI."""
import base64
import os
import json
import shutil
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import Candidate, CatalogQuery, RunStatus, SearchMode, SearchRun, SpaceObject, Verdict
from .searcher import engine, like_images, prompts, query_builder, runner, ucp_client

FAKE_UCP = str(Path(__file__).parent / 'test_fixtures' / 'fake_ucp.py')
FAKE_CLAUDE = str(Path(__file__).parent / 'test_fixtures' / 'fake_claude.py')
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
JPG = b'\xff\xd8\xff\xe0' + b'\x01' * 64
SOURCE_GID = 'gid://shopify/p/IKAjRltyX0rlkX0y47B7c'  # first product of the coffee-table fixture


def make_object(oid='obj1', **kw):
    fields = dict(
        id=oid, name='Coffee table', dims={'x': 46, 'y': 24, 'z': 17}, type='furniture',
        material='wood', style='mid-century', price_limit=400, similar_items=['cocktail table'],
        created_at=timezone.now(),
    )
    fields.update(kw)
    return SpaceObject.objects.create(**fields)


def fake_read(url):
    """Stand-in for like_images._read: jpg for .jpg, png for .png."""
    return PNG if '.png' in url else JPG


class LikeBase(TestCase):
    def setUp(self):
        self.var = Path(tempfile.mkdtemp(prefix='searcher-var-'))
        self.addCleanup(shutil.rmtree, self.var, True)
        for cm in (override_settings(SEARCHER_UCP_BIN=FAKE_UCP, SEARCHER_VAR_DIR=self.var,
                                     SEARCHER_AGENT='deterministic'),):
            cm.enable()
            self.addCleanup(cm.disable)
        self.obj = make_object()

    def accepted_candidate(self, obj=None, gid=SOURCE_GID, verdict=Verdict.ACCEPTED, **kw):
        """An accepted candidate on a finished earlier run."""
        obj = obj or self.obj
        run = engine.create_run(obj)
        SearchRun.objects.filter(pk=run.pk).update(status=RunStatus.DONE)
        cq = CatalogQuery.objects.create(run=run, round=1, strategy='exact', payload={'query': 'x'})
        fields = dict(run=run, query=cq, product_gid=gid, title='Walnut table', seller_name='Acme',
                      description='A walnut table', image_url='https://cdn.shopify.com/x/cand.jpg',
                      verdict=verdict)
        fields.update(kw)
        return Candidate.objects.create(**fields)

    def like_run(self, candidate=None, images=2):
        """A running like run with `images` cached photos (as the worker would leave it)."""
        candidate = candidate or self.accepted_candidate()
        source = runner.like_source_for(candidate)
        run = engine.create_run(self.obj, mode=SearchMode.LIKE, like_source=source)
        photos = []
        folder = runner.like_dir(run.pk)
        folder.mkdir(parents=True)
        for i in range(images):
            path = folder / f'{i}.jpg'
            path.write_bytes(JPG + bytes([i]))
            photos.append({'index': i, 'path': str(path), 'contentType': 'image/jpeg',
                           'bytes': path.stat().st_size})
        source['images'] = photos
        SearchRun.objects.filter(pk=run.pk).update(like_source=source)
        run.refresh_from_db()
        return run


# --------------------------------------------------------------- endpoint

@override_settings(SEARCHER_UCP_BIN=FAKE_UCP)
class LikeEndpointTests(LikeBase):
    def setUp(self):
        super().setUp()
        mock.patch.object(
            runner, '_spawn_worker',
            side_effect=lambda run_id: SearchRun.objects.filter(pk=run_id).update(worker_pid=70001),
        ).start()
        mock.patch.object(runner, '_pid_alive', return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body=None, oid='obj1'):
        kw = {} if body is None else {'data': json.dumps(body), 'content_type': 'application/json'}
        return self.client.post(f'/api/objects/{oid}/searches/', **kw)

    def test_plain_search_still_works_without_a_body(self):
        r = self.post()
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()['mode'], 'search')
        self.assertNotIn('likeCandidateId', r.json())

    def test_start_like_run(self):
        cand = self.accepted_candidate()
        r = self.post({'mode': 'like', 'candidateId': cand.pk})
        self.assertEqual(r.status_code, 202)
        body = r.json()
        self.assertEqual((body['mode'], body['likeCandidateId'], body['likeTitle']),
                         ('like', cand.pk, 'Walnut table'))
        run = SearchRun.objects.get(pk=body['id'])
        self.assertEqual(run.mode, 'like')
        src = run.like_source
        self.assertEqual((src['productGid'], src['seller'], src['imageUrls']),
                         (SOURCE_GID, 'Acme', ['https://cdn.shopify.com/x/cand.jpg']))

    def test_active_run_is_returned(self):
        cand = self.accepted_candidate()
        first = self.post({'mode': 'like', 'candidateId': cand.pk}).json()
        again = self.post({'mode': 'like', 'candidateId': cand.pk})
        self.assertEqual((again.status_code, again.json()['id']), (200, first['id']))

    def test_validation(self):
        other = make_object('obj2')
        foreign = self.accepted_candidate(obj=other)
        pending = self.accepted_candidate(gid='gid://shopify/p/other', verdict=Verdict.PENDING)
        good = self.accepted_candidate(gid='gid://shopify/p/good')
        cases = [
            {'mode': 'like', 'candidateId': foreign.pk},
            {'mode': 'like', 'candidateId': pending.pk},
            {'mode': 'like', 'candidateId': 999999},
            {'mode': 'like'},
            {'mode': 'like', 'candidateId': 'x'},
            {'mode': 'like', 'candidateId': True},
            {'mode': 'like', 'candidateId': good.pk, 'extra': 1},
            {'mode': 'nope', 'candidateId': good.pk},
            {'mode': 'search', 'candidateId': good.pk},
            [1],
        ]
        for body in cases:
            r = self.post(body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'validation_failed'), body)
        self.assertEqual(SearchRun.objects.filter(mode='like').count(), 0)
        bad = self.client.post('/api/objects/obj1/searches/', data='{', content_type='application/json')
        self.assertEqual(bad.status_code, 400)

    def test_imported_object_refuses(self):
        owned = make_object('owned', source='imported')
        cand = self.accepted_candidate(obj=owned)
        r = self.post({'mode': 'like', 'candidateId': cand.pk}, oid='owned')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'imported_object'))


# ----------------------------------------------------------------- photos

class ImageUrlTests(SimpleTestCase):
    def product(self):
        return {
            'media': [
                {'type': 'image', 'url': 'https://cdn.shopify.com/f/a.jpg?v=1'},
                {'type': 'image', 'url': 'https://cdn.shopify.com/f/a.jpg?v=2'},
                {'type': 'video', 'url': 'https://cdn.shopify.com/f/clip.mp4'},
                {'url': 'https://cdn.shopify.com/f/movie.mp4'},
                {'type': 'image', 'url': 'http://cdn.shopify.com/f/insecure.jpg'},
                'junk',
                {'type': 'image', 'url': 'https://cdn.shopify.com/f/b.png'},
            ],
            'variants': [
                {'media': [{'type': 'image', 'url': 'https://cdn.shopify.com/f/c.jpg'}]},
                {'media': [{'type': 'image', 'url': 'https://cdn.shopify.com/f/b.png?v=9'},
                           {'type': 'image', 'url': 'https://cdn.shopify.com/f/d.jpg'}]},
            ],
        }

    def test_dedupe_order_and_non_images(self):
        urls = like_images.image_urls(self.product(), cap=8)
        self.assertEqual([u.rsplit('/', 1)[1] for u in urls],
                         ['a.jpg?v=1', 'b.png', 'c.jpg', 'd.jpg'])

    def test_cap(self):
        self.assertEqual(len(like_images.image_urls(self.product(), cap=2)), 2)
        with override_settings(SEARCHER_LIKE_MAX_IMAGES=3):
            self.assertEqual(len(like_images.image_urls(self.product())), 3)

    def test_garbage_product(self):
        self.assertEqual(like_images.image_urls(None), [])
        self.assertEqual(like_images.image_urls({'media': 'x', 'variants': ['y']}), [])


class FetchImageTests(SimpleTestCase):
    def test_host_allowlist(self):
        for bad in ('https://evil.example.com/a.jpg', 'http://cdn.shopify.com/a.jpg',
                    'https://cdn.shopify.com.evil.com/a.jpg', 'ftp://cdn.shopify.com/a.jpg', ''):
            with self.assertRaises(like_images.ImageError, msg=bad):
                like_images.allowed_url(bad)
        self.assertEqual(like_images.allowed_url('https://CDN.shopify.com/a.jpg'),
                         'https://CDN.shopify.com/a.jpg')
        with override_settings(SEARCHER_LIKE_IMAGE_HOSTS=['img.example.com']):
            like_images.allowed_url('https://img.example.com/a.jpg')
            with self.assertRaises(like_images.ImageError):
                like_images.allowed_url('https://cdn.shopify.com/a.jpg')

    def test_width_added_or_replaced(self):
        self.assertEqual(like_images.sized_url('https://cdn.shopify.com/a.jpg'),
                         'https://cdn.shopify.com/a.jpg?width=768')
        self.assertEqual(like_images.sized_url('https://cdn.shopify.com/a.jpg?v=3&width=2000'),
                         'https://cdn.shopify.com/a.jpg?v=3&width=768')

    def test_type_comes_from_magic_bytes(self):
        cases = [(JPG, ('image/jpeg', 'jpg')), (PNG, ('image/png', 'png')),
                 (b'RIFF\x00\x00\x00\x00WEBPVP8 ', ('image/webp', 'webp')),
                 (b'GIF89a....', ('image/gif', 'gif')), (b'<html>', None), (b'', None)]
        for data, expected in cases:
            self.assertEqual(like_images.sniff(data), expected)
        # A .jpg URL that serves a PNG is stored as a PNG; HTML is refused.
        with mock.patch.object(like_images, '_read', return_value=PNG) as read:
            _, ctype, ext = like_images.fetch_image('https://cdn.shopify.com/a.jpg')
        self.assertEqual((ctype, ext), ('image/png', 'png'))
        read.assert_called_once_with('https://cdn.shopify.com/a.jpg?width=768')
        with mock.patch.object(like_images, '_read', return_value=b'<html>nope</html>'):
            with self.assertRaises(like_images.ImageError):
                like_images.fetch_image('https://cdn.shopify.com/a.jpg')

    def test_not_allowlisted_is_never_requested(self):
        with mock.patch.object(like_images, '_read') as read:
            with self.assertRaises(like_images.ImageError):
                like_images.fetch_image('https://evil.example.com/a.jpg')
        read.assert_not_called()

    def test_redirects_must_stay_on_the_allowlist(self):
        handler = like_images._AllowlistRedirects()
        req = mock.Mock(full_url='https://cdn.shopify.com/a.jpg')
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, 'Found', {}, 'https://evil.example.com/a.jpg')
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, 'Found', {}, 'http://cdn.shopify.com/a.jpg')

    def test_size_cap(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b'\xff\xd8\xff' + b'0' * like_images.MAX_BYTES
        with mock.patch('urllib.request.OpenerDirector.open', return_value=resp):
            with self.assertRaises(like_images.ImageError):
                like_images._read('https://cdn.shopify.com/a.jpg')

    def test_cache_images_skips_failures_and_numbers_consecutively(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        logs = []
        urls = ['https://cdn.shopify.com/a.jpg', 'https://evil.example.com/b.jpg',
                'https://cdn.shopify.com/c.png']
        with mock.patch.object(like_images, '_read', side_effect=fake_read):
            images = like_images.cache_images(urls, tmp / 'like', logs.append)
        self.assertEqual([(i['index'], i['contentType'], Path(i['path']).name) for i in images],
                         [(0, 'image/jpeg', '0.jpg'), (1, 'image/png', '1.png')])
        self.assertEqual(images[1]['bytes'], len(PNG))
        self.assertTrue(Path(images[0]['path']).is_file())
        self.assertEqual(len(logs), 1)
        self.assertIn('host not allowed', logs[0])


# ------------------------------------------------------------ ladder/payload

class LikeLadderTests(LikeBase):
    def test_order(self):
        run = self.like_run(images=2)
        self.assertEqual(
            query_builder.strategies(run.object_snapshot, [], [], like=run.like_source),
            ['like:0', 'like:1', 'like:product', 'like-visual:0', 'like-visual:1'])
        # A second ladder entry is used only when a round actually ran it.
        prev = [{'round': 1, 'strategy': 'like:0', 'query': 'Coffee table', 'payload': {},
                 'response_meta': {}, 'passing_count': 0}]
        self.assertEqual(
            query_builder.strategies(run.object_snapshot, ['Coffee table'], prev, like=run.like_source)[0],
            'like:1')

    def test_more_appears_when_a_page_is_left(self):
        run = self.like_run(images=1)
        prev = [{'round': 1, 'strategy': 'like:0', 'query': 'Coffee table', 'payload': {},
                 'response_meta': {'has_next_page': True, 'cursor': 'page2'}, 'passing_count': 2}]
        self.assertEqual(query_builder.strategies({}, [], prev, like=run.like_source)[-1], 'more')

    def test_search_mode_ladder_is_untouched(self):
        snap = self.like_run().object_snapshot
        self.assertEqual(query_builder.strategies(snap, []),
                         ['exact', 'relax-style', 'name', 'similar:0', 'broad'])

    def test_no_photos_leaves_the_product_round(self):
        run = self.like_run(images=0)
        self.assertEqual(query_builder.strategies({}, [], [], like=run.like_source), ['like:product'])

    def test_default_ladder_is_not_cut_short_by_expand_after(self):
        run = self.like_run(images=2)
        SearchRun.objects.filter(pk=run.pk).update(round_count=7, max_rounds=12)
        run.refresh_from_db()
        self.assertEqual(engine._default_strategy(run, engine.available_strategies(run)), 'like:0')


class LikePayloadTests(LikeBase):
    def setUp(self):
        super().setUp()
        self.sent = []
        real = ucp_client.search_catalog

        def spy(payload):
            self.sent.append(json.loads(json.dumps(payload)))
            return real(payload)
        p = mock.patch.object(ucp_client, 'search_catalog', side_effect=spy)
        p.start()
        self.addCleanup(p.stop)

    def test_image_round_payload(self):
        run = self.like_run(images=2)
        out = engine.next_round(run)
        self.assertEqual(out['strategy'], 'like:0')
        sent = self.sent[0]
        self.assertEqual(sent['query'], 'Coffee table')
        self.assertEqual(len(sent['like']), 1)
        self.assertEqual(sent['like'][0]['image']['content_type'], 'image/jpeg')
        self.assertEqual(base64.b64decode(sent['like'][0]['image']['data']), JPG + b'\x00')
        # Same hard filters as a text search, intent names the reference.
        self.assertEqual(sent['filters']['price'], {'max': 40000})
        self.assertTrue(sent['filters']['available'])
        self.assertEqual(sent['filters']['ships_to'], {'country': 'US'})
        self.assertIn('Visually similar to: Walnut table', sent['context']['intent'])

    def test_database_never_holds_base64(self):
        run = self.like_run(images=2)
        for _ in range(4):
            engine.next_round(run)
        b64s = [item['image']['data'] for item in (p['like'][0] for p in self.sent) if 'image' in item]
        self.assertTrue(b64s)
        for q in CatalogQuery.objects.filter(run=run):
            text = json.dumps(q.payload)
            for b in b64s:
                self.assertNotIn(b, text)
        q0 = CatalogQuery.objects.get(run=run, round=1)
        self.assertEqual(q0.payload['like'][0]['image']['data'], f'<image 0, {len(JPG) + 1} bytes>')
        self.assertEqual(q0.payload['query'], 'Coffee table')

    def test_product_and_visual_rounds(self):
        run = self.like_run(images=1)
        engine.next_round(run, 'like:product')
        engine.next_round(run, 'like-visual:0')
        self.assertEqual(self.sent[0]['like'], [{'id': SOURCE_GID}])
        self.assertEqual(self.sent[0]['query'], 'Coffee table')
        self.assertNotIn('query', self.sent[1])
        self.assertIn('image', self.sent[1]['like'][0])
        stored = CatalogQuery.objects.get(run=run, strategy='like-visual:0').payload
        self.assertNotIn('query', stored)

    def test_more_reattaches_the_same_like_item(self):
        run = self.like_run(images=2)
        engine.next_round(run, 'like-visual:1')   # no query, page 1 has a cursor
        engine.next_round(run, 'like:0')
        self.assertIn('more', engine.available_strategies(run))
        # `more` continues the most productive query; make like:0 the winner.
        CatalogQuery.objects.filter(run=run, strategy='like:0').update(passing_count=9)
        engine.next_round(run, 'more')
        first, more = self.sent[1], self.sent[2]
        self.assertEqual(more['like'], first['like'])
        self.assertEqual(more['query'], first['query'])
        self.assertEqual(more['pagination']['cursor'], 'page2')
        self.assertNotIn('<image', json.dumps(more))
        stored = CatalogQuery.objects.get(run=run, strategy='more').payload
        self.assertEqual(stored['like'][0]['image']['data'], f'<image 0, {len(JPG) + 0 + 1} bytes>')

    def test_more_of_a_product_round(self):
        run = self.like_run(images=0)
        engine.next_round(run, 'like:product')
        engine.next_round(run, 'more')
        self.assertEqual(self.sent[1]['like'], [{'id': SOURCE_GID}])
        self.assertEqual(self.sent[1]['pagination']['cursor'], 'page2')

    def test_source_product_is_excluded(self):
        run = self.like_run(images=1)
        out = engine.next_round(run)
        self.assertEqual(out['returned'], 5)  # the fixture's first product is the source
        self.assertEqual(out['new'], 4)
        self.assertFalse(Candidate.objects.filter(run=run, product_gid=SOURCE_GID).exists())

    def test_seed_embedding_failure_is_just_a_failed_round(self):
        cand = self.accepted_candidate(gid='gid://shopify/p/NOEMB1')
        run = self.like_run(candidate=cand, images=1)
        out = engine.next_round(run, 'like:product')
        self.assertIn('similarity seed embedding is missing', out['error'])
        q = CatalogQuery.objects.get(run=run, strategy='like:product')
        self.assertIn('embedding', q.error)
        self.assertNotIn('like:product', engine.available_strategies(run))
        self.assertTrue(engine.next_round(run)['strategy'].startswith('like'))

    def test_vanished_photo_is_a_failed_round(self):
        run = self.like_run(images=1)
        Path(run.like_source['images'][0]['path']).unlink()
        out = engine.next_round(run, 'like:0')
        self.assertIn('could not build like query', out['error'])
        self.assertEqual(self.sent, [])
        self.assertNotIn('like:0', engine.available_strategies(run))


# ---------------------------------------------------------- worker / prompts

class LikeRunTests(LikeBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(like_images, '_read', side_effect=fake_read)
        p.start()
        self.addCleanup(p.stop)

    def queued_like_run(self, candidate=None):
        candidate = candidate or self.accepted_candidate()
        return engine.create_run(self.obj, status=RunStatus.QUEUED, mode=SearchMode.LIKE,
                                 like_source=runner.like_source_for(candidate))

    def test_prepare_gathers_dedupes_and_downloads(self):
        run = self.queued_like_run()
        runner.prepare_like_run(run)
        src = run.like_source
        # a.jpg, b.png, (evil host: listed but skipped), d.jpg, e.jpg; clip.mp4 never listed.
        self.assertEqual([u.rsplit('/', 1)[1] for u in src['imageUrls']],
                         ['a.jpg?v=1', 'b.png', 'c.jpg', 'd.jpg', 'e.jpg'])
        self.assertEqual([i['contentType'] for i in src['images']],
                         ['image/jpeg', 'image/png', 'image/jpeg', 'image/jpeg'])
        self.assertEqual([i['index'] for i in src['images']], [0, 1, 2, 3])
        self.assertEqual(Path(src['images'][0]['path']),
                         self.var / 'searches' / str(run.pk) / 'like' / '0.jpg')
        self.assertEqual(run.max_rounds, 12)  # max(12, min(20, 2*4+2))
        self.assertIn('host not allowed', runner.log_path(run.pk).read_text())

    def test_prepare_caps_images_and_grows_the_round_budget(self):
        run = self.queued_like_run()
        with override_settings(SEARCHER_LIKE_MAX_IMAGES=3, SEARCHER_MAX_ROUNDS=4):
            runner.prepare_like_run(run)
        self.assertEqual(len(run.like_source['imageUrls']), 3)
        self.assertEqual(run.max_rounds, 2 * 2 + 2)  # 3 urls, one is off-allowlist
        run2 = self.queued_like_run()
        with override_settings(SEARCHER_MAX_ROUNDS=4):
            runner.prepare_like_run(run2)
        self.assertEqual(run2.max_rounds, 10)

    def test_get_product_failure_falls_back_to_the_candidate_image(self):
        cand = self.accepted_candidate(gid='gid://shopify/p/FAIL1')
        run = self.queued_like_run(cand)
        runner.prepare_like_run(run)
        self.assertEqual(run.like_source['imageUrls'], ['https://cdn.shopify.com/x/cand.jpg'])
        self.assertEqual(len(run.like_source['images']), 1)
        self.assertIn('get_product failed', runner.log_path(run.pk).read_text())

    def test_no_photos_at_all_still_leaves_the_product_round(self):
        cand = self.accepted_candidate(gid='gid://shopify/p/FAIL2', image_url='')
        run = self.queued_like_run(cand)
        runner.prepare_like_run(run)
        self.assertEqual(run.like_source['images'], [])
        self.assertEqual(engine.available_strategies(run), ['like:product'])

    def test_get_product_ids_are_validated(self):
        with self.assertRaises(ucp_client.UcpError):
            ucp_client.get_product('--help')
        with self.assertRaises(ucp_client.UcpError):
            ucp_client.get_product('gid://x; rm -rf')

    def test_deterministic_like_run_end_to_end(self):
        run = self.queued_like_run()
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.DONE, run.error)
        self.assertEqual(run.agent_mode, 'deterministic')
        self.assertEqual(len(run.like_source['images']), 4)
        accepted = Candidate.objects.filter(run=run, verdict=Verdict.ACCEPTED)
        self.assertGreater(accepted.count(), 0)
        self.assertFalse(accepted.filter(product_gid=SOURCE_GID).exists())
        strategies = [q.strategy for q in run.queries.all()]
        self.assertEqual(strategies[:4], ['like:0', 'like:1', 'like:2', 'like:3'])
        self.assertIn('like:product', strategies)
        self.assertIn('like-visual:0', strategies)
        self.assertTrue(all(not q.error for q in run.queries.all()))
        for q in run.queries.all():
            self.assertNotIn('"data": "/9j', json.dumps(q.payload))
        # Served like any run.
        body = self.client.get(f'/api/searches/{run.pk}/').json()
        self.assertEqual((body['mode'], body['likeTitle'], body['status']), ('like', 'Walnut table', 'done'))
        self.assertGreater(body['accepted'], 0)

    def test_prompts(self):
        run = self.queued_like_run()
        runner.prepare_like_run(run)
        text = prompts.build_user_prompt(run)
        self.assertIn('reference product', text)
        self.assertIn('Walnut table', text)
        self.assertIn('A walnut table', text)
        self.assertIn('"name": "Coffee table"', text)
        self.assertIn('MORE LIKE THIS', prompts.system_prompt(run))
        search = engine.create_run(self.obj)
        self.assertEqual(prompts.system_prompt(search), prompts.SYSTEM_PROMPT)
        self.assertNotIn('reference', prompts.build_user_prompt(search))

    def test_agent_gets_no_read_tool(self):
        argv = runner.build_argv(self.queued_like_run())
        self.assertEqual(argv[argv.index('--tools') + 1], 'Bash')
        self.assertIn('MORE LIKE THIS', argv[argv.index('--system-prompt') + 1])
        self.assertNotIn('Read', ' '.join(argv[argv.index('--allowedTools'):argv.index('--permission-mode')]))


# ------------------------------------------------- the agent sees the photos

class LikeAgentArgvTests(LikeBase):
    def tool_rules(self, argv):
        return argv[argv.index('--allowedTools') + 1:argv.index('--permission-mode')]

    def test_like_run_with_photos_uses_stream_json_stdin(self):
        run = self.like_run(images=3)
        argv = runner.build_argv(run)
        self.assertEqual(argv[:2], [settings_bin(), '-p'])
        self.assertEqual(argv[2:7], ['--input-format', 'stream-json', '--output-format',
                                     'stream-json', '--verbose'])
        self.assertEqual(argv[argv.index('--tools') + 1], 'Bash')
        self.assertEqual(self.tool_rules(argv), list(runner.ALLOWED_TOOLS))
        self.assertNotIn('Read', ' '.join(argv))
        for flag in ('--strict-mcp-config', '--safe-mode', '--no-session-persistence'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--setting-sources') + 1], 'project,local')
        self.assertIn('are attached to the first message', argv[argv.index('--system-prompt') + 1])
        self.assertNotIn(prompts.build_user_prompt(run, photos_attached=3), argv)

    def test_search_run_argv_is_unchanged(self):
        run = engine.create_run(self.obj)
        argv = runner.build_argv(run)
        self.assertEqual(argv[:2], [settings_bin(), '-p'])
        self.assertEqual(argv[2], prompts.build_user_prompt(run))
        self.assertEqual(argv[3:5], ['--output-format', 'json'])
        for flag in ('--input-format', '--verbose', 'stream-json'):
            self.assertNotIn(flag, argv)

    def test_like_run_without_photos_is_text_only(self):
        run = self.like_run(images=0)
        argv = runner.build_argv(run)
        self.assertEqual(argv[3:5], ['--output-format', 'json'])
        self.assertNotIn('--input-format', argv)
        self.assertIn('You cannot see the reference photos', argv[2])
        self.assertIn('not available to you', argv[argv.index('--system-prompt') + 1])

    def test_photos_that_vanished_fall_back_to_text_only(self):
        run = self.like_run(images=2)
        for image in run.like_source['images']:
            Path(image['path']).unlink()
        self.assertEqual(runner.like_photo_entries(run), [])
        self.assertNotIn('--input-format', runner.build_argv(run))

    def test_stdin_message_has_text_then_images_in_order(self):
        run = self.like_run(images=3)
        # Make the middle one a png.
        src = run.like_source
        png_path = Path(src['images'][1]['path']).with_suffix('.png')
        png_path.write_bytes(PNG)
        Path(src['images'][1]['path']).unlink()
        src['images'][1].update(path=str(png_path), contentType='image/png')
        SearchRun.objects.filter(pk=run.pk).update(like_source=src)
        run.refresh_from_db()
        photos = runner.like_photo_entries(run)
        raw = runner.build_stdin_message(run, photos)
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(raw.count(b'\n'), 1)
        msg = json.loads(raw)
        self.assertEqual((msg['type'], msg['message']['role']), ('user', 'user'))
        blocks = msg['message']['content']
        self.assertEqual([b['type'] for b in blocks], ['text', 'image', 'image', 'image'])
        self.assertEqual(blocks[0]['text'], prompts.build_user_prompt(run, photos_attached=3))
        self.assertIn('attached to this message', blocks[0]['text'])
        self.assertEqual([b['source']['media_type'] for b in blocks[1:]],
                         ['image/jpeg', 'image/png', 'image/jpeg'])
        self.assertEqual([base64.b64decode(b['source']['data']) for b in blocks[1:]],
                         [JPG + b'\x00', PNG, JPG + b'\x02'])
        self.assertTrue(all(b['source']['type'] == 'base64' for b in blocks[1:]))

    def test_missing_oversized_and_foreign_files_are_skipped(self):
        run = self.like_run(images=4)
        images = run.like_source['images']
        Path(images[0]['path']).unlink()  # gone
        with open(images[1]['path'], 'wb') as fh:  # too big
            fh.truncate(like_images.MAX_BYTES + 1)
        outside = self.var / 'outside.jpg'  # not in the run's like dir
        outside.write_bytes(JPG)
        images[2]['path'] = str(outside)
        photos = runner.like_photo_entries(run)
        self.assertEqual([p['index'] for p in photos], [3])
        blocks = json.loads(runner.build_stdin_message(run, photos))['message']['content']
        self.assertEqual(len(blocks), 2)

    def test_photos_are_capped(self):
        run = self.like_run(images=5)
        with override_settings(SEARCHER_LIKE_MAX_IMAGES=2):
            self.assertEqual(len(runner.like_photo_entries(run)), 2)


def settings_bin():
    from django.conf import settings
    return settings.SEARCHER_CLAUDE_BIN


class ParseOutputTests(SimpleTestCase):
    RESULT = {'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 3,
              'total_cost_usd': 0.01, 'result': 'ok', 'structured_output': {'outcome': 'x'}}

    def test_single_object_is_unchanged(self):
        self.assertEqual(runner._parse_output(json.dumps(self.RESULT)), self.RESULT)
        self.assertEqual(runner._parse_output('log noise\n' + json.dumps(self.RESULT)), self.RESULT)
        self.assertIsNone(runner._parse_output('not json'))
        self.assertIsNone(runner._parse_output(''))

    def test_stream_json_takes_the_last_result_line(self):
        early = dict(self.RESULT, subtype='error_max_turns', is_error=True)
        lines = [
            {'type': 'system', 'subtype': 'init'},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '"result"'}]}},
            early,
            self.RESULT,
            {'type': 'system', 'subtype': 'post_turn_summary'},
        ]
        raw = '\n'.join(json.dumps(x) for x in lines) + '\n'
        data = runner._parse_output(raw)
        self.assertEqual(data, self.RESULT)
        trimmed = runner.trim_agent_result(data)
        self.assertEqual(trimmed['subtype'], 'success')
        self.assertEqual(trimmed['structured_output'], {'outcome': 'x'})

    def test_stream_without_a_result_is_invalid(self):
        raw = '\n'.join(json.dumps(x) for x in [{'type': 'system'}, {'type': 'assistant'}])
        self.assertEqual(runner._parse_output(raw), {'type': 'assistant'})  # last-line fallback, as before
        self.assertIsNone(runner._parse_output('{"type": "system"}\ngarbage'))


class LikeAgentEndToEndTests(TransactionTestCase):
    """Real subprocesses: fake claude (stream-json) -> real searcher wrapper ->
    management command -> fake ucp, all on the (file) test database."""
    accepted_candidate = LikeBase.accepted_candidate

    def setUp(self):
        self.var = Path(tempfile.mkdtemp(prefix='searcher-var-'))
        self.addCleanup(shutil.rmtree, self.var, True)
        self.cwd = self.var / 'agent-cwd'
        cm = override_settings(
            SEARCHER_VAR_DIR=self.var, SEARCHER_AGENT_CWD=self.cwd, SEARCHER_UCP_BIN=FAKE_UCP,
            SEARCHER_CLAUDE_BIN=FAKE_CLAUDE, SEARCHER_AGENT='claude',
            SEARCHER_FALLBACK='deterministic', SEARCHER_RUN_TIMEOUT=60, SEARCHER_TARGET=2,
            SEARCHER_MODEL='haiku')
        cm.enable()
        self.addCleanup(cm.disable)
        for p in (mock.patch.object(runner, '_spawn_worker', side_effect=AssertionError('no workers')),
                  mock.patch.object(like_images, '_read', side_effect=fake_read)):
            p.start()
            self.addCleanup(p.stop)
        self.obj = make_object(dims={'x': 48, 'y': 24, 'z': 18})

    def run_like(self):
        run = engine.create_run(self.obj, status=RunStatus.QUEUED, mode=SearchMode.LIKE,
                                like_source=runner.like_source_for(self.accepted_candidate()))
        runner.execute(run)
        run.refresh_from_db()
        return run

    def dump(self, run):
        return json.loads((self.cwd / f'fake-claude-{run.pk}.json').read_text())

    def test_like_run_through_fake_agent_sees_the_photos(self):
        run = self.run_like()
        self.assertEqual(run.status, RunStatus.DONE, run.error)
        self.assertEqual((run.agent_mode, run.fallback_reason), ('claude', ''))
        self.assertGreaterEqual(Candidate.objects.filter(run=run, verdict=Verdict.ACCEPTED).count(), 1)
        info = self.dump(run)
        self.assertEqual(info['problems'], [])
        self.assertTrue(info['stream'])
        self.assertEqual(info['image_types'], ['image/jpeg', 'image/png', 'image/jpeg', 'image/jpeg'])
        self.assertIn('Walnut table', info['stdin_text'])
        self.assertNotIn('Read', ' '.join(info['argv']))
        self.assertEqual(info['argv'][info['argv'].index('--tools') + 1], 'Bash')
        # The result object came from the last line of the stream.
        self.assertEqual(run.agent_result['subtype'], 'success')
        self.assertEqual(run.agent_result['structured_output']['outcome'], 'target_reached')
        self.assertGreater(len(runner.agent_output_path(run.pk).read_text().splitlines()), 1)
        self.assertIn('4 photo(s) attached', runner.log_path(run.pk).read_text())
        self.assertFalse(runner.stdin_path(run.pk).exists())  # temp stdin file is gone

    def test_like_run_without_photos_is_text_only_through_fake_agent(self):
        cand = self.accepted_candidate(gid='gid://shopify/p/FAIL2', image_url='')
        run = engine.create_run(self.obj, status=RunStatus.QUEUED, mode=SearchMode.LIKE,
                                like_source=runner.like_source_for(cand))
        runner.execute(run)
        run.refresh_from_db()
        info = self.dump(run)
        self.assertEqual(info['problems'], [])
        self.assertFalse(info['stream'])
        self.assertNotIn('image_types', info)
        self.assertEqual(run.agent_result['subtype'], 'success')
        self.assertFalse(runner.stdin_path(run.pk).exists())

    def test_fake_agent_rejects_extra_tools(self):
        import subprocess
        argv = [a for a in runner.build_argv(engine.create_run(self.obj))]
        argv[argv.index('--tools') + 1] = 'Bash,Read'
        proc = subprocess.run([FAKE_CLAUDE, *argv[1:]], capture_output=True, text=True,
                              cwd=self.var, env={**os.environ, 'SEARCH_RUN_ID': 'x'})
        self.assertEqual(proc.returncode, 3)
