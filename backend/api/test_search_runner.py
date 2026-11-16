"""Search runner: full in-process runs against the fake claude + fake ucp,
plus dispatch (concurrency, reaping) and cancel with mocked processes."""
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import Candidate, RunStatus, SearchRun, SpaceObject, Verdict
from .searcher import engine, runner
from .serializers import iso, run_to_json

FIXTURES = Path(__file__).parent / 'test_fixtures'
FAKE_UCP = str(FIXTURES / 'fake_ucp.py')
FAKE_CLAUDE = str(FIXTURES / 'fake_claude.py')


def make_object(oid='obj1', **kw):
    fields = dict(
        id=oid, name='Coffee table', dims={'x': 46, 'y': 24, 'z': 17}, type='furniture',
        material='wood', style='mid-century', price_limit=400, similar_items=['cocktail table'],
        created_at=timezone.now(),
    )
    fields.update(kw)
    return SpaceObject.objects.create(**fields)


def pid_gone(pid, wait=3.0):
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


class TempVarDir:
    """Give each test its own SEARCHER_VAR_DIR and SEARCHER_AGENT_CWD (both in
    the system temp dir, outside the repo)."""

    def setUp(self):
        super().setUp()
        self.var = Path(tempfile.mkdtemp(prefix='searcher-var-'))
        self.addCleanup(shutil.rmtree, self.var, True)
        self.cwd = self.var / 'agent-cwd'


# ---------------------------------------------------------------- integration

