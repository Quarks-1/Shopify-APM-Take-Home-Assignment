import json
import os
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    Candidate, CatalogQuery, Placement, RunStatus, SearchRun, Space, SpaceObject, Verdict,
)
from .searcher import engine, ucp_client

FAKE = str(Path(__file__).parent / 'test_fixtures' / 'fake_ucp.py')
fake_ucp = override_settings(SEARCHER_UCP_BIN=FAKE)


def make_object(**kw):
    fields = dict(
        id='obj1', name='Coffee table', dims={'x': 46, 'y': 24, 'z': 17}, type='furniture',
        material='wood', style='mid-century', price_limit=400, similar_items=['cocktail table'],
        created_at=timezone.now(),
    )
    fields.update(kw)
    return SpaceObject.objects.create(**fields)


@fake_ucp
class EngineTests(TestCase):
    def setUp(self):
        self.obj = make_object()
        self.run = engine.create_run(self.obj)

    # ------------------------------------------------------------- runs
    def test_create_run(self):
        r = self.run
        self.assertEqual((r.status, r.step), ('running', 'planning'))
        self.assertIsNotNone(r.started_at)
        self.assertEqual((r.target, r.max_rounds), (10, 12))
        self.assertEqual(r.object_snapshot['name'], 'Coffee table')
        self.assertEqual(r.object_snapshot['priceLimit'], 400)
        self.assertNotIn('space', r.object_snapshot)

    def test_snapshot_includes_price_minimum(self):
        self.obj.price_min = 100
        self.obj.save()
        run = engine.create_run(self.obj)
        self.assertEqual((run.object_snapshot['priceMin'], run.object_snapshot['priceLimit']), (100, 400))

    def test_snapshot_includes_first_space_and_is_frozen(self):
        space = Space.objects.create(id='s1', name='Den', type='shelf', unit='in',
                                     dims={'x': 50, 'y': 12}, created_at=timezone.now(),
                                     updated_at=timezone.now())
        Placement.objects.create(space=space, object=self.obj, x=0, y=0)
        run = engine.create_run(self.obj)
        self.assertEqual(run.object_snapshot['space'], {'type': 'shelf', 'dims': {'x': 50, 'y': 12}})
        self.obj.price_limit = 5
        self.obj.save()
        out = engine.next_round(run)
        self.assertEqual(out['passing'], out['new'] - out['filtered_out'])
        self.assertEqual(CatalogQuery.objects.get(run=run).payload['filters']['price'], {'max': 40000})

    # ------------------------------------------------------- next_round
    def test_first_round_persists_everything(self):
        out = engine.next_round(self.run)
        self.assertEqual((out['round'], out['strategy']), (1, 'exact'))
        self.assertEqual(out['query'], 'mid-century wood Coffee table')
        self.assertEqual((out['returned'], out['new']), (5, 5))
        self.assertEqual(out['passing'] + out['filtered_out'], 5)
        self.assertEqual((out['accepted'], out['target'], out['rounds_left']), (0, 10, 11))
        self.assertIn('more', out['strategies_left'])
        self.assertNotIn('exact', out['strategies_left'])

        self.run.refresh_from_db()
        self.assertEqual((self.run.round_count, self.run.step), (1, 'querying-catalog'))
        q = CatalogQuery.objects.get(run=self.run)
        self.assertEqual((q.round, q.strategy, q.returned_count, q.new_count, q.passing_count),
                         (1, 'exact', 5, 5, out['passing']))
        self.assertEqual(q.response_meta['cursor'], 'page2')
        self.assertTrue(q.response_meta['has_next_page'])
        self.assertEqual(q.response_meta['total_count'], 585)
        self.assertEqual(q.payload['pagination'], {'limit': 20})
        self.assertEqual(q.payload['filters']['price'], {'max': 40000})
        self.assertEqual(Candidate.objects.filter(run=self.run, query=q).count(), 5)

    def test_compact_candidates(self):
        out = engine.next_round(self.run)
        self.assertEqual(len(out['candidates']), 1)
        oval = out['candidates'][0]
        self.assertEqual(oval['title'], '39" Mid-Century Modern Oval Fluted Coffee Table with Curved Edges')
        self.assertEqual(oval['size'], '39.38 x 19.69 x 13.78 in (fits)')
        self.assertEqual(oval['price'], '$129.00')
        self.assertTrue(oval['seller'])
        self.assertIsInstance(oval['id'], int)
        self.assertLessEqual(len(oval['specs']), 6)
        self.assertLessEqual(len(oval['summary']), 200)
        onyx = Candidate.objects.get(run=self.run, title__startswith='Onyx')
        self.assertFalse(onyx.passes_filters)
        self.assertIn('size unverified', onyx.filter_reasons)
        self.assertEqual(set(oval), {'id', 'title', 'seller', 'price', 'size', 'weight',
                                     'materials', 'specs', 'summary'})
        self.assertEqual(Candidate.objects.filter(run=self.run, pk=oval['id']).count(), 1)

    def test_price_note_on_fuzzy_band(self):
        self.obj.price_limit = 120
        self.obj.price_limit_fuzzy = True
        self.obj.save()
        run = engine.create_run(self.obj)
        out = engine.next_round(run)
        oval = next(c for c in out['candidates'] if c['price'] == '$129.00')
        self.assertEqual(oval['priceNote'], 'slightly over budget')

    def test_no_price_note_when_in_range(self):
        out = engine.next_round(self.run)
        self.assertNotIn('priceNote', out['candidates'][0])

    def test_filter_reasons_stored_and_hidden_from_agent(self):
        out = engine.next_round(self.run)
        garvee = Candidate.objects.get(run=self.run, title__startswith='Garvee')
        self.assertFalse(garvee.passes_filters)
        self.assertEqual(garvee.size_status, 'too_big')
        self.assertIn('too large', garvee.filter_reasons[0])
        self.assertNotIn(garvee.pk, [c['id'] for c in out['candidates']])
        self.assertEqual(out['filtered_out'], 4)
        self.assertEqual(len(out['candidates']), 1)

    def test_dedupe_across_rounds(self):
        engine.next_round(self.run)
        out = engine.next_round(self.run)  # relax-style: fake returns the same first page
        self.assertEqual((out['strategy'], out['returned'], out['new'], out['passing']), ('relax-style', 5, 0, 0))
        self.assertEqual(out['candidates'], [])
        self.assertEqual(Candidate.objects.filter(run=self.run).count(), 5)
        self.assertEqual(Candidate.objects.filter(run=self.run, query__round=2).count(), 0)

    def test_default_strategy_broad_after_expand_threshold(self):
        for _ in range(5):
            out = engine.next_round(self.run)
            self.assertFalse(out.get('stop'))
        self.run.refresh_from_db()
        self.assertEqual(self.run.round_count, 5)
        out = engine.next_round(self.run)
        self.assertEqual(out['round'], 6)
        self.assertIn(out['strategy'], ('broad', 'more'))

    def test_more_uses_cursor_and_finds_new_products(self):
        engine.next_round(self.run)
        out = engine.next_round(self.run, 'more')
        self.assertEqual((out['returned'], out['new']), (3, 3))
        q = CatalogQuery.objects.get(run=self.run, round=2)
        self.assertEqual(q.payload['pagination']['cursor'], 'page2')
        self.assertEqual(q.payload['query'], 'mid-century wood Coffee table')
        self.assertNotIn('more', out['strategies_left'])  # page 2 had no next page

    def test_unknown_strategy_lists_available(self):
        for bad in ['bogus', 'more', 'similar:5']:
            with self.assertRaises(engine.SearcherError) as cm:
                engine.next_round(self.run, bad)
            self.assertIn('exact', cm.exception.extra['available'])
        self.run.refresh_from_db()
        self.assertEqual(self.run.round_count, 0)
        self.assertEqual(CatalogQuery.objects.count(), 0)

    def test_explicit_strategy(self):
        out = engine.next_round(self.run, 'broad')
        self.assertEqual((out['strategy'], out['query']), ('broad', 'mid-century wood table'))

    # ---------------------------------------------------- ucp failures
    def test_ucp_errors_are_recorded_and_counted(self):
        for name, snippet in [('CRASH', 'exit_2'), ('GARBAGE', 'invalid_json'), ('ERRENV', 'no_result')]:
            run = engine.create_run(make_object(id='o-' + name, name=name))
            out = engine.next_round(run, 'name')
            self.assertIn(snippet, out['error'], name)
            self.assertEqual((out['round'], out['strategy']), (1, 'name'))
            self.assertNotIn('candidates', out)
            run.refresh_from_db()
            self.assertEqual(run.round_count, 1)
            q = run.queries.get()
            self.assertIn(snippet, q.error)
            self.assertEqual((q.returned_count, q.payload['query']), (0, name))
            self.assertEqual(run.status, 'running')

    def test_missing_binary_is_ucp_error(self):
        with override_settings(SEARCHER_UCP_BIN='/nonexistent/ucp'):
            out = engine.next_round(self.run)
        self.assertIn('not_found', out['error'])
        self.assertEqual(self.run.queries.count(), 1)

    # --------------------------------------------------- stop conditions
    def assertStop(self, out, fragment):
        self.assertTrue(out['stop'], out)
        self.assertIn(fragment, out['reason'])

    def test_stop_max_rounds(self):
        SearchRun.objects.filter(pk=self.run.pk).update(max_rounds=1)
        engine.next_round(self.run)
        self.assertStop(engine.next_round(self.run), 'max rounds')

    def test_stop_target_reached(self):
        ids = [c['id'] for c in engine.next_round(self.run)['candidates']]
        self.assertEqual(len(ids), 1)
        SearchRun.objects.filter(pk=self.run.pk).update(target=1)
        engine.judge(self.run, ids)
        self.run.refresh_from_db()
        self.assertStop(engine.next_round(self.run), 'target reached')

    def test_stop_not_running(self):
        for st in ['queued', 'done', 'empty', 'failed', 'canceled']:
            SearchRun.objects.filter(pk=self.run.pk).update(status=st)
            self.assertStop(engine.next_round(self.run), 'not running')
        self.assertEqual(CatalogQuery.objects.count(), 0)

    def test_stop_no_strategies(self):
        for _ in range(6):
            out = engine.next_round(self.run)
            if out.get('stop'):
                break
        # exact, relax-style, name, similar:0, broad all used; page 2 of `exact` via more
        out = engine.next_round(self.run)
        out = engine.next_round(self.run)
        self.assertStop(out, 'no strategies')

    # ----------------------------------------------------------- judge
    def test_judge_accept_reject_rejudge(self):
        ids = [x['id'] for x in engine.next_round(self.run)['candidates']]
        self.assertEqual(len(ids), 1)
        a = ids[0]
        c = Candidate.objects.get(run=self.run, title__startswith='Garvee').pk
        res = engine.judge(self.run, [a], {c: 'x' * 500})
        self.assertEqual(res, {'accepted': 1, 'target': 10, 'done': False, 'pending': 0})
        self.assertEqual(Candidate.objects.get(pk=c).verdict_reason, 'x' * 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.step, 'querying-catalog')
        res = engine.judge(self.run, [], {a: 'changed my mind'})
        self.assertEqual((res['accepted'], res['pending']), (0, 0))
        self.assertEqual(Candidate.objects.get(pk=a).verdict, 'rejected')
        self.assertEqual(Candidate.objects.get(pk=a).verdict_reason, 'changed my mind')
        res = engine.judge(self.run, [a], {c: 'changed my mind'})
        self.assertEqual((res['accepted'], res['pending']), (1, 0))
        self.assertEqual(Candidate.objects.get(pk=c).verdict, 'rejected')
        self.assertEqual(Candidate.objects.get(pk=a).verdict, 'accepted')
        self.assertEqual(Candidate.objects.get(pk=a).verdict_reason, '')

    def test_judge_done_flag(self):
        out = engine.next_round(self.run)
        SearchRun.objects.filter(pk=self.run.pk).update(target=1)
        self.run.refresh_from_db()
        res = engine.judge(self.run, [out['candidates'][0]['id']])
        self.assertTrue(res['done'])

    def test_judge_rejects_foreign_ids_atomically(self):
        out = engine.next_round(self.run)
        good = out['candidates'][0]['id']
        other_run = engine.create_run(make_object(id='other'))
        engine.next_round(other_run)
        foreign = Candidate.objects.filter(run=other_run).first().pk
        with self.assertRaisesMessage(engine.SearcherError, 'not in this run'):
            engine.judge(self.run, [good], {foreign: 'nope'})
        with self.assertRaises(engine.SearcherError):
            engine.judge(self.run, [good, 999999])
        self.assertEqual(Candidate.objects.get(pk=good).verdict, Verdict.PENDING)
        self.assertEqual(Candidate.objects.get(pk=foreign).verdict, Verdict.PENDING)

    def test_judge_cannot_accept_filtered_out(self):
        out = engine.next_round(self.run)
        good = out['candidates'][0]['id']
        bad = Candidate.objects.get(run=self.run, title__startswith='Garvee')
        with self.assertRaisesMessage(engine.SearcherError, 'failed hard filters'):
            engine.judge(self.run, [good, bad.pk])
        self.assertEqual(Candidate.objects.get(pk=good).verdict, Verdict.PENDING)  # atomic
        engine.judge(self.run, [], {bad.pk: 'too big'})  # rejecting one is fine
        self.assertEqual(Candidate.objects.get(pk=bad.pk).verdict, Verdict.REJECTED)

    def test_judge_same_id_both_ways(self):
        out = engine.next_round(self.run)
        i = out['candidates'][0]['id']
        with self.assertRaises(engine.SearcherError):
            engine.judge(self.run, [i], {i: 'both'})

    def test_status(self):
        engine.next_round(self.run)
        s = engine.status(self.run)
        self.assertEqual((s['status'], s['step'], s['round'], s['accepted'], s['target'], s['pending']),
                         ('running', 'querying-catalog', 1, 0, 10, 1))
        self.assertIn('relax-style', s['strategies_left'])
        self.assertEqual(s['object']['name'], 'Coffee table')

    # -------------------------------------------------- deterministic
    def test_deterministic_done(self):
        SearchRun.objects.filter(pk=self.run.pk).update(target=2)
        self.run.refresh_from_db()
        res = engine.run_deterministic(self.run)
        # Page 1 holds one verified fit; rounds 2-5 repeat page 1; round 6 pages
        # ahead but nothing else in the fixture passes the hard size cap.
        self.assertEqual((res['status'], res['accepted'], res['rounds_used']), ('done', 1, 6))
        self.assertEqual(res['unverified'], 0)
        self.run.refresh_from_db()
        self.assertEqual((self.run.status, self.run.step), ('done', None))
        self.assertIsNotNone(self.run.finished_at)
        accepted = list(Candidate.objects.filter(run=self.run, verdict='accepted'))
        self.assertEqual(res['fits'], len(accepted))
        sellers = {c.seller_domain or c.seller_name for c in accepted}
        self.assertEqual(len(sellers), len(accepted))
        self.assertEqual(Candidate.objects.get(run=self.run, title__startswith='39"').verdict, 'accepted')

    def test_deterministic_runs_until_target_or_stop(self):
        res = engine.run_deterministic(self.run)  # target 10, only verified fits in fixture
        self.assertEqual(res['status'], 'done')
        self.assertEqual(res['unverified'], 0)
        self.assertGreaterEqual(res['accepted'], 1)
        self.assertLess(res['accepted'], res['target'])
        self.assertEqual(res['stopped'], 'no strategies left')
        self.assertLessEqual(res['rounds_used'], 12)

    def test_deterministic_empty(self):
        run = engine.create_run(make_object(id='e', name='thing EMPTY', style=None, material=None, similar_items=[]))
        res = engine.run_deterministic(run)
        self.assertEqual((res['status'], res['accepted']), ('empty', 0))
        run.refresh_from_db()
        self.assertEqual((run.status, run.step), ('empty', None))
        self.assertIsNotNone(run.finished_at)

    def test_deterministic_all_errors_fails(self):
        run = engine.create_run(make_object(id='c', name='CRASH', style=None, material=None, similar_items=[]))
        res = engine.run_deterministic(run)
        self.assertEqual(res['status'], 'failed')
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertTrue(run.error)

    def test_deterministic_leaves_canceled_run_alone(self):
        run = engine.create_run(make_object(id='x', name='EMPTY x'))
        def cancel(r, strategy=None):
            SearchRun.objects.filter(pk=r.pk).update(status='canceled')
            return {'stop': True, 'reason': 'run is not running'}

        with mock.patch.object(engine, 'next_round', cancel):
            engine.run_deterministic(run)
        run.refresh_from_db()
        self.assertEqual(run.status, 'canceled')


