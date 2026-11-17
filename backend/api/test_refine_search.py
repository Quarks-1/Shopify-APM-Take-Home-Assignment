"""Refine runs ("Refresh"): votes shape the next search of a row. Endpoint,
feedback snapshot, pinned liked items, exclusions, the refine ladder and its
payloads, agent-written queries, preferences, and explore-run tweaks. No
network: `ucp` is the fake CLI and image downloads are mocked."""
import json
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings

from .models import (
    PINNED_STRATEGY, Candidate, CatalogQuery, ProductVote, RunStatus, SearchMode, SearchRun,
    Verdict,
)
from .searcher import engine, like_images, prompts, query_builder, runner
from .serializers import run_to_json, vote_product_snapshot
from .test_like_search import FAKE_CLAUDE, FAKE_UCP, fake_read, make_object

FIXTURE = [  # coffee-table fixture gids, in fixture order
    'gid://shopify/p/IKAjRltyX0rlkX0y47B7c', 'gid://shopify/p/4s7yXCGTJ0W6WDgwIYx1Sg',
    'gid://shopify/p/DshvK4BEGVLXboqGYROte', 'gid://shopify/p/50laFPkQBOQ86tN7LBbgdk',
    'gid://shopify/p/j4dgbM1YdkYZ12fZF1nbp', 'gid://shopify/p/2sHUE7bYDAxfnyMWYTzK',
    'gid://shopify/p/76gV5a9ZH5XHRE9swGZqA1', 'gid://shopify/p/6juFanhvLh9SDI1czmw1IU',
]
FITS = dict(currency='USD', dims={'x': 40, 'y': 20, 'z': 16}, size_status='fits')


def finished_run(obj, items, status=RunStatus.DONE):
    """A finished run of `obj` with candidates [(gid, fields)]; returns them."""
    run = engine.create_run(obj)
    SearchRun.objects.filter(pk=run.pk).update(status=status)
    cq = CatalogQuery.objects.create(run=run, round=1, strategy='exact', payload={'query': 'x'})
    out = []
    for gid, fields in items:
        kw = dict(run=run, query=cq, product_gid=gid, title=f'Table {gid[-4:]}',
                  seller_name='Acme', seller_domain=f'{gid[-4:]}.example.com',
                  price_cents=25000, verdict=Verdict.ACCEPTED,
                  image_url=f'https://cdn.shopify.com/x/{gid[-4:]}.jpg', **FITS)
        kw.update(fields)
        out.append(Candidate.objects.create(**kw))
    return out


def vote(obj, cand, value):
    return ProductVote.objects.create(object=obj, product_gid=cand.product_gid, vote=value,
                                      candidate=cand, product=vote_product_snapshot(cand))


class RefineBase(TestCase):
    def setUp(self):
        self.var = Path(tempfile.mkdtemp(prefix='searcher-var-'))
        self.addCleanup(shutil.rmtree, self.var, True)
        cm = override_settings(SEARCHER_UCP_BIN=FAKE_UCP, SEARCHER_VAR_DIR=self.var,
                               SEARCHER_AGENT='deterministic')
        cm.enable()
        self.addCleanup(cm.disable)
        p = mock.patch.object(like_images, '_read', side_effect=fake_read)
        p.start()
        self.addCleanup(p.stop)
        self.obj = make_object()

    def voted_row(self):
        """obj1 showed fixture products 0-2 (and rejected 3); 0 and 1 liked
        (prices $200, $300), 2 disliked. Another row voted too."""
        a, b, c, rejected = finished_run(self.obj, [
            (FIXTURE[0], {'title': 'Walnut mid-century table', 'materials': ['Walnut', 'Brass'],
                          'price_cents': 20000}),
            (FIXTURE[1], {'title': 'Walnut tapered mid-century table', 'materials': ['walnut'],
                          'price_cents': 30000}),
            (FIXTURE[2], {'title': 'Glossy white table', 'materials': ['lacquer']}),
            (FIXTURE[3], {'verdict': Verdict.REJECTED}),
        ])
        vote(self.obj, a, 'up')
        vote(self.obj, b, 'up')
        vote(self.obj, c, 'down')
        other = make_object('obj2', name='Floor lamp')
        x, y = finished_run(other, [('gid://shopify/p/lampX', {'title': 'Brass lamp'}),
                                    (FIXTURE[4], {'title': 'Chrome lamp'})])
        vote(other, x, 'up')
        vote(other, y, 'down')  # disliked in another row: excluded everywhere
        return a, b, c

    def refine_run(self, status=RunStatus.RUNNING):
        run = runner._create_refine_run(self.obj)
        if status == RunStatus.RUNNING:
            SearchRun.objects.filter(pk=run.pk).update(status=RunStatus.RUNNING)
        run.refresh_from_db()
        return run