class RunnerIntegrationTests(TempVarDir, TransactionTestCase):
    """Real subprocesses: fake claude -> real `searcher` wrapper -> real
    management command -> fake ucp, all on the (file) test database."""

    def setUp(self):
        super().setUp()
        cm = override_settings(
            SEARCHER_VAR_DIR=self.var, SEARCHER_AGENT_CWD=self.cwd,
            SEARCHER_UCP_BIN=FAKE_UCP, SEARCHER_CLAUDE_BIN=FAKE_CLAUDE,
            SEARCHER_AGENT='claude', SEARCHER_FALLBACK='deterministic', SEARCHER_RUN_TIMEOUT=60,
            SEARCHER_TARGET=3, SEARCHER_MODEL='sonnet',
        )
        cm.enable()
        self.addCleanup(cm.disable)
        # No queued runs exist, so dispatch() must never start a real worker here.
        p = mock.patch.object(runner, '_spawn_worker', side_effect=AssertionError('no workers'))
        p.start()
        self.addCleanup(p.stop)

    def make_run(self, name='Coffee table', **kw):
        return engine.create_run(make_object(name=name, **kw))

    def dump(self, run):
        path = self.cwd / f'fake-claude-{run.pk}.json'
        return json.loads(path.read_text())

    def accepted(self, run):
        return Candidate.objects.filter(run=run, verdict=Verdict.ACCEPTED).count()

    def test_happy_path_reaches_target_through_real_wrapper(self):
        run = self.make_run()
        leaks = {'CLAUDE_CODE_SESSION_ID': 'leak', 'CLAUDECODE': '1',
                 'ANTHROPIC_BASE_URL': 'http://leak.invalid', 'ANTHROPIC_API_KEY': 'sk-leak',
                 'DJANGO_EXTRA_FLAG': 'kept', 'SOME_OTHER_VAR': 'dropped'}
        with mock.patch.dict(os.environ, leaks):
            runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.DONE)
        self.assertGreaterEqual(self.accepted(run), run.target)
        self.assertEqual((run.agent_mode, run.agent_model, run.fallback_reason), ('claude', 'sonnet', ''))
        self.assertIsNone(run.step)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNone(run.agent_pid)
        self.assertEqual(run.worker_pid, os.getpid())  # in-process
        self.assertEqual(run.round_count, 1)

        # Verdicts came from the agent's `searcher judge` calls.
        info = self.dump(run)
        self.assertEqual(info['problems'], [])
        self.assertEqual(info['mode'], 'agent')
        self.assertTrue(info['calls'])

        # The trimmed agent result.
        res = run.agent_result
        self.assertEqual(res['subtype'], 'success')
        self.assertFalse(res['is_error'])
        self.assertEqual(len(res['result']), 1000)
        self.assertNotIn('usage', res)
        self.assertEqual(res['structured_output']['outcome'], 'target_reached')
        for key in ('session_id', 'num_turns', 'total_cost_usd', 'duration_ms', 'permission_denials'):
            self.assertIn(key, res)
        agent = run_to_json(run)['agent']
        self.assertEqual(agent['deniedCommands'], ['ls /'])
        self.assertEqual((agent['mode'], agent['outcome']), ('claude', 'target_reached'))

        obj = SpaceObject.objects.get(pk=run.object_id)
        self.assertEqual(obj.search, {'status': 'done', 'lastRunAt': iso(run.finished_at)})
        self.assertTrue(runner.log_path(run.pk).exists())

    def test_agent_env_is_minimal_and_points_at_parent_db(self):
        run = self.make_run()
        leaks = {'CLAUDE_CODE_SESSION_ID': 'leak', 'CLAUDE_CODE_ENTRYPOINT': 'x',
                 'ANTHROPIC_BASE_URL': 'http://leak.invalid', 'ANTHROPIC_API_KEY': 'sk-leak',
                 'DJANGO_EXTRA_FLAG': 'kept', 'SOME_OTHER_VAR': 'dropped'}
        with mock.patch.dict(os.environ, leaks):
            runner.execute(run)
        env = self.dump(run)['env']
        bad = [k for k in env if k.upper().startswith(('CLAUDE', 'ANTHROPIC'))]
        self.assertEqual(bad, [])
        self.assertEqual(env['SEARCH_RUN_ID'], str(run.pk))
        self.assertEqual(env['DJANGO_DB_PATH'], str(connection.settings_dict['NAME']))
        self.assertIn('test_db', env['DJANGO_DB_PATH'])
        self.assertEqual(env['DJANGO_EXTRA_FLAG'], 'kept')
        self.assertNotIn('SOME_OTHER_VAR', env)
        self.assertEqual(env['PATH'].split(':')[0], str(self.var / 'bin'))
        self.assertEqual(env['SEARCHER_UCP_BIN'], FAKE_UCP)
        self.assertEqual(env['SEARCHER_PYTHON'], sys.executable)
        allowed = {
            'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'SHELL', 'LANG', 'PATH', 'SEARCH_RUN_ID',
            'SEARCHER_PYTHON', 'SEARCHER_MANAGE', *runner.FORWARDED_SETTINGS,
        }
        extra = {k for k in env if k not in allowed and not k.startswith(('DJANGO_', '__CF'))}
        self.assertEqual(extra, set())
        # The agent ran in the empty agent cwd, outside the repo.
        self.assertEqual(os.listdir(self.cwd), [f'fake-claude-{run.pk}.json'])
        self.assertFalse(runner.inside_repo(self.cwd))

    def test_agent_env_unit(self):
        run = self.make_run()
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'x', 'CLAUDECODE': '1'}):
            env = runner.agent_env(run, '/w')
        self.assertFalse([k for k in env if k.startswith(('CLAUDE', 'ANTHROPIC'))])
        self.assertEqual(env['PATH'], '/w:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin')
        self.assertEqual((env['SHELL'], env['LANG']), ('/bin/bash', 'en_US.UTF-8'))

    def test_timeout_kills_agent_group_and_falls_back(self):
        run = self.make_run('Coffee table fake=slow')
        with override_settings(SEARCHER_RUN_TIMEOUT=1.5):
            started = time.monotonic()
            runner.execute(run)
            self.assertLess(time.monotonic() - started, 30)
        run.refresh_from_db()
        info = self.dump(run)
        self.assertTrue(pid_gone(info['pid']), 'agent still alive')
        self.assertTrue(pid_gone(info['children'][0]), 'agent child still alive')
        self.assertIn('agent timed out', run.fallback_reason)
        self.assertEqual((run.agent_mode, run.status), ('deterministic', RunStatus.DONE))
        self.assertGreaterEqual(self.accepted(run), run.target)
        self.assertIsNone(run.agent_pid)

    def test_stubborn_agent_gets_sigkill(self):
        run = self.make_run('Coffee table fake=stubborn')
        with override_settings(SEARCHER_RUN_TIMEOUT=1), \
                mock.patch.object(runner, 'KILL_GRACE_SECONDS', 0.3):
            runner.execute(run)
        run.refresh_from_db()
        info = self.dump(run)
        self.assertTrue(pid_gone(info['pid']))
        self.assertTrue(pid_gone(info['children'][0]))
        self.assertIn('agent timed out', run.fallback_reason)
        self.assertEqual(run.status, RunStatus.DONE)

    def test_crash_falls_back_to_deterministic(self):
        run = self.make_run('Coffee table fake=exit1')
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.fallback_reason, 'claude exited with code 1')
        self.assertEqual((run.agent_mode, run.status), ('deterministic', RunStatus.DONE))
        self.assertGreaterEqual(self.accepted(run), run.target)
        self.assertIsNone(run.agent_result)
        self.assertEqual(run.error, '')

    def test_error_json_falls_back(self):
        run = self.make_run('Coffee table fake=error_json')
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.fallback_reason, 'claude reported an error (error_during_execution)')
        self.assertTrue(run.agent_result['is_error'])
        self.assertEqual((run.agent_mode, run.status), ('deterministic', RunStatus.DONE))

    def test_invalid_json_and_missing_binary_fall_back(self):
        run = self.make_run('Coffee table fake=notjson')
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.fallback_reason, 'claude returned invalid JSON')
        self.assertEqual(run.status, RunStatus.DONE)

        run = engine.create_run(make_object('obj2'))
        with override_settings(SEARCHER_CLAUDE_BIN='/nonexistent/claude'):
            runner.execute(run)
        run.refresh_from_db()
        self.assertIn('claude could not start', run.fallback_reason)
        self.assertEqual((run.agent_mode, run.status), ('deterministic', RunStatus.DONE))

    def test_bad_flags_exit_3(self):
        run = self.make_run()
        real = runner.build_argv
        with mock.patch.object(runner, 'build_argv',
                               lambda r: real(r) + ['--dangerously-skip-permissions']):
            runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.fallback_reason, 'claude exited with code 3')
        self.assertIn('forbidden --dangerously-skip-permissions', self.dump(run)['problems'])

    def test_no_fallback_failures_end_failed_or_empty(self):
        with override_settings(SEARCHER_FALLBACK='none'):
            run = self.make_run('Coffee table fake=exit1')
            runner.execute(run)
            run.refresh_from_db()
            self.assertEqual(run.status, RunStatus.FAILED)  # no rounds at all
            self.assertEqual(run.error, 'claude exited with code 1')
            self.assertEqual((run.agent_mode, run.fallback_reason, run.round_count), ('claude', '', 0))
            self.assertEqual(SpaceObject.objects.get(pk=run.object_id).search['status'], 'idle')

            run = engine.create_run(make_object('obj2', name='Coffee table fake=error_json'))
            runner.execute(run)
            run.refresh_from_db()
            self.assertEqual(run.status, RunStatus.FAILED)
            self.assertIn('reported an error', run.error)

    def test_agent_that_finds_nothing_ends_empty(self):
        run = self.make_run('thing EMPTY', style=None, material=None, similar_items=[])
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.EMPTY)
        self.assertEqual(run.fallback_reason, '')  # the agent succeeded; nothing to fall back for
        self.assertGreater(run.round_count, 0)
        self.assertEqual(run.agent_result['structured_output']['outcome'], 'exhausted')
        self.assertEqual(SpaceObject.objects.get(pk=run.object_id).search['status'], 'empty')

    def test_deterministic_mode(self):
        run = self.make_run()
        with override_settings(SEARCHER_AGENT='deterministic'):
            runner.execute(run)
        run.refresh_from_db()
        self.assertEqual((run.status, run.agent_mode, run.agent_model), (RunStatus.DONE, 'deterministic', ''))
        self.assertEqual(self.accepted(run), run.target)
        self.assertFalse((self.cwd / f'fake-claude-{run.pk}.json').exists())

    def test_cancel_while_agent_runs(self):
        run = self.make_run('Coffee table fake=slow')
        errors = []

        def cancel_soon():
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if SearchRun.objects.filter(pk=run.pk, agent_pid__isnull=False).exists():
                        break
                    time.sleep(0.05)
                runner.cancel_run(SearchRun.objects.get(pk=run.pk))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                connection.close()

        t = threading.Thread(target=cancel_soon)
        t.start()
        runner.execute(run)
        t.join()
        self.assertEqual(errors, [])
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELED)
        self.assertEqual(run.agent_mode, 'claude')  # no fallback after a cancel
        self.assertTrue(pid_gone(self.dump(run)['pid']))
        self.assertEqual(SpaceObject.objects.get(pk=run.object_id).search['status'], 'idle')

    def test_execute_never_leaves_a_run_running(self):
        run = self.make_run()
        with mock.patch.object(runner, '_run_agent', side_effect=RuntimeError('boom')):
            runner.execute(run)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(run.error, 'RuntimeError: boom')
        self.assertIn('Traceback', runner.log_path(run.pk).read_text())

    def test_execute_skips_canceled_run(self):
        run = self.make_run()
        SearchRun.objects.filter(pk=run.pk).update(status=RunStatus.CANCELED)
        runner.execute(run)
        run.refresh_from_db()
        self.assertEqual((run.status, run.agent_mode), (RunStatus.CANCELED, ''))

    def test_run_search_command(self):
        from io import StringIO
        from django.core.management import call_command
        run = self.make_run()
        out = StringIO()
        with override_settings(SEARCHER_AGENT='deterministic'):
            call_command('run_search', str(run.pk), stdout=out)
        doc = json.loads(out.getvalue())
        self.assertEqual((doc['status'], doc['mode']), ('done', 'deterministic'))
        with self.assertRaises(SystemExit):
            call_command('run_search', 'nope', stdout=StringIO())