class UcpClientTests(TestCase):
    @fake_ucp
    def test_temp_file_is_deleted_and_argv_is_a_list(self):
        seen = {}
        real_run = __import__('subprocess').run

        def spy(argv, **kw):
            seen['argv'], seen['kw'] = argv, kw
            seen['path'] = argv[argv.index('--input') + 1][1:]
            seen['existed'] = os.path.exists(seen['path'])
            return real_run(argv, **kw)

        with mock.patch('api.searcher.ucp_client.subprocess.run', spy):
            env = ucp_client.search_catalog({'query': 'coffee table; rm -rf / $(x)'})
        self.assertTrue(seen['existed'])
        self.assertFalse(os.path.exists(seen['path']))
        self.assertIsInstance(seen['argv'], list)
        self.assertFalse(seen['kw'].get('shell'))
        self.assertEqual(seen['argv'][:3], [FAKE, 'catalog', 'search'])
        self.assertEqual(seen['argv'][-2:], ['--format', 'json'])
        self.assertIn('products', env['result'])

    @fake_ucp
    def test_error_codes(self):
        for q, code in [('crash', 'exit_2'), ('garbage', 'invalid_json'), ('errenv', 'no_result')]:
            with self.assertRaises(ucp_client.UcpError) as cm:
                ucp_client.search_catalog({'query': q})
            self.assertEqual(cm.exception.code, code)

    @override_settings(SEARCHER_UCP_BIN=FAKE, SEARCHER_UCP_TIMEOUT=0.01)
    def test_timeout_deletes_temp_file(self):
        import subprocess
        paths = []
        real = subprocess.run

        def slow(argv, **kw):
            paths.append(argv[argv.index('--input') + 1][1:])
            raise subprocess.TimeoutExpired(argv, kw['timeout'])

        with mock.patch('api.searcher.ucp_client.subprocess.run', slow):
            with self.assertRaises(ucp_client.UcpError) as cm:
                ucp_client.search_catalog({'query': 'x'})
        self.assertEqual(cm.exception.code, 'timeout')
        self.assertFalse(os.path.exists(paths[0]))


