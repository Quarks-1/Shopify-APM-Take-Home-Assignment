"""Search endpoints. Workers are never really spawned (Popen is mocked)."""
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    Candidate,
    CatalogQuery,
    Placement,
    RunStatus,
    SearchMode,
    SearchRun,
    Space,
    SpaceObject,
    Verdict,
)
from .searcher import engine, runner

FAKE_UCP = str(Path(__file__).parent / 'test_fixtures' / 'fake_ucp.py')

RUN_KEYS = {
    'id', 'objectId', 'mode', 'status', 'step', 'round', 'maxRounds', 'accepted', 'target', 'partial',
    'createdAt', 'startedAt', 'finishedAt', 'error', 'agent', 'queries', 'candidates',
}
AGENT_KEYS = {'mode', 'model', 'turns', 'sessionId', 'outcome', 'notes', 'fallbackReason',
              'deniedCommands'}


def make_object(oid='obj1', **kw):
    fields = dict(
        id=oid, name='Coffee table', dims={'x': 46, 'y': 24, 'z': 17}, type='furniture',
        material='wood', style='mid-century', price_limit=400, similar_items=['cocktail table'],
        created_at=timezone.now(),
    )
    fields.update(kw)
    return SpaceObject.objects.create(**fields)


@override_settings(SEARCHER_UCP_BIN=FAKE_UCP)
class SearchApiTests(TestCase):
    def setUp(self):
        var = tempfile.mkdtemp(prefix='searcher-var-')
        self.addCleanup(shutil.rmtree, var, True)
        cm = override_settings(SEARCHER_VAR_DIR=Path(var))
        cm.enable()
        self.addCleanup(cm.disable)
        # Workers are stubbed (subprocess itself stays real: the fake ucp needs it).
        self.popen = mock.patch.object(
            runner, '_spawn_worker',
            side_effect=lambda run_id: SearchRun.objects.filter(pk=run_id).update(worker_pid=70001),
        ).start()
        mock.patch.object(runner, '_pid_alive', return_value=True).start()
        self.killpg = mock.patch.object(runner.os, 'killpg').start()
        self.addCleanup(mock.patch.stopall)
        self.obj = make_object()

    def post(self, path, body=None):
        return self.client.post(path, data=json.dumps(body) if body is not None else '',
                                content_type='application/json')

    def judged_run(self, obj=None):
        """A finished run: accepted, rejected and filtered-out candidates."""
        run = engine.create_run(obj or self.obj)
        engine.next_round(run)
        engine.next_round(run, 'more')
        passing = list(Candidate.objects.filter(run=run, passes_filters=True).order_by('pk'))
        accept = [c.pk for c in passing[:3]]
        # Reject the next passing candidate, else the first filtered-out one that
        # is not too big (filtered-out candidates may be rejected, never accepted).
        rest = (Candidate.objects.filter(run=run).exclude(pk__in=accept)
                .exclude(size_status='too_big').order_by('-passes_filters', 'pk'))
        reject = {rest[0].pk: 'a book, not a table'}
        engine.judge(run, accept, reject)
        runner.finalize(run)
        return run, [c.pk for c in passing]

    # ------------------------------------------------------------ start
    def test_start_returns_202_then_200(self):
        r = self.post('/api/objects/obj1/searches/')
        self.assertEqual(r.status_code, 202, r.content)
        body = r.json()
        self.assertEqual(set(body), RUN_KEYS)
        self.assertEqual(set(body['agent']), AGENT_KEYS)
        self.assertEqual((body['objectId'], body['status'], body['step']), ('obj1', 'running', 'planning'))
        self.assertEqual((body['round'], body['accepted'], body['partial']), (0, 0, False))
        self.assertEqual((body['target'], body['maxRounds']), (10, 12))
        self.assertEqual((body['queries'], body['candidates'], body['error']), ([], [], None))
        self.assertIsNone(body['finishedAt'])
        self.assertEqual(self.popen.call_count, 1)

        again = self.post('/api/objects/obj1/searches/')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['id'], body['id'])
        self.assertEqual(self.popen.call_count, 1)
        obj = self.client.get('/api/objects/obj1/').json()
        self.assertEqual(obj['search'], {'status': 'running', 'step': 'planning'})

    def test_start_errors(self):
        make_object('owned', source='imported')
        r = self.post('/api/objects/owned/searches/')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'imported_object')
        self.assertEqual(self.post('/api/objects/nope/searches/').status_code, 404)
        self.assertEqual(self.client.get('/api/objects/obj1/searches/').status_code, 405)
        self.assertEqual(SearchRun.objects.count(), 0)

    def test_force_cancels_active_and_uses_fresh_snapshot(self):
        first = self.post('/api/objects/obj1/searches/').json()
        SearchRun.objects.filter(pk=first['id']).update(agent_pid=333)

        # Without force (or force=False), active run is reused with 200.
        reused = self.post('/api/objects/obj1/searches/', {'force': False})
        self.assertEqual((reused.status_code, reused.json()['id']), (200, first['id']))
        self.assertEqual(self.popen.call_count, 1)

        # Update object constraints, then force a fresh search.
        patch_res = self.client.patch(
            '/api/objects/obj1/',
            data=json.dumps({'name': 'Updated table', 'priceLimit': 250}),
            content_type='application/json',
        )
        self.assertEqual(patch_res.status_code, 200)

        forced = self.post('/api/objects/obj1/searches/', {'force': True})
        self.assertEqual(forced.status_code, 202, forced.content)
        second = forced.json()
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(SearchRun.objects.get(pk=first['id']).status, 'canceled')
        self.assertIn(mock.call(333, 15), self.killpg.call_args_list)
        self.assertEqual(self.popen.call_count, 2)

        fresh_run = SearchRun.objects.get(pk=second['id'])
        self.assertEqual(fresh_run.object_snapshot['name'], 'Updated table')
        self.assertEqual(fresh_run.object_snapshot['priceLimit'], 250)

    def test_force_validation(self):
        run, ids = self.judged_run()
        for bad in (
            {'force': 'yes'},
            {'force': 1},
            {'force': None},
            {'mode': 'refine', 'force': True},
            {'mode': 'like', 'candidateId': ids[0], 'force': True},
        ):
            r = self.post('/api/objects/obj1/searches/', bad)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'validation_failed'), bad)

    # ----------------------------------------------------------- theme order
    def themed_run(self):
        run, ids = self.judged_run()
        accepted = list(Candidate.objects.filter(run=run, verdict=Verdict.ACCEPTED).order_by('pk'))
        plain, zebra = accepted[0], accepted[-1]
        Candidate.objects.filter(pk=plain.pk).update(title='Plain Chair', description='', price_cents=9900)
        Candidate.objects.filter(pk=zebra.pk).update(title='Zebra Print Chair', price_cents=57000)
        return run, plain, zebra

    def test_theme_orders_themed_first_in_latest_and_state(self):
        run, plain, zebra = self.themed_run()
        before = [c['id'] for c in self.client.get('/api/objects/obj1/searches/latest/').json()['candidates']]
        self.assertEqual(before[0], plain.pk)
        SearchRun.objects.filter(pk=run.pk).update(vibes={'theme': {
            'family': 'animal print', 'patterns': ['zebra print'], 'pins': 3, 'boardPins': 5}})
        latest = [c['id'] for c in self.client.get('/api/objects/obj1/searches/latest/').json()['candidates']]
        self.assertEqual(latest[0], zebra.pk)
        state = [c['id'] for c in self.state_candidates()]
        self.assertEqual(state, latest)

    def test_malformed_theme_is_ignored(self):
        run, plain, _ = self.themed_run()
        SearchRun.objects.filter(pk=run.pk).update(vibes={'theme': 'nope'})
        latest = self.client.get('/api/objects/obj1/searches/latest/').json()['candidates']
        self.assertEqual(latest[0]['id'], plain.pk)
        SearchRun.objects.filter(pk=run.pk).update(vibes=['x'])
        self.assertEqual(self.client.get('/api/state/').status_code, 200)

    # ----------------------------------------------------------- latest
    def test_latest(self):
        self.assertEqual(self.client.get('/api/objects/obj1/searches/latest/').status_code, 404)
        self.assertEqual(self.client.get('/api/objects/nope/searches/latest/').status_code, 404)
        old, _ = self.judged_run()
        new = self.post('/api/objects/obj1/searches/').json()
        r = self.client.get('/api/objects/obj1/searches/latest/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['id'], new['id'])
        self.assertNotEqual(new['id'], str(old.pk))

    def test_polling_queued_run_promotes_it(self):
        run = engine.create_run(self.obj, status='queued')
        self.assertEqual(SearchRun.objects.get(pk=run.pk).status, 'queued')
        r = self.client.get(f'/api/searches/{run.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'running')
        self.popen.assert_called_once_with(run.pk)
        self.assertEqual(SpaceObject.objects.get(pk=self.obj.pk).search['status'], 'running')

    def test_polling_queued_run_survives_dispatch_failure(self):
        run = engine.create_run(self.obj, status='queued')
        with mock.patch.object(runner, 'dispatch', side_effect=RuntimeError('boom')), \
                self.assertLogs('api.search_views', 'ERROR'):
            r = self.client.get(f'/api/searches/{run.pk}/')
        self.assertEqual((r.status_code, r.json()['status']), (200, 'queued'))

    # ----------------------------------------------------------- detail
    def test_detail_shape_and_default_candidates(self):
        run, ids = self.judged_run()
        r = self.client.get(f'/api/searches/{run.pk}/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['status'], 'done')
        accepted_n = min(3, Candidate.objects.filter(run=run, passes_filters=True).count())
        self.assertEqual((body['accepted'], body['target'], body['partial']), (accepted_n, 10, True))
        self.assertEqual(body['queries'][0], {
            'round': 1, 'strategy': 'exact', 'query': 'mid-century wood Coffee table',
            'returned': 5, 'new': 5, 'passing': 1, 'error': None,
        })
        cands = body['candidates']
        self.assertEqual(len(cands), accepted_n)
        self.assertTrue(all(c['verdict'] == 'accepted' for c in cands))
        self.assertTrue(all(c['sizeStatus'] == 'fits' for c in cands))
        domains = [c['seller']['domain'] for c in cands if c['seller']['domain']]
        if len(domains) > 1:
            self.assertEqual(len(domains), len(set(domains)))
        c = next(c for c in cands if c['sizeStatus'] == 'fits')
        for key in ('id', 'objectId', 'runId', 'productId', 'variantId', 'title', 'seller', 'price',
                    'currency', 'url', 'checkoutUrl', 'imageUrl', 'sizeStatus', 'weightStatus',
                    'materials', 'verdict', 'verdictReason', 'filterReasons', 'query'):
            self.assertIn(key, c)
        self.assertEqual((c['objectId'], c['runId']), ('obj1', str(run.pk)))
        self.assertEqual(set(c['seller']), {'name', 'domain', 'url'})
        self.assertIsInstance(c['price'], float)
        self.assertEqual(c['currency'], 'USD')
        self.assertEqual(c['query'], 1)
        self.assertIn('dims', c)
        db = Candidate.objects.get(pk=c['id'])
        self.assertEqual(c['price'], db.price_cents / 100)
        self.assertEqual(c['productId'], db.product_gid)
        if db.rating_value is not None:
            self.assertEqual(c['rating'], {'value': db.rating_value, 'count': db.rating_count})

    def test_include_all(self):
        run, ids = self.judged_run()
        body = self.client.get(f'/api/searches/{run.pk}/?include=all').json()
        verdicts = [c['verdict'] for c in body['candidates']]
        # Both fixture pages (8 products): one verified fit is accepted, one
        # unverified product is rejected, the other 6 stay pending (filtered out).
        self.assertEqual(len(verdicts), 8)
        self.assertEqual(verdicts[0], 'accepted')
        self.assertEqual(sorted(verdicts[1:]), ['pending'] * 6 + ['rejected'])
        rejected = next(c for c in body['candidates'] if c['verdict'] == 'rejected')
        self.assertEqual(rejected['verdictReason'], 'a book, not a table')
        pending = [c for c in body['candidates'] if c['verdict'] == 'pending']
        self.assertTrue(all(c['filterReasons'] for c in pending))
        filtered = next(c for c in pending if c['sizeStatus'] == 'too_big')
        self.assertTrue(filtered['filterReasons'][0].startswith('too large'))
        self.assertIn(['size unverified'], [c['filterReasons'] for c in pending])
        self.assertEqual(body['accepted'], 1)
        latest = self.client.get('/api/objects/obj1/searches/latest/?include=all').json()
        self.assertEqual(len(latest['candidates']), 8)

    def test_agent_block(self):
        run, _ = self.judged_run()
        SearchRun.objects.filter(pk=run.pk).update(
            agent_mode='deterministic', agent_model='haiku', fallback_reason='agent timed out after 420s',
            agent_result={'num_turns': 7, 'session_id': 's-1', 'permission_denials': [
                {'tool_name': 'Bash', 'tool_input': {'command': 'searcher auto'}}, 'odd'],
                'structured_output': {'outcome': 'gave_up', 'accepted': 3, 'notes': 'n'}},
        )
        agent = self.client.get(f'/api/searches/{run.pk}/').json()['agent']
        self.assertEqual(agent, {
            'mode': 'deterministic', 'model': 'haiku', 'turns': 7, 'sessionId': 's-1',
            'outcome': 'gave_up', 'notes': 'n', 'fallbackReason': 'agent timed out after 420s',
            'deniedCommands': ['searcher auto', 'odd'],
        })

    def test_detail_404(self):
        self.assertEqual(self.client.get(f'/api/searches/{uuid.uuid4()}/').status_code, 404)
        self.assertEqual(self.client.get('/api/searches/not-a-uuid/').status_code, 404)
        self.assertEqual(self.post(f'/api/searches/{uuid.uuid4()}/cancel/').status_code, 404)

    # ----------------------------------------------------------- cancel
    def test_cancel(self):
        run_id = self.post('/api/objects/obj1/searches/').json()['id']
        SearchRun.objects.filter(pk=run_id).update(agent_pid=222)
        r = self.post(f'/api/searches/{run_id}/cancel/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body['status'], body['step']), ('canceled', None))
        self.assertIsNotNone(body['finishedAt'])
        self.assertIn(mock.call(222, 15), self.killpg.call_args_list)
        again = self.post(f'/api/searches/{run_id}/cancel/')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['finishedAt'], body['finishedAt'])
        self.assertEqual(self.client.get(f'/api/searches/{run_id}/cancel/').status_code, 405)
        self.assertEqual(self.client.get('/api/objects/obj1/').json()['search']['status'], 'idle')

    # ----------------------------------------------------------- select
    def test_select_and_clear(self):
        run, ids = self.judged_run()
        r = self.post('/api/objects/obj1/select/', {'candidateId': ids[0]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['selectedCandidateId'], ids[0])
        self.assertEqual(self.client.get('/api/objects/obj1/').json()['selectedCandidateId'], ids[0])
        r = self.post('/api/objects/obj1/select/', {'candidateId': None})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('selectedCandidateId', r.json())

    def test_select_validation(self):
        run, ids = self.judged_run()
        other_run, other_ids = self.judged_run(make_object('obj2'))
        pending = Candidate.objects.filter(run=run, verdict=Verdict.PENDING).first().pk
        rejected = Candidate.objects.get(run=run, verdict=Verdict.REJECTED).pk

        def details(body, status=400):
            r = self.post('/api/objects/obj1/select/', body)
            self.assertEqual(r.status_code, status, r.content)
            return r.json().get('details', {})

        self.assertIn('not accepted', details({'candidateId': rejected})['candidateId'])
        self.assertIn('not accepted', details({'candidateId': pending})['candidateId'])
        self.assertIn('not a candidate', details({'candidateId': other_ids[0]})['candidateId'])
        self.assertIn('not a candidate', details({'candidateId': 999999})['candidateId'])
        self.assertIn('integer', details({'candidateId': True})['candidateId'])
        self.assertIn('integer', details({'candidateId': '5'})['candidateId'])
        self.assertIn('integer', details({'candidateId': 1.5})['candidateId'])
        self.assertIn('candidateId', details({}))
        self.assertIn('extra', details({'candidateId': None, 'extra': 1}))
        self.assertEqual(self.post('/api/objects/nope/select/', {'candidateId': None}).status_code, 404)
        r = self.client.post('/api/objects/obj1/select/', data='{bad', content_type='application/json')
        self.assertEqual(r.json(), {'error': 'invalid_json'})
        self.assertIsNone(SpaceObject.objects.get(pk='obj1').selected_candidate_id)

    def test_selected_candidate_id_is_read_only_in_patch_and_import(self):
        run, ids = self.judged_run()
        self.post('/api/objects/obj1/select/', {'candidateId': ids[0]})
        other_id = Candidate.objects.filter(run=run).exclude(pk=ids[0]).first().pk
        fetched = self.client.get('/api/objects/obj1/').json()
        fetched['name'] = 'Coffee table 2'
        fetched['selectedCandidateId'] = other_id  # ignored
        body = {k: v for k, v in fetched.items() if k not in ('id', 'createdAt')}  # never patchable
        r = self.client.patch('/api/objects/obj1/', data=json.dumps(body),
                              content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['name'], r.json()['selectedCandidateId']), ('Coffee table 2', ids[0]))
        r = self.client.patch('/api/objects/obj1/', data=json.dumps({'selectedCandidateId': None}),
                              content_type='application/json')
        self.assertEqual(r.json()['selectedCandidateId'], ids[0])

        snapshot = self.client.get('/api/state/').json()
        snapshot['version'] = 1
        snapshot['objects'][0]['selectedCandidateId'] = 424242  # ignored
        r = self.post('/api/state/import/', snapshot)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(SpaceObject.objects.get(pk='obj1').selected_candidate_id, ids[0])

        r = self.post('/api/objects/', {'id': 'new', 'name': 'n', 'type': 'rug', 'dims': {'x': 1},
                                        'selectedCandidateId': ids[0]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn('selectedCandidateId', r.json())

    def test_selection_cleared_when_candidate_goes(self):
        run, ids = self.judged_run()
        self.post('/api/objects/obj1/select/', {'candidateId': ids[0]})
        run.delete()
        self.assertNotIn('selectedCandidateId', self.client.get('/api/objects/obj1/').json())

    # --------------------------------------------------------- overview
    def test_overview_lists_search_routes(self):
        endpoints = self.client.get('/api/overview/').json()['endpoints']
        routes = {(e['method'], e['path']) for e in endpoints}
        for route in [('POST', '/api/objects/<id>/searches/'),
                      ('GET', '/api/objects/<id>/searches/latest/'),
                      ('POST', '/api/objects/<id>/select/'),
                      ('GET', '/api/searches/<id>/'),
                      ('POST', '/api/searches/<id>/cancel/')]:
            self.assertIn(route, routes)

    # ------------------------------------------------------------ state
    def state_candidates(self):
        return self.client.get('/api/state/').json()['candidates']

    def test_state_candidates_follow_newest_accepted_run(self):
        self.assertEqual(self.state_candidates(), [])
        make_object('obj2', name='Rug', type='rug', dims={'x': 96, 'y': 120})  # never searched
        old, old_ids = self.judged_run()
        run_cands = self.client.get(f'/api/searches/{old.pk}/').json()['candidates']
        self.assertEqual(self.state_candidates(), run_cands)
        self.assertEqual({(c['objectId'], c['runId']) for c in run_cands}, {('obj1', str(old.pk))})

        # A plain search run clears any prior selection when it finishes.
        self.post('/api/objects/obj1/select/', {'candidateId': old_ids[0]})
        new, _ = self.judged_run()
        self.assertIsNone(SpaceObject.objects.get(pk='obj1').selected_candidate_id)

        # Selection from an older run comes last after the newest DONE run's candidates.
        self.post('/api/objects/obj1/select/', {'candidateId': old_ids[0]})
        new_cands = self.client.get(f'/api/searches/{new.pk}/').json()['candidates']
        state = self.state_candidates()
        self.assertEqual([c['id'] for c in state], [c['id'] for c in new_cands] + [old_ids[0]])
        self.assertEqual(state[-1]['runId'], str(old.pk))

        # A newer like/refine run without accepted candidates keeps both candidates and 'done'.
        empty = engine.create_run(self.obj, mode=SearchMode.LIKE, like_source={})
        runner.finalize(empty)
        self.assertEqual(SearchRun.objects.get(pk=empty.pk).status, 'empty')
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'done')
        self.assertEqual([c['id'] for c in self.state_candidates()], [c['id'] for c in state])

        # Selecting one of the newest run's candidates is not listed twice.
        self.post('/api/objects/obj1/select/', {'candidateId': new_cands[0]['id']})
        self.assertEqual([c['id'] for c in self.state_candidates()], [c['id'] for c in new_cands])

    def test_state_candidates_ignore_partial_non_done_runs(self):
        # Partial accepted candidates on running, canceled, or failed runs never leak into /api/state/.
        for status in (RunStatus.RUNNING, RunStatus.CANCELED, RunStatus.FAILED):
            run = engine.create_run(self.obj)
            cq = CatalogQuery.objects.create(run=run, round=1, strategy='exact', payload={'query': 'x'})
            Candidate.objects.create(
                run=run, query=cq, product_gid=f'gid://shopify/p/{status}',
                title=f'Partial {status}', verdict=Verdict.ACCEPTED,
            )
            SearchRun.objects.filter(pk=run.pk).update(status=status)
        self.assertEqual(self.state_candidates(), [])

        done_run, done_ids = self.judged_run()
        # A subsequent running or canceled run with partial accepted candidates still keeps done_run.
        for status in (RunStatus.RUNNING, RunStatus.CANCELED, RunStatus.FAILED):
            later = engine.create_run(self.obj)
            cq = CatalogQuery.objects.create(run=later, round=1, strategy='exact', payload={'query': 'x'})
            Candidate.objects.create(
                run=later, query=cq, product_gid=f'gid://shopify/p/later-{status}',
                title=f'Later {status}', verdict=Verdict.ACCEPTED,
            )
            SearchRun.objects.filter(pk=later.pk).update(status=status)
            self.assertEqual([c['id'] for c in self.state_candidates()], done_ids[:1])

    def test_empty_like_and_refine_keep_done_only_when_prior_matches_exist(self):
        # Without prior DONE matches, an empty refine after an empty search stays 'empty'.
        first_empty = engine.create_run(self.obj)
        runner.finalize(first_empty)
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'empty')

        refine_empty = engine.create_run(self.obj, mode=SearchMode.REFINE, feedback={})
        runner.finalize(refine_empty)
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'empty')

        # Once a DONE run has accepted candidates, empty like and refine runs preserve 'done'.
        self.judged_run()
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'done')

        for mode in (SearchMode.LIKE, SearchMode.REFINE):
            extra = {'like_source': {}} if mode == SearchMode.LIKE else {'feedback': {}}
            empty_followup = engine.create_run(self.obj, mode=mode, **extra)
            runner.finalize(empty_followup)
            self.assertEqual(SearchRun.objects.get(pk=empty_followup.pk).status, 'empty')
            self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'done')

        # A plain search run that finishes empty still flips search.status to 'empty'.
        plain_empty = engine.create_run(self.obj)
        runner.finalize(plain_empty)
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'empty')

    def test_state_candidates_in_object_order(self):
        second = make_object('obj0', position=5)
        self.judged_run(second)
        self.judged_run()
        objects = [o['id'] for o in self.client.get('/api/state/').json()['objects']]
        self.assertEqual(objects, ['obj1', 'obj0'])
        owners = [c['objectId'] for c in self.state_candidates()]
        self.assertEqual(owners, sorted(owners, key=objects.index))
        self.assertEqual(set(owners), {'obj1', 'obj0'})

    def test_state_query_count_is_constant(self):
        space = Space.objects.create(id='s1', name='Room', type='floor', unit='in',
                                     dims={'x': 100, 'y': 100}, created_at=timezone.now(),
                                     updated_at=timezone.now())
        Placement.objects.create(space=space, object=self.obj, x=0, y=0)
        _, ids = self.judged_run()
        accepted = len(ids)  # every verified fit in the fixture (1)
        with self.assertNumQueries(9):  # + votes, preferences, cart lines and merchant carts
            self.client.get('/api/state/')
        for i in range(3):
            obj = make_object(f'more{i}')
            run, ids = self.judged_run(obj)
            self.judged_run(obj)
            self.post(f'/api/objects/{obj.pk}/select/', {'candidateId': ids[0]})  # selection from older run
        with self.assertNumQueries(9):
            # obj1's accepted + per extra object: its accepted plus the older selection
            self.assertEqual(len(self.client.get('/api/state/').json()['candidates']),
                             accepted + 3 * (accepted + 1))
        self.assertEqual(accepted, 1)

    def test_detail_query_count_is_constant(self):
        run, _ = self.judged_run()
        with self.assertNumQueries(3):
            self.client.get(f'/api/searches/{run.pk}/?include=all')


class StaleReadTests(TestCase):
    """Polling a run whose worker died (e.g. after a reboot) reaps it."""

    def setUp(self):
        self.killpg = mock.patch.object(runner.os, 'killpg').start()
        self.addCleanup(mock.patch.stopall)
        self.run = engine.create_run(make_object())
        dead = subprocess.Popen(['true'])
        dead.wait()
        SearchRun.objects.filter(pk=self.run.pk).update(worker_pid=dead.pid)

    def test_detail_reaps_dead_worker(self):
        body = self.client.get(f'/api/searches/{self.run.pk}/').json()
        self.assertEqual(body['status'], 'failed')
        self.assertEqual(body['error'], 'worker exited unexpectedly')
        self.assertIsNotNone(body['finishedAt'])
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'idle')

    def test_latest_reaps_dead_worker(self):
        body = self.client.get('/api/objects/obj1/searches/latest/').json()
        self.assertEqual((body['id'], body['status']), (str(self.run.pk), 'failed'))

    def test_live_worker_is_left_alone(self):
        SearchRun.objects.filter(pk=self.run.pk).update(worker_pid=os.getpid())
        self.assertEqual(self.client.get(f'/api/searches/{self.run.pk}/').json()['status'], 'running')