# --------------------------------------------------------------- endpoint

class RefineEndpointTests(RefineBase):
    def setUp(self):
        super().setUp()
        mock.patch.object(
            runner, '_spawn_worker',
            side_effect=lambda run_id: SearchRun.objects.filter(pk=run_id).update(worker_pid=70001),
        ).start()
        mock.patch.object(runner, '_pid_alive', return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body, oid='obj1'):
        return self.client.post(f'/api/objects/{oid}/searches/', data=json.dumps(body),
                                content_type='application/json')

    def test_refused_without_a_finished_search(self):
        r = self.post({'mode': 'refine'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'no_finished_search'))
        finished_run(self.obj, [], status=RunStatus.FAILED)
        finished_run(self.obj, [], status=RunStatus.CANCELED)
        self.assertEqual(self.post({'mode': 'refine'}).status_code, 400)
        self.assertFalse(SearchRun.objects.filter(mode='refine').exists())

    def test_strict_body(self):
        finished_run(self.obj, [])
        for body in ({'mode': 'refine', 'candidateId': 1}, {'mode': 'refine', 'extra': True},
                     {'mode': 'Refine'}, {'mode': ['refine']}):
            r = self.post(body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'validation_failed'), body)
        self.assertFalse(SearchRun.objects.filter(mode='refine').exists())

    def test_start_and_reuse(self):
        _a, b, _c = self.voted_row()
        r = self.post({'mode': 'refine'})
        self.assertEqual(r.status_code, 202)
        body = r.json()
        self.assertEqual(body['mode'], 'refine')
        self.assertNotIn('preferences', body)
        self.assertEqual(body['candidates'][0]['productId'], b.product_gid)  # pinned already
        self.assertEqual(body['queries'], [])  # the pin holder is not a catalog round
        again = self.post({'mode': 'refine'})
        self.assertEqual((again.status_code, again.json()['id']), (200, body['id']))

    def test_imported_object_refuses(self):
        owned = make_object('owned', source='imported')
        finished_run(owned, [])
        r = self.post({'mode': 'refine'}, oid='owned')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'imported_object'))


# ------------------------------------------------- feedback and pinning