# ------------------------------------------------------------ agent cwd

class AgentCwdTests(TempVarDir, SimpleTestCase):
    def test_default_is_outside_the_repo(self):
        from django.conf import settings
        self.assertTrue(runner.repo_roots())  # the check has a repo to compare against
        self.assertFalse(runner.inside_repo(settings.SEARCHER_AGENT_CWD))
        self.assertFalse(runner.inside_repo(runner.default_agent_cwd()))
        with override_settings(SEARCHER_AGENT_CWD=self.cwd):
            path = runner.agent_cwd()
        self.assertEqual(path, self.cwd)
        self.assertTrue(path.is_dir())
        self.assertFalse(runner.inside_repo(path))

    def test_path_inside_repo_is_refused(self):
        repo = runner.repo_roots()[-1]
        for bad in (runner.BACKEND_DIR / 'var' / 'agent-cwd', repo, repo / 'x' / '..' / 'backend'):
            with self.subTest(bad=bad):
                self.assertTrue(runner.inside_repo(bad))
                with override_settings(SEARCHER_AGENT_CWD=bad), \
                        self.assertLogs('api.searcher.runner', 'WARNING') as logs:
                    path = runner.agent_cwd()
                self.assertEqual(path, runner.default_agent_cwd())
                self.assertFalse(runner.inside_repo(path))
                self.assertIn('inside the git repo', logs.output[0])
        self.assertFalse((runner.BACKEND_DIR / 'var' / 'agent-cwd').exists())

    def test_claude_is_started_outside_the_repo(self):
        seen = {}

        class Stop(Exception):
            pass

        def fake_popen(argv, **kw):
            seen.update(kw)
            raise Stop

        run = mock.Mock(pk='r1', target=10, max_rounds=8, object_snapshot={'name': 'x'})
        with override_settings(SEARCHER_VAR_DIR=self.var,
                               SEARCHER_AGENT_CWD=runner.BACKEND_DIR / 'var' / 'agent-cwd'), \
                mock.patch.object(runner.subprocess, 'Popen', fake_popen), \
                mock.patch.object(runner.SearchRun.objects, 'filter'), \
                self.assertLogs('api.searcher.runner', 'WARNING'):
            with self.assertRaises(Stop):
                runner._invoke_claude(run)
        self.assertFalse(runner.inside_repo(seen['cwd']))


