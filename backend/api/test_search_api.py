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

from .models import Candidate, Placement, SearchRun, Space, SpaceObject, Verdict
from .searcher import engine, runner

FAKE_UCP = str(Path(__file__).parent / 'test_fixtures' / 'fake_ucp.py')

RUN_KEYS = {
    'id', 'objectId', 'status', 'step', 'round', 'maxRounds', 'accepted', 'target', 'partial',
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
        """A finished run with one round: accepted, rejected and filtered-out candidates."""
        run = engine.create_run(obj or self.obj)
        out = engine.next_round(run)
        ids = [c['id'] for c in out['candidates']]
        engine.judge(run, ids[:3], {ids[3]: 'a book, not a table'})
        runner.finalize(run)
        return run, ids

    # ------------------------------------------------------------ start
    def test_start_returns_202_then_200(self):
        r = self.post('/api/objects/obj1/searches/')
        self.assertEqual(r.status_code, 202, r.content)
        body = r.json()
        self.assertEqual(set(body), RUN_KEYS)
        self.assertEqual(set(body['agent']), AGENT_KEYS)
        self.assertEqual((body['objectId'], body['status'], body['step']), ('obj1', 'running', 'planning'))
        self.assertEqual((body['round'], body['accepted'], body['partial']), (0, 0, False))
        self.assertEqual((body['target'], body['maxRounds']), (10, 8))
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

    # ----------------------------------------------------------- detail
    def test_detail_shape_and_default_candidates(self):
        run, ids = self.judged_run()
        r = self.client.get(f'/api/searches/{run.pk}/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['status'], 'done')
        self.assertEqual((body['accepted'], body['target'], body['partial']), (3, 10, True))
        self.assertEqual(body['queries'], [{
            'round': 1, 'strategy': 'exact', 'query': 'mid-century wood Coffee table',
            'returned': 5, 'new': 5, 'passing': 4, 'error': None,
        }])
        cands = body['candidates']
        self.assertEqual(len(cands), 3)
        self.assertTrue(all(c['verdict'] == 'accepted' for c in cands))
        # fits first, then unverified; cheapest first within each
        order = [(0 if c['sizeStatus'] == 'fits' else 1, c['price']) for c in cands]
        self.assertEqual(order, sorted(order))
        self.assertIn('fits', [c['sizeStatus'] for c in cands])
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
        self.assertEqual(len(verdicts), 5)
        self.assertEqual(verdicts[:3], ['accepted'] * 3)
        self.assertEqual(sorted(verdicts[3:]), ['pending', 'rejected'])
        rejected = next(c for c in body['candidates'] if c['verdict'] == 'rejected')
        self.assertEqual(rejected['verdictReason'], 'a book, not a table')
        filtered = next(c for c in body['candidates'] if c['verdict'] == 'pending')
        self.assertTrue(filtered['filterReasons'][0].startswith('too large'))
        self.assertEqual(filtered['sizeStatus'], 'too_big')
        self.assertEqual(body['accepted'], 3)
        latest = self.client.get('/api/objects/obj1/searches/latest/?include=all').json()
        self.assertEqual(len(latest['candidates']), 5)

    def test_agent_block(self):
        run, _ = self.judged_run()
        SearchRun.objects.filter(pk=run.pk).update(
            agent_mode='deterministic', agent_model='sonnet', fallback_reason='agent timed out after 420s',
            agent_result={'num_turns': 7, 'session_id': 's-1', 'permission_denials': [
                {'tool_name': 'Bash', 'tool_input': {'command': 'searcher auto'}}, 'odd'],
                'structured_output': {'outcome': 'gave_up', 'accepted': 3, 'notes': 'n'}},
        )
        agent = self.client.get(f'/api/searches/{run.pk}/').json()['agent']
        self.assertEqual(agent, {
            'mode': 'deterministic', 'model': 'sonnet', 'turns': 7, 'sessionId': 's-1',
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
        pending = Candidate.objects.get(run=run, verdict=Verdict.PENDING).pk

        def details(body, status=400):
            r = self.post('/api/objects/obj1/select/', body)
            self.assertEqual(r.status_code, status, r.content)
            return r.json().get('details', {})

        self.assertIn('not accepted', details({'candidateId': ids[3]})['candidateId'])  # rejected
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
        fetched = self.client.get('/api/objects/obj1/').json()
        fetched['name'] = 'Coffee table 2'
        fetched['selectedCandidateId'] = ids[1]  # ignored
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

        # Selection from the old run survives a newer run and comes last.
        self.post('/api/objects/obj1/select/', {'candidateId': old_ids[0]})
        new, _ = self.judged_run()
        new_cands = self.client.get(f'/api/searches/{new.pk}/').json()['candidates']
        state = self.state_candidates()
        self.assertEqual([c['id'] for c in state], [c['id'] for c in new_cands] + [old_ids[0]])
        self.assertEqual(state[-1]['runId'], str(old.pk))

        # A newer run without accepted candidates does not hide them.
        empty = engine.create_run(self.obj)
        runner.finalize(empty)
        self.assertEqual(SearchRun.objects.get(pk=empty.pk).status, 'empty')
        self.assertEqual([c['id'] for c in self.state_candidates()], [c['id'] for c in state])

        # Selecting one of the newest run's candidates is not listed twice.
        self.post('/api/objects/obj1/select/', {'candidateId': new_cands[1]['id']})
        self.assertEqual([c['id'] for c in self.state_candidates()], [c['id'] for c in new_cands])

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
        self.judged_run()
        with self.assertNumQueries(5):
            self.client.get('/api/state/')
        for i in range(3):
            obj = make_object(f'more{i}')
            run, ids = self.judged_run(obj)
            self.post(f'/api/objects/{obj.pk}/select/', {'candidateId': ids[0]})
            self.judged_run(obj)  # the selection is now from an older run
        with self.assertNumQueries(5):
            self.assertEqual(len(self.client.get('/api/state/').json()['candidates']), 3 + 3 * 4)

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