class RefineFeedbackTests(RefineBase):
    def test_feedback_snapshot(self):
        a, b, c = self.voted_row()
        ProductVote.objects.create(object=None, product_gid='gid://shopify/p/orphan', vote='up',
                                   product={'title': 'Orphan sofa', 'price': 900})
        run = self.refine_run()
        fb = run.feedback
        self.assertEqual([i['productGid'] for i in fb['liked']], [b.product_gid, a.product_gid])
        self.assertEqual(fb['liked'][1]['materials'], ['Walnut', 'Brass'])
        self.assertEqual(fb['liked'][1]['price'], 200.0)
        self.assertEqual([i['productGid'] for i in fb['disliked']], [c.product_gid])
        self.assertEqual(fb['seen'], sorted(FIXTURE[:3]))  # accepted only, not the rejected one
        taste = {(t['title'], t['vote'], t.get('row')) for t in fb['taste']}
        self.assertEqual(taste, {('Brass lamp', 'up', 'Floor lamp'), ('Orphan sofa', 'up', None),
                                 ('Chrome lamp', 'down', 'Floor lamp')})
        self.assertEqual(set(fb['taste'][1]), {'title', 'materials', 'price', 'vote', 'row'})

    def test_taste_is_capped(self):
        finished_run(self.obj, [])
        for i in range(12):
            ProductVote.objects.create(object=None, product_gid=f'g{i}', vote='down',
                                       product={'title': f'T{i}'})
        fb = self.refine_run().feedback
        self.assertEqual(len(fb['taste']), 8)
        self.assertEqual(fb['taste'][0]['title'], 'T11')  # newest first

    def test_pins_first_accepted_and_not_counted(self):
        a, b, _c = self.voted_row()
        run = self.refine_run()
        pins = list(run.candidates.filter(query__strategy=PINNED_STRATEGY).order_by('pk'))
        self.assertEqual([p.product_gid for p in pins], [b.product_gid, a.product_gid])
        self.assertTrue(all(p.verdict == Verdict.ACCEPTED and p.passes_filters for p in pins))
        self.assertEqual(run.feedback['pinned'], [b.product_gid, a.product_gid])
        self.assertEqual(engine.accepted_count(run), 0)
        self.assertEqual(engine.status(run)['accepted'], 0)
        # More accepted items from a later round still sort after the pins.
        cq = CatalogQuery.objects.create(run=run, round=1, strategy='exact', payload={})
        Candidate.objects.create(run=run, query=cq, product_gid='gid://shopify/p/cheap',
                                 title='Cheap', price_cents=100, verdict=Verdict.ACCEPTED, **FITS)
        body = run_to_json(run)
        self.assertEqual([c['productId'] for c in body['candidates']],
                         [b.product_gid, a.product_gid, 'gid://shopify/p/cheap'])
        self.assertEqual([q['strategy'] for q in body['queries']], ['exact'])
        with self.assertRaises(engine.SearcherError):
            engine.judge(run, rejects={pins[0].pk: 'no'})

    def test_selected_product_is_kept_not_cloned(self):
        a, b, _c = self.voted_row()
        self.obj.selected_candidate = a
        self.obj.save(update_fields=['selected_candidate'])
        run = self.refine_run()
        self.assertEqual(run.feedback['pinned'], [b.product_gid])
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.selected_candidate_id, a.pk)  # still valid, untouched
        self.assertIn(a.product_gid, engine.votes.excluded_gids(run))

    def test_liked_item_that_no_longer_fits_is_not_pinned(self):
        a, b, _c = self.voted_row()
        self.obj.price_limit = 250
        self.obj.save(update_fields=['price_limit'])
        run = self.refine_run()
        self.assertEqual(run.feedback['pinned'], [a.product_gid])  # b costs $300


# -------------------------------------------------------- ladder/payloads