# ----------------------------------------------------------------- dispatch

class DispatchTests(TempVarDir, TestCase):
    def setUp(self):
        super().setUp()
        cm = override_settings(SEARCHER_VAR_DIR=self.var, SEARCHER_MAX_CONCURRENT=2)
        cm.enable()
        self.addCleanup(cm.disable)
        pids = itertools.count(50001)
        self.popen = mock.patch.object(
            runner.subprocess, 'Popen', side_effect=lambda *a, **k: mock.Mock(pid=next(pids)),
        ).start()
        self.alive = mock.patch.object(runner, '_pid_alive', return_value=True).start()
        self.killpg = mock.patch.object(runner.os, 'killpg').start()
        self.addCleanup(mock.patch.stopall)
        runner._children.clear()

    def statuses(self, runs):
        return [SearchRun.objects.get(pk=r.pk).status for r in runs]

    def test_cap_and_promotion_order(self):
        runs = [runner.start_search(make_object(f'o{i}'))[0] for i in range(4)]
        self.assertEqual(self.statuses(runs), ['running', 'running', 'queued', 'queued'])
        self.assertEqual(self.popen.call_count, 2)
        argv, kw = self.popen.call_args_list[0]
        self.assertEqual(argv[0], [sys.executable, str(runner.MANAGE_PY), 'run_search', str(runs[0].pk)])
        self.assertTrue(kw['start_new_session'])
        self.assertFalse(kw['shell'])
        self.assertEqual(kw['cwd'], str(runner.BACKEND_DIR))
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['env']['DJANGO_DB_PATH'], str(connection.settings_dict['NAME']))
        self.assertEqual(SearchRun.objects.get(pk=runs[0].pk).worker_pid, 50001)
        self.assertEqual(SpaceObject.objects.get(pk='o3').search, {'status': 'running', 'step': 'planning'})

        # The oldest queued run goes next, regardless of insertion order.
        SearchRun.objects.filter(pk=runs[3].pk).update(created_at=timezone.now() - timedelta(hours=1))
        SearchRun.objects.filter(pk=runs[0].pk).update(status='done')
        self.assertEqual(runner.dispatch(), [runs[3].pk])
        self.assertEqual(self.statuses(runs), ['done', 'running', 'queued', 'running'])
        self.assertEqual(runner.dispatch(), [])  # still at the cap
        SearchRun.objects.filter(pk=runs[1].pk).update(status='failed')
        self.assertEqual(runner.dispatch(), [runs[2].pk])

    def test_start_search_is_idempotent(self):
        obj = make_object()
        run, created = runner.start_search(obj)
        again, created2 = runner.start_search(obj)
        self.assertEqual((created, created2, again.pk), (True, False, run.pk))
        self.assertEqual(SearchRun.objects.count(), 1)
        self.assertEqual(self.popen.call_count, 1)
        SearchRun.objects.filter(pk=run.pk).update(status='done')
        third, created3 = runner.start_search(obj)
        self.assertTrue(created3)
        self.assertNotEqual(third.pk, run.pk)

    def test_imported_objects_are_refused(self):
        obj = make_object(source='imported')
        with self.assertRaises(runner.SearchRefused):
            runner.start_search(obj)
        self.assertEqual(SearchRun.objects.count(), 0)

    def test_queued_run_snapshot_and_state(self):
        with override_settings(SEARCHER_MAX_CONCURRENT=1):
            runner.start_search(make_object('a'))
            run, _ = runner.start_search(make_object('b'))
        self.assertEqual((run.status, run.step, run.started_at), ('queued', None, None))
        self.assertEqual(run.object_snapshot['name'], 'Coffee table')

    def test_reaps_stale_runs(self):
        dead = engine.create_run(make_object('dead'))
        SearchRun.objects.filter(pk=dead.pk).update(worker_pid=999, agent_pid=998)
        orphan = engine.create_run(make_object('orphan'))
        SearchRun.objects.filter(pk=orphan.pk).update(started_at=timezone.now() - timedelta(seconds=61))
        fresh = engine.create_run(make_object('fresh'))  # no pid yet, but just started
        alive = engine.create_run(make_object('alive'))
        SearchRun.objects.filter(pk=alive.pk).update(worker_pid=1000)
        self.alive.side_effect = lambda pid: pid != 999
        with override_settings(SEARCHER_MAX_CONCURRENT=10):
            queued, _ = runner.start_search(make_object('queued'))
        self.assertEqual(self.statuses([dead, orphan, fresh, alive]), ['failed', 'failed', 'running', 'running'])
        for r in (dead, orphan):
            r.refresh_from_db()
            self.assertIn('worker exited unexpectedly', r.error)
            self.assertIsNotNone(r.finished_at)
            self.assertEqual(SpaceObject.objects.get(pk=r.object_id).search['status'], 'idle')
        self.killpg.assert_called_once_with(998, signal.SIGTERM)  # the orphaned agent
        self.assertEqual(SearchRun.objects.get(pk=queued.pk).status, 'running')

    def test_manual_cli_runs_are_never_reaped(self):
        from io import StringIO
        from django.core.management import call_command
        make_object('m')
        out = StringIO()
        call_command('searcher', 'start', '--object', 'm', stdout=out)
        manual = SearchRun.objects.get(pk=json.loads(out.getvalue())['run'])
        self.assertEqual((manual.status, manual.agent_mode), ('running', 'manual'))
        orphan = engine.create_run(make_object('orphan'))
        old = timezone.now() - timedelta(minutes=10)
        SearchRun.objects.filter(pk__in=[manual.pk, orphan.pk]).update(started_at=old)
        self.assertEqual(runner.reap_stale(), [orphan.pk])
        self.assertEqual(self.statuses([manual, orphan]), ['running', 'failed'])

    def test_spawn_failure_marks_run_failed(self):
        self.popen.side_effect = OSError('no python')
        run, _ = runner.start_search(make_object())
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertIn('could not start worker', run.error)

    def test_pid_alive(self):
        mock.patch.stopall()  # the real _pid_alive
        self.assertTrue(runner._pid_alive(os.getpid()))
        proc = subprocess.Popen(['true'])
        proc.wait()
        self.assertFalse(runner._pid_alive(proc.pid))
        zombie = subprocess.Popen(['true'])
        time.sleep(0.3)  # exited, not yet reaped
        self.assertFalse(runner._pid_alive(zombie.pid))
        self.assertFalse(runner._pid_alive(None))

    def test_never_signals_own_group(self):
        self.assertFalse(runner._signal_group(os.getpid(), signal.SIGTERM))
        self.assertFalse(runner._signal_group(os.getpgrp(), signal.SIGTERM))
        self.killpg.assert_not_called()


