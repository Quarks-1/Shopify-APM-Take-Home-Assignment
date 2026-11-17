"""Direct tests of the generic claude CLI subprocess helper."""
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import claude_cli


class ClaudeCliRunTests(SimpleTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.out = self.dir / 'out.json'
        self.err = self.dir / 'err.log'

    def call(self, code, **kw):
        kw.setdefault('timeout', 20)
        return claude_cli.run(
            [sys.executable, '-c', code], cwd=self.dir, env=claude_cli.base_env(),
            out_path=self.out, err_path=self.err, **kw)

    def test_success_parses_result(self):
        pids = []
        res = self.call('import json;print(json.dumps({"type":"result","num_turns":2}))',
                        on_spawn=pids.append)
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.data['num_turns'], 2)
        self.assertFalse(res.timed_out or res.canceled)
        self.assertEqual(len(pids), 1)

    def test_stdin_path_is_fed_to_the_process(self):
        src = self.dir / 'in.txt'
        src.write_text('{"type": "result", "x": 1}')
        res = self.call('import sys;print(sys.stdin.read())', stdin_path=src)
        self.assertEqual(res.data['x'], 1)

    def test_timeout_kills_the_process_group(self):
        t0 = time.monotonic()
        with mock.patch.object(claude_cli, 'KILL_GRACE_SECONDS', 1):
            res = self.call('import time;time.sleep(60)', timeout=0.5)
        self.assertTrue(res.timed_out)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual(res.rc, -signal.SIGTERM)

    def test_is_canceled_stops_it(self):
        flag = threading.Event()
        threading.Timer(0.3, flag.set).start()
        with mock.patch.object(claude_cli, 'POLL_SECONDS', 0.1):
            res = self.call('import time;time.sleep(60)', is_canceled=flag.is_set)
        self.assertTrue(res.canceled)
        self.assertFalse(res.timed_out)

    def test_spawn_error(self):
        res = claude_cli.run(['/nonexistent/claude'], cwd=self.dir, env={}, out_path=self.out,
                             err_path=self.err, timeout=5)
        self.assertIsNotNone(res.spawn_error)
        self.assertIsNone(res.rc)

    def test_concurrent_runs(self):
        results = []

        def work(i):
            res = claude_cli.run(
                [sys.executable, '-c', f'import json;print(json.dumps({{"type":"result","i":{i}}}))'],
                cwd=self.dir, env=claude_cli.base_env(), out_path=self.dir / f'o{i}', err_path=self.dir / f'e{i}',
                timeout=20)
            results.append(res.data['i'])

        threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [0, 1, 2])


class ClaudeCliEnvTests(SimpleTestCase):
    def test_base_env_has_no_claude_or_anthropic_keys(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'x', 'CLAUDECODE': '1',
                                          'CLAUDE_CODE_FOO': 'y', 'DJANGO_DB_PATH': '/db'}):
            env = claude_cli.base_env({'EXTRA': 'z', 'EMPTY': ''})
        self.assertFalse([k for k in env if k.startswith(('CLAUDE', 'ANTHROPIC'))])
        self.assertEqual(env['PATH'], claude_cli.SYSTEM_PATH)
        self.assertEqual(env['DJANGO_DB_PATH'], '/db')
        self.assertEqual(env['EXTRA'], 'z')
        self.assertNotIn('EMPTY', env)

    def test_extra_overrides_path(self):
        self.assertEqual(claude_cli.base_env({'PATH': '/x'})['PATH'], '/x')

    def test_trim_result(self):
        out = claude_cli.trim_result({'type': 'result', 'junk': 1, 'result': 'a' * 5000})
        self.assertEqual(set(out), {'type', 'result'})
        self.assertEqual(len(out['result']), claude_cli.RESULT_TEXT_LEN)