class RefineLadderTests(RefineBase):
    def test_ladder(self):
        self.voted_row()
        run = self.refine_run()
        self.assertEqual(engine.available_strategies(run)[:5],
                         ['pref', 'liked:0', 'liked:1', 'exact', 'relax-style'])

    def test_pref_payload(self):
        self.voted_row()
        run = self.refine_run()
        out = engine.next_round(run, 'pref')
        payload = run.queries.get(strategy='pref').payload
        self.assertEqual(payload['query'], 'mid-century walnut Coffee table')
        intent = payload['context']['intent']
        self.assertTrue(intent.startswith('Looking for a coffee table'))
        self.assertIn('Prefers items like: Walnut tapered mid-century table (walnut); '
                      'Walnut mid-century table (Walnut, Brass).', intent)
        self.assertIn('Avoid: Glossy white table.', intent)
        self.assertEqual(payload['filters']['price'], {'min': 17000, 'max': 34500})
        self.assertNotIn('like', payload)
        self.assertEqual(out['strategy'], 'pref')

    def test_price_window_is_clipped_to_the_object(self):
        self.voted_row()
        self.obj.price_limit = 320
        self.obj.price_limit_fuzzy = True
        self.obj.save(update_fields=['price_limit', 'price_limit_fuzzy'])
        run = self.refine_run()
        payload = query_builder.build_payload(
            run.object_snapshot, 'pref', refine={'feedback': run.feedback})
        self.assertEqual(payload['filters']['price'], {'min': 17000, 'max': 34500})  # 320*1.15=368
        self.obj.price_limit_fuzzy = False
        self.obj.save(update_fields=['price_limit_fuzzy'])
        run2 = runner._create_refine_run(self.obj)
        payload = query_builder.build_payload(
            run2.object_snapshot, 'pref', refine={'feedback': run2.feedback})
        self.assertEqual(payload['filters']['price'], {'min': 17000, 'max': 32000})

    def test_no_window_with_one_liked_item(self):
        a, b, _c = self.voted_row()
        ProductVote.objects.filter(product_gid=b.product_gid).delete()
        run = self.refine_run()
        payload = query_builder.build_payload(
            run.object_snapshot, 'pref', refine={'feedback': run.feedback})
        self.assertEqual(payload['filters']['price'], {'max': 40000})
        self.assertEqual(payload['query'], 'walnut brass Coffee table')

    def test_liked_round_payload(self):
        a, b, _c = self.voted_row()
        run = self.refine_run()
        engine.next_round(run, 'liked:1')
        payload = run.queries.get(strategy='liked:1').payload
        self.assertEqual(payload['like'], [{'id': a.product_gid}])
        self.assertEqual(payload['query'], 'Coffee table')
        self.assertEqual(payload['filters']['price'], {'min': 17000, 'max': 34500})

    def test_later_rungs_use_the_object_range_and_the_preferences(self):
        self.voted_row()
        run = self.refine_run()
        engine.next_round(run, 'exact')
        payload = run.queries.get(strategy='exact').payload
        self.assertEqual(payload['query'], 'mid-century wood Coffee table')
        self.assertEqual(payload['filters']['price'], {'max': 40000})
        self.assertIn('Prefers items like:', payload['context']['intent'])

    def test_more_repeats_the_round_with_the_cursor(self):
        a, b, _c = self.voted_row()
        run = self.refine_run()
        engine.next_round(run, 'liked:0')
        self.assertIn('more', engine.available_strategies(run))
        engine.next_round(run, 'more')
        first, more = (run.queries.get(strategy=s).payload for s in ('liked:0', 'more'))
        self.assertEqual(more['pagination']['cursor'], 'page2')
        self.assertEqual({k: v for k, v in more.items() if k != 'pagination'},
                         {k: v for k, v in first.items() if k != 'pagination'})

    def test_seen_pinned_and_disliked_never_come_back(self):
        self.voted_row()
        run = self.refine_run()
        for strategy in ('pref', 'more', 'liked:0'):
            engine.next_round(run, strategy)
        new = set(run.candidates.exclude(query__strategy=PINNED_STRATEGY)
                  .values_list('product_gid', flat=True))
        self.assertTrue(new)
        self.assertEqual(new & set(FIXTURE[:3] + [FIXTURE[4]]), set())  # shown, or disliked elsewhere
        self.assertEqual(new, {FIXTURE[3]} | set(FIXTURE[5:]))  # the rejected one may return

    def test_search_mode_is_untouched(self):
        run = engine.create_run(self.obj)
        self.assertEqual(engine.available_strategies(run)[0], 'exact')
        engine.next_round(run)
        self.assertNotIn('Prefers', run.queries.get().payload['context']['intent'])


# ------------------------------------------------ agent query and intent