# ------------------------------------------------------------------- cancel

class CancelTests(TempVarDir, TestCase):
    def setUp(self):
        super().setUp()
        cm = override_settings(SEARCHER_VAR_DIR=self.var, SEARCHER_MAX_CONCURRENT=1)
        cm.enable()
        self.addCleanup(cm.disable)
        self.popen = mock.patch.object(
            runner.subprocess, 'Popen', side_effect=lambda *a, **k: mock.Mock(pid=60001)).start()
        mock.patch.object(runner, '_pid_alive', return_value=True).start()
        self.killpg = mock.patch.object(runner.os, 'killpg').start()
        self.addCleanup(mock.patch.stopall)

    def test_cancel_signals_processes_and_promotes_next(self):
        run, _ = runner.start_search(make_object('a'))
        SearchRun.objects.filter(pk=run.pk).update(worker_pid=111, agent_pid=222)
        waiting, _ = runner.start_search(make_object('b'))
        self.assertEqual(waiting.status, 'queued')
        run, changed = runner.cancel_run(run)
        self.assertTrue(changed)
        self.assertEqual((run.status, run.step), ('canceled', None))
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(self.killpg.call_args_list,
                         [mock.call(222, signal.SIGTERM), mock.call(111, signal.SIGTERM)])
        self.assertEqual(SpaceObject.objects.get(pk='a').search,
                         {'status': 'idle', 'lastRunAt': iso(run.finished_at)})
        self.assertEqual(SearchRun.objects.get(pk=waiting.pk).status, 'running')
        # The engine refuses further rounds.
        self.assertTrue(engine.next_round(run)['stop'])

    def test_cancel_queued_run(self):
        runner.start_search(make_object('a'))
        queued, _ = runner.start_search(make_object('b'))
        run, changed = runner.cancel_run(queued)
        self.assertTrue(changed)
        self.assertEqual(run.status, 'canceled')
        self.killpg.assert_not_called()

    def test_cancel_finished_run_is_noop(self):
        run, _ = runner.start_search(make_object('a'))
        finished = timezone.now() - timedelta(minutes=5)
        SearchRun.objects.filter(pk=run.pk).update(status='done', finished_at=finished,
                                                   worker_pid=111, agent_pid=222)
        run, changed = runner.cancel_run(run)
        self.assertFalse(changed)
        self.assertEqual((run.status, run.finished_at), ('done', finished))
        self.killpg.assert_not_called()

    def test_deleting_object_cancels_its_runs(self):
        run, _ = runner.start_search(make_object('a'))
        SearchRun.objects.filter(pk=run.pk).update(worker_pid=333, agent_pid=444)
        r = self.client.delete('/api/objects/a/')
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.killpg.call_args_list,
                         [mock.call(444, signal.SIGTERM), mock.call(333, signal.SIGTERM)])
        self.assertFalse(SpaceObject.objects.filter(pk='a').exists())
        self.assertFalse(SearchRun.objects.filter(pk=run.pk).exists())


