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
        self.assertEqual((r.target, r.max_rounds), (10, 8))
        self.assertEqual(r.object_snapshot['name'], 'Coffee table')
        self.assertEqual(r.object_snapshot['priceLimit'], 400)
        self.assertNotIn('space', r.object_snapshot)

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
        self.assertEqual((out['accepted'], out['target'], out['rounds_left']), (0, 10, 7))
        self.assertIn('more', out['strategies_left'])
        self.assertNotIn('exact', out['strategies_left'])

        self.run.refresh_from_db()
        self.assertEqual((self.run.round_count, self.run.step), (1, 'ranking'))
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
        by_title = {c['title']: c for c in out['candidates']}
        oval = by_title['39" Mid-Century Modern Oval Fluted Coffee Table with Curved Edges']
        self.assertEqual(oval['size'], '39.38 x 19.69 x 13.78 in (fits)')
        self.assertEqual(oval['price'], '$129.00')
        self.assertTrue(oval['seller'])
        self.assertIsInstance(oval['id'], int)
        self.assertLessEqual(len(oval['specs']), 6)
        self.assertLessEqual(len(oval['summary']), 200)
        longest = by_title['Mid-Century Modern Coffee Table - 48"']
        self.assertEqual(longest['size'], '~48 in longest (unverified)')
        self.assertEqual(by_title['Onyx Mid Century Modern Wood Coffee Table with Storage']['size'],
                         'unverified')
        self.assertEqual(set(oval), {'id', 'title', 'seller', 'price', 'size', 'weight',
                                     'materials', 'specs', 'summary'})
        # ids are real Candidate pks of this run
        self.assertEqual(Candidate.objects.filter(run=self.run, pk=oval['id']).count(), 1)

    def test_filter_reasons_stored_and_hidden_from_agent(self):
        out = engine.next_round(self.run)
        garvee = Candidate.objects.get(run=self.run, title__startswith='Garvee')
        self.assertFalse(garvee.passes_filters)
        self.assertEqual(garvee.size_status, 'too_big')
        self.assertIn('too large', garvee.filter_reasons[0])
        self.assertNotIn(garvee.pk, [c['id'] for c in out['candidates']])
        self.assertEqual(out['filtered_out'], 1)
        self.assertEqual(len(out['candidates']), 4)

    def test_dedupe_across_rounds(self):
        engine.next_round(self.run)
        out = engine.next_round(self.run)  # relax-style: fake returns the same first page
        self.assertEqual((out['strategy'], out['returned'], out['new'], out['passing']), ('relax-style', 5, 0, 0))
        self.assertEqual(out['candidates'], [])
        self.assertEqual(Candidate.objects.filter(run=self.run).count(), 5)
        self.assertEqual(Candidate.objects.filter(run=self.run, query__round=2).count(), 0)

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
        out = engine.next_round(self.run)
        ids = [c['id'] for c in out['candidates']][:2]
        SearchRun.objects.filter(pk=self.run.pk).update(target=2)
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
        out = engine.next_round(self.run)
        a, b, c, d = [x['id'] for x in out['candidates']]
        res = engine.judge(self.run, [a, b], {c: 'x' * 500})
        self.assertEqual(res, {'accepted': 2, 'target': 10, 'done': False, 'pending': 1})
        self.assertEqual(Candidate.objects.get(pk=c).verdict_reason, 'x' * 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.step, 'ranking')
        res = engine.judge(self.run, [c], {a: 'changed my mind'})  # re-judging allowed
        self.assertEqual((res['accepted'], res['pending']), (2, 1))
        self.assertEqual(Candidate.objects.get(pk=a).verdict, 'rejected')
        self.assertEqual(Candidate.objects.get(pk=c).verdict, 'accepted')
        self.assertEqual(Candidate.objects.get(pk=c).verdict_reason, '')

    def test_judge_done_flag(self):
        out = engine.next_round(self.run)
        SearchRun.objects.filter(pk=self.run.pk).update(target=2)
        self.run.refresh_from_db()
        res = engine.judge(self.run, [x['id'] for x in out['candidates']][:2])
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
                         ('running', 'ranking', 1, 0, 10, 4))
        self.assertIn('relax-style', s['strategies_left'])
        self.assertEqual(s['object']['name'], 'Coffee table')

    # -------------------------------------------------- deterministic
    def test_deterministic_done(self):
        SearchRun.objects.filter(pk=self.run.pk).update(target=3)
        self.run.refresh_from_db()
        res = engine.run_deterministic(self.run)
        self.assertEqual((res['status'], res['accepted'], res['rounds_used']), ('done', 3, 1))
        self.assertEqual(res['fits'] + res['unverified'], 3)
        self.run.refresh_from_db()
        self.assertEqual((self.run.status, self.run.step), ('done', None))
        self.assertIsNotNone(self.run.finished_at)
        # best first: the one `fits` candidate (39.38 oval) is accepted, then cheapest
        accepted = list(Candidate.objects.filter(run=self.run, verdict='accepted').order_by('price_cents'))
        self.assertEqual(res['fits'], 1)  # the one verified fit is always taken first
        self.assertEqual([a.price_cents for a in accepted], [9900, 12900, 14999])
        self.assertEqual(Candidate.objects.get(run=self.run, title__startswith='39"').verdict, 'accepted')

    def test_deterministic_runs_until_target_or_stop(self):
        res = engine.run_deterministic(self.run)  # target 10, only 7 passing products exist
        self.assertEqual(res['status'], 'done')
        self.assertEqual(res['accepted'], 7)
        self.assertEqual(res['stopped'], 'no strategies left')
        self.assertLessEqual(res['rounds_used'], 8)

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
        res = self.run_cmd('judge', '--accept', str(ids[0]), str(ids[1]),
                           '--reject', f'{ids[2]}=wrong type', f'{ids[3]}="coffee table book"',
                           run_id=run_id)
        self.assertEqual((res['accepted'], res['pending']), (2, 0))
        self.assertEqual(Candidate.objects.get(pk=ids[2]).verdict_reason, 'wrong type')
        self.assertEqual(Candidate.objects.get(pk=ids[3]).verdict_reason, 'coffee table book')
        st = self.run_cmd('status', run_id=run_id)
        self.assertEqual((st['round'], st['accepted']), (1, 2))
        self.assertEqual(self.run_cmd('next', '--strategy', 'broad', run_id=run_id)['strategy'], 'broad')
        auto = self.run_cmd('auto', run_id=run_id)
        self.assertEqual(auto['status'], 'done')
        self.assertGreaterEqual(auto['accepted'], 2)

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