class AgentQueryTests(RefineBase):
    def cmd(self, run, *args):
        out = StringIO()
        with mock.patch.dict(os.environ, {'SEARCH_RUN_ID': str(run.pk)}):
            try:
                call_command('searcher', *args, stdout=out)
            except SystemExit:
                pass
        return json.loads(out.getvalue())

    def test_rejected_outside_refine_pref(self):
        search = engine.create_run(self.obj)
        doc = self.cmd(search, 'next', '--strategy', 'exact', '--query', 'oak table')
        self.assertIn('only allowed with --strategy pref on a refine run', doc['error'])
        self.assertFalse(search.queries.exists())
        self.voted_row()
        run = self.refine_run()
        for args in (('next', '--query', 'oak'), ('next', '--strategy', 'exact', '--intent', 'x')):
            self.assertIn('only allowed', self.cmd(run, *args)['error'])
        self.assertFalse(run.queries.exclude(strategy=PINNED_STRATEGY).exists())

    def test_stored_on_refine(self):
        self.voted_row()
        run = self.refine_run()
        doc = self.cmd(run, 'next', '--strategy', 'pref', '--query', 'walnut\n coffee\x00table ' + 'x' * 200,
                       '--intent', 'Warm walnut wood, no glossy white.\r\nNothing else.')
        self.assertEqual(doc['strategy'], 'pref')
        payload = run.queries.get(strategy='pref').payload
        self.assertEqual(len(payload['query']), engine.AGENT_QUERY_LEN)
        self.assertTrue(payload['query'].startswith('walnut coffee table xxx'))
        self.assertTrue(payload['context']['intent'].endswith(
            ' Warm walnut wood, no glossy white. Nothing else.'))
        self.assertNotIn('Prefers items like', payload['context']['intent'])
        self.assertIn('min', payload['filters']['price'])
        # pref is used up now.
        self.assertIn('unavailable', self.cmd(run, 'next', '--strategy', 'pref', '--query', 'x')['error'])


# ------------------------------------------------------ runs end to end

class RefineRunTests(RefineBase):
    def test_deterministic_refine_run_end_to_end(self):
        a, b, _c = self.voted_row()
        self.obj.selected_candidate = a
        self.obj.save(update_fields=['selected_candidate'])
        run = runner._create_refine_run(self.obj)
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.DONE, run.error)
        self.assertEqual(run.agent_mode, 'deterministic')
        self.assertEqual([q.strategy for q in run.queries.exclude(strategy=PINNED_STRATEGY)][:3],
                         ['pref', 'liked:0', 'liked:1'])
        self.assertEqual(len(run.feedback['images']), 2)  # a photo of each liked item
        body = self.client.get(f'/api/searches/{run.pk}/').json()
        self.assertEqual(body['mode'], 'refine')
        self.assertEqual(body['candidates'][0]['productId'], b.product_gid)
        gids = [c['productId'] for c in body['candidates']]
        self.assertEqual(len(gids), len(set(gids)))
        self.assertNotIn(a.product_gid, gids)  # selected: kept by the client, never duplicated
        self.assertNotIn(FIXTURE[2], gids)
        self.assertNotIn(FIXTURE[4], gids)
        self.assertNotIn('preferences', body)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.selected_candidate_id, a.pk)

    def test_explore_run_excludes_disliked_products(self):
        other = make_object('obj2')
        x, = finished_run(other, [(FIXTURE[1], {})])
        vote(other, x, 'down')
        ProductVote.objects.create(object=None, product_gid=FIXTURE[6], vote='down', product={})
        run = engine.create_run(self.obj)
        engine.run_deterministic(run)
        gids = set(run.candidates.values_list('product_gid', flat=True))
        self.assertTrue(gids)
        self.assertEqual(gids & {FIXTURE[1], FIXTURE[6]}, set())

    def test_explore_picks_spread_over_price_tiers(self):
        run = engine.create_run(self.obj)
        cq = CatalogQuery.objects.create(run=run, round=1, strategy='exact', payload={})
        ids = [Candidate.objects.create(run=run, query=cq, product_gid=f'g{i}', title=f'T{i}',
                                        seller_domain=f's{i}.com', price_cents=price, **FITS).pk
               for i, price in enumerate([1000, 2000, 3000, 20000, 21000, 30000])]
        picked = engine._pick_deterministic_accepts(run, ids, 3)
        self.assertEqual([c.price_cents for c in picked], [1000, 3000, 21000])
        like = engine.create_run(self.obj, mode=SearchMode.LIKE, like_source={})
        Candidate.objects.filter(pk__in=ids).update(run=like)
        picked = engine._pick_deterministic_accepts(like, ids, 3)
        self.assertEqual([c.price_cents for c in picked], [1000, 2000, 3000])  # cheapest first

    def test_preferences_are_parsed_and_served(self):
        self.voted_row()
        run = self.refine_run()
        prefs = runner.save_preferences(run, {'structured_output': {
            'outcome': 'exhausted', 'accepted': 0, 'notes': '',
            'preferences': {'likes': ['warm\x07 walnut', '', 7] + [f'l{i}' for i in range(9)],
                            'avoids': ['x' * 100]}}})
        self.assertEqual(prefs['likes'][:2], ['warm walnut', 'l0'])
        self.assertEqual(len(prefs['likes']), 6)
        self.assertEqual(prefs['avoids'], ['x' * 60])
        run.refresh_from_db()
        self.assertEqual(run_to_json(run)['preferences'], prefs)
        self.assertIsNone(runner.save_preferences(run, {'structured_output': {'outcome': 'x'}}))
        self.assertNotIn('preferences', run_to_json(SearchRun.objects.get(pk=run.pk)))
        search = engine.create_run(self.obj)
        runner.save_preferences(search, {'structured_output': {'preferences': {'likes': ['a'],
                                                                               'avoids': []}}})
        search.refresh_from_db()
        self.assertIsNone(search.preferences)

    def test_prompts(self):
        self.voted_row()
        run = self.refine_run()
        system = prompts.system_prompt(run, photos_attached=0)
        self.assertIn('REFINE mode', system)
        self.assertIn('--strategy pref --query', system)
        self.assertNotIn('Explore (this run)', system)
        self.assertIn('Explore (this run)', prompts.system_prompt(engine.create_run(self.obj)))
        text = prompts.build_user_prompt(run, photos_attached=0)
        self.assertIn('untrusted merchant data', text)
        self.assertIn('"title": "Glossy white table"', text)
        self.assertIn('"row": "Floor lamp"', text)
        self.assertIn('2 liked item(s) are already pinned', text)
        schema = json.loads(prompts.output_schema_json())
        self.assertIn('preferences', schema['properties'])
        self.assertNotIn('preferences', schema['required'])

    def test_argv_tools_unchanged_and_photos_go_on_stdin(self):
        self.voted_row()
        run = self.refine_run()
        runner.prepare_refine_run(run)
        self.assertEqual(len(run.feedback['images']), 2)
        argv = runner.build_argv(run)
        self.assertIn('--input-format', argv)
        rules = argv[argv.index('--allowedTools') + 1:argv.index('--permission-mode')]
        self.assertEqual(rules, list(runner.ALLOWED_TOOLS))
        self.assertIn('Photos of some liked items are attached', argv[argv.index('--system-prompt') + 1])