# ----------------------------------------------------------------- finalize

class FinalizeTests(TestCase):
    def setUp(self):
        self.run = engine.create_run(make_object())

    def finalize(self, **fields):
        SearchRun.objects.filter(pk=self.run.pk).update(**fields)
        return runner.finalize(self.run)

    def test_statuses(self):
        self.assertEqual(self.finalize().status, 'empty')
        self.assertEqual(self.finalize(status='running', error='boom').status, 'failed')
        self.assertEqual(self.finalize(status='running', error='boom', round_count=2).status, 'empty')
        self.assertEqual(self.finalize(status='canceled').status, 'canceled')

    def test_idempotent_and_done(self):
        with override_settings(SEARCHER_UCP_BIN=FAKE_UCP):
            out = engine.next_round(self.run)
        engine.judge(self.run, [out['candidates'][0]['id']])
        first = self.finalize()
        self.assertEqual((first.status, first.step), ('done', None))
        again = runner.finalize(self.run)
        self.assertEqual((again.status, again.finished_at), ('done', first.finished_at))
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search,
                         {'status': 'done', 'lastRunAt': iso(first.finished_at)})
        self.assertTrue(run_to_json(again)['partial'])

    def test_state_follows_newest_run_only(self):
        newer = engine.create_run(SpaceObject.objects.get(pk='obj1'))
        runner.finalize(self.run)  # the older run finishing must not clobber the newer one
        state = SpaceObject.objects.get(pk='obj1').search
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['lastRunAt'], iso(SearchRun.objects.get(pk=self.run.pk).finished_at))
        runner.finalize(newer)
        self.assertEqual(SpaceObject.objects.get(pk='obj1').search['status'], 'empty')