@fake_ucp
class CommandTests(TestCase):
    def setUp(self):
        self.obj = make_object()

    def run_cmd(self, *args, run_id=None, expect_exit=None):
        out = StringIO()
        env = {k: v for k, v in os.environ.items() if k != 'SEARCH_RUN_ID'}
        if run_id:
            env['SEARCH_RUN_ID'] = str(run_id)
        with mock.patch.dict(os.environ, env, clear=True):
            if expect_exit:
                with self.assertRaises(SystemExit) as cm:
                    call_command('searcher', *args, stdout=out)
                self.assertEqual(cm.exception.code, expect_exit)
            else:
                call_command('searcher', *args, stdout=out)
        return json.loads(out.getvalue())

    def test_full_flow_reads_run_from_env(self):
        run_id = self.run_cmd('start', '--object', 'obj1')['run']
        self.assertTrue(SearchRun.objects.filter(pk=run_id).exists())
        nxt = self.run_cmd('next', run_id=run_id)
        self.assertEqual(nxt['strategy'], 'exact')
        ids = [c['id'] for c in nxt['candidates']]
        self.assertEqual(len(ids), 1)  # page 1 holds one verified fit
        # Reject two filtered-out candidates to exercise --reject parsing.
        garvee = Candidate.objects.get(run_id=run_id, title__startswith='Garvee').pk
        onyx = Candidate.objects.get(run_id=run_id, title__startswith='Onyx').pk
        res = self.run_cmd('judge', '--accept', str(ids[0]),
                           '--reject', f'{garvee}=wrong type', f'{onyx}="coffee table book"',
                           run_id=run_id)
        self.assertEqual((res['accepted'], res['pending']), (1, 0))
        self.assertEqual(Candidate.objects.get(pk=garvee).verdict_reason, 'wrong type')
        self.assertEqual(Candidate.objects.get(pk=onyx).verdict_reason, 'coffee table book')
        st = self.run_cmd('status', run_id=run_id)
        self.assertEqual((st['round'], st['accepted']), (1, 1))
        self.assertEqual(self.run_cmd('next', '--strategy', 'broad', run_id=run_id)['strategy'], 'broad')
        auto = self.run_cmd('auto', run_id=run_id)
        self.assertEqual(auto['status'], 'done')
        self.assertGreaterEqual(auto['accepted'], 1)

    def test_errors_are_json_with_exit_1(self):
        for args in (['next'], ['status'], ['auto'], ['judge', '--accept', '1']):
            err = self.run_cmd(*args, expect_exit=1)
            self.assertIn('SEARCH_RUN_ID', err['error'])
        err = self.run_cmd('next', run_id='not-a-uuid', expect_exit=1)
        self.assertIn('UUID', err['error'])
        err = self.run_cmd('next', run_id='7b1cb3b4-0c53-4b4e-9d5e-0a1a2b3c4d5e', expect_exit=1)
        self.assertIn('not found', err['error'])
        err = self.run_cmd('start', '--object', 'missing', expect_exit=1)
        self.assertIn('not found', err['error'])
        run_id = self.run_cmd('start', '--object', 'obj1')['run']
        err = self.run_cmd('next', '--strategy', 'bogus', run_id=run_id, expect_exit=1)
        self.assertIn('exact', err['available'])
        err = self.run_cmd('judge', '--accept', '424242', run_id=run_id, expect_exit=1)
        self.assertIn('not in this run', err['error'])
        err = self.run_cmd('judge', '--reject', 'abc', run_id=run_id, expect_exit=1)
        self.assertIn('ID=reason', err['error'])

    def test_no_run_flag(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('searcher', 'next', '--run', 'x', stderr=StringIO())


@fake_ucp
class StepTests(TestCase):
    def setUp(self):
        self.obj = make_object()
        self.run = engine.create_run(self.obj)

    def stored(self):
        return SearchRun.objects.get(pk=self.run.pk).step

    def obj_step(self):
        return SpaceObject.objects.get(pk=self.obj.pk).search.get('step')

    def test_step_stays_querying_catalog_after_non_final_round_and_judge(self):
        out = engine.next_round(self.run)
        self.assertEqual(self.stored(), 'querying-catalog')
        self.assertEqual(self.obj_step(), 'querying-catalog')
        engine.judge(self.run, [out['candidates'][0]['id']])
        self.assertEqual(self.stored(), 'querying-catalog')
        self.assertEqual(self.obj_step(), 'querying-catalog')
        engine.next_round(self.run, 'more')
        self.assertEqual(self.stored(), 'querying-catalog')

    def test_ranking_when_judge_reaches_target(self):
        SearchRun.objects.filter(pk=self.run.pk).update(target=1)
        self.run.refresh_from_db()
        out = engine.next_round(self.run)
        self.assertEqual(self.stored(), 'querying-catalog')
        engine.judge(self.run, [out['candidates'][0]['id']])
        self.assertEqual(self.stored(), 'ranking')
        self.assertEqual(self.obj_step(), 'ranking')

    def test_ranking_when_last_round_used(self):
        SearchRun.objects.filter(pk=self.run.pk).update(max_rounds=1)
        self.run.refresh_from_db()
        engine.next_round(self.run)
        self.assertEqual(self.stored(), 'ranking')
        self.assertEqual(self.obj_step(), 'ranking')

    def test_ranking_when_last_round_errors(self):
        SearchRun.objects.filter(pk=self.run.pk).update(max_rounds=1)
        self.run.refresh_from_db()
        with override_settings(SEARCHER_UCP_BIN='/nonexistent/ucp'):
            out = engine.next_round(self.run)
        self.assertIn('error', out)
        self.assertEqual(self.stored(), 'ranking')

    def test_ranking_when_judge_uses_last_round(self):
        SearchRun.objects.filter(pk=self.run.pk).update(max_rounds=1)
        self.run.refresh_from_db()
        out = engine.next_round(self.run)
        SearchRun.objects.filter(pk=self.run.pk).update(step='querying-catalog')
        engine.judge(self.run, [out['candidates'][0]['id']])
        self.assertEqual(self.stored(), 'ranking')

    def test_stop_on_running_run_sets_ranking(self):
        SearchRun.objects.filter(pk=self.run.pk).update(max_rounds=0)
        out = engine.next_round(self.run)
        self.assertTrue(out['stop'])
        self.assertEqual(self.stored(), 'ranking')
        self.assertEqual(self.obj_step(), 'ranking')

    def test_stop_on_target_reached_sets_ranking(self):
        SearchRun.objects.filter(pk=self.run.pk).update(target=0)
        out = engine.next_round(self.run)
        self.assertEqual(out['reason'], 'target reached')
        self.assertEqual(self.stored(), 'ranking')

    def test_stop_on_non_running_run_leaves_step(self):
        SearchRun.objects.filter(pk=self.run.pk).update(status='canceled', step=None)
        out = engine.next_round(self.run)
        self.assertTrue(out['stop'])
        self.assertIsNone(self.stored())

    def test_set_step_never_goes_backwards(self):
        self.assertTrue(engine._set_step(self.run, 'querying-catalog'))
        self.assertFalse(engine._set_step(self.run, 'building-query'))
        self.assertFalse(engine._set_step(self.run, 'planning'))
        self.assertFalse(engine._set_step(self.run, 'querying-catalog'))
        self.assertEqual((self.stored(), self.run.step), ('querying-catalog', 'querying-catalog'))
        self.assertTrue(engine._set_step(self.run, 'ranking'))
        self.assertFalse(engine._set_step(self.run, 'querying-catalog'))
        self.assertEqual((self.stored(), self.run.step), ('ranking', 'ranking'))

    def test_set_step_from_null_and_stale_instance(self):
        SearchRun.objects.filter(pk=self.run.pk).update(step=None)
        self.assertTrue(engine._set_step(self.run, 'planning'))
        # A stale in-memory step is corrected from the stored value.
        SearchRun.objects.filter(pk=self.run.pk).update(step='ranking')
        self.run.step = 'planning'
        self.assertFalse(engine._set_step(self.run, 'querying-catalog'))
        self.assertEqual(self.run.step, 'ranking')

    def test_set_step_skips_unchanged_sync(self):
        with mock.patch.object(engine, 'sync_object_search') as sync:
            engine._set_step(self.run, 'querying-catalog')
            engine._set_step(self.run, 'querying-catalog')
            engine._set_step(self.run, 'planning')
        self.assertEqual(sync.call_count, 1)

    def test_set_step_never_writes_on_non_running_run(self):
        for st in ['queued', 'done', 'empty', 'failed', 'canceled']:
            SearchRun.objects.filter(pk=self.run.pk).update(status=st, step=None)
            self.assertFalse(engine._set_step(self.run, 'ranking'))
            self.assertIsNone(self.stored())