class RefineAgentEndToEndTests(TransactionTestCase):
    """fake claude -> real searcher wrapper -> fake ucp, for a refine run."""

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
        self.obj = make_object(name='Coffee table fake=prefs')

    def test_refine_run_through_fake_agent(self):
        liked, = finished_run(self.obj, [(FIXTURE[0], {'title': 'Walnut table'})])
        vote(self.obj, liked, 'up')
        run = runner._create_refine_run(self.obj)
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.DONE, run.error)
        self.assertEqual((run.agent_mode, run.fallback_reason), ('claude', ''))
        info = json.loads((self.cwd / f'fake-claude-{run.pk}.json').read_text())
        self.assertEqual(info['problems'], [])
        self.assertEqual(info['image_types'], ['image/jpeg'])
        self.assertIn('Walnut table', info['stdin_text'])
        self.assertEqual(run.preferences, {'likes': ['warm walnut wood', 'tapered legs'],
                                           'avoids': ['glossy white']})
        new = engine.accepted_count(run)  # few fixture products fit this object
        self.assertGreaterEqual(new, 1)
        body = run_to_json(run)
        self.assertEqual(body['preferences'], run.preferences)
        self.assertEqual(body['candidates'][0]['productId'], FIXTURE[0])
        self.assertEqual(body['accepted'], new + 1)
        self.assertEqual(run.queries.exclude(strategy=PINNED_STRATEGY).first().strategy, 'pref')
