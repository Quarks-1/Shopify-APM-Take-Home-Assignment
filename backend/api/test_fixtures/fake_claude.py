#!/usr/bin/env python3
"""Fake `claude` CLI for tests (used via SEARCHER_CLAUDE_BIN).

Parses argv the way the runner calls the real one and exits 3 if an
important flag is missing or a forbidden one is present.

It always writes `fake-claude-<SEARCH_RUN_ID>.json` into its working
directory (the runner's agent-cwd) with its argv, env, pid and the pids of
any helper processes, so tests can inspect exactly what it was given.

Behaviour is picked by a marker in the object name (it is embedded in the
prompt). Markers avoid the fake ucp's keywords (crash, garbage, empty...):
  (none)            "agent": runs `searcher next`, accepts every returned
                    id with `searcher judge --accept`, through the REAL
                    `searcher` wrapper found on PATH, until done or stop
  fake=slow         sleeps (with a child process) well past any tiny timeout
  fake=stubborn     like slow but ignores SIGTERM (exercises SIGKILL)
  fake=exit1        prints nothing useful and exits 1
  fake=error_json   prints a result with is_error: true (exit 0)
  fake=notjson      prints text that is not JSON (exit 0)
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid

ALLOWED = ['Bash(searcher next:*)', 'Bash(searcher judge:*)', 'Bash(searcher status:*)']
FORBIDDEN = ('--dangerously-skip-permissions', '--bare', '--max-budget-usd')


def flag_value(argv, flag):
    if flag not in argv:
        return None
    i = argv.index(flag)
    return argv[i + 1] if i + 1 < len(argv) else None


def variadic(argv, flag):
    if flag not in argv:
        return None
    out = []
    for a in argv[argv.index(flag) + 1:]:
        if a.startswith('--'):
            break
        out.append(a)
    return out


def check_flags(argv):
    problems = []
    if not argv or argv[0] != '-p' or len(argv) < 2 or argv[1].startswith('-'):
        problems.append('-p must come first, followed by the prompt')
    if variadic(argv, '--allowedTools') != ALLOWED:
        problems.append(f'--allowedTools must be exactly {ALLOWED}')
    if variadic(argv, '--tools') != ['Bash']:
        problems.append('--tools must be exactly Bash')
    expect = {
        '--setting-sources': 'project,local', '--permission-mode': 'dontAsk',
        '--output-format': 'json',
    }
    for flag, value in expect.items():
        if flag_value(argv, flag) != value:
            problems.append(f'{flag} must be {value}')
    for flag in ('--strict-mcp-config', '--safe-mode', '--no-session-persistence', '--json-schema',
                 '--system-prompt', '--max-turns', '--model'):
        if flag not in argv:
            problems.append(f'missing {flag}')
    for flag in FORBIDDEN:
        if flag in argv:
            problems.append(f'forbidden {flag}')
    try:
        json.loads(flag_value(argv, '--json-schema') or '')
    except ValueError:
        problems.append('--json-schema is not JSON')
    return problems


def dump(info):
    path = os.path.join(os.getcwd(), f'fake-claude-{os.environ.get("SEARCH_RUN_ID", "none")}.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(info, fh, indent=1)


def searcher(*args):
    exe = shutil.which('searcher')  # the wrapper, via PATH only
    proc = subprocess.run([exe, *args], capture_output=True, text=True)
    return json.loads(proc.stdout), proc.returncode


def mode_of(prompt):
    for mode in ('slow', 'stubborn', 'exit1', 'error_json', 'notjson'):
        if f'fake={mode}' in prompt:
            return mode
    return 'agent'


def result_doc(turns, accepted, outcome, is_error=False, subtype='success'):
    return {
        'type': 'result', 'subtype': subtype, 'is_error': is_error,
        'duration_ms': 1234, 'duration_api_ms': 1000, 'num_turns': turns,
        'result': 'Done. ' + 'x' * 2000,
        'session_id': str(uuid.uuid4()), 'total_cost_usd': 0.0421,
        'usage': {'input_tokens': 10, 'output_tokens': 20},
        'permission_denials': [
            {'tool_name': 'Bash', 'tool_use_id': 'toolu_1',
             'tool_input': {'command': 'ls /', 'description': 'list root'}},
        ],
        'structured_output': {'outcome': outcome, 'accepted': accepted,
                              'notes': 'fake agent accepted everything'},
    }


def main(argv):
    info = {'argv': argv, 'env': dict(os.environ), 'pid': os.getpid(), 'children': []}
    problems = check_flags(argv)
    info['problems'] = problems
    prompt = argv[1] if len(argv) > 1 else ''
    mode = mode_of(prompt)
    info['mode'] = mode
    dump(info)
    if problems:
        print('fake claude: bad flags: ' + '; '.join(problems), file=sys.stderr)
        return 3

    if mode in ('slow', 'stubborn'):
        if mode == 'stubborn':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child = subprocess.Popen(['sleep', '120'])
        info['children'].append(child.pid)
        dump(info)
        time.sleep(120)
        return 0
    if mode == 'exit1':
        print('fake claude crashed', file=sys.stderr)
        return 1
    if mode == 'notjson':
        print('this is not json')
        return 0
    if mode == 'error_json':
        print(json.dumps(result_doc(2, 0, 'gave_up', is_error=True, subtype='error_during_execution')))
        return 0

    turns, accepted, outcome = 0, 0, 'exhausted'
    for _ in range(20):
        out, _rc = searcher('next')
        turns += 1
        info.setdefault('calls', []).append({'next': {k: out.get(k) for k in ('round', 'stop', 'error')}})
        if out.get('stop'):
            accepted = out.get('accepted', accepted)
            outcome = 'target_reached' if out.get('reason') == 'target reached' else 'exhausted'
            break
        ids = [str(c['id']) for c in out.get('candidates') or []]
        if not ids:
            continue
        res, _rc = searcher('judge', '--accept', *ids)
        turns += 1
        accepted = res.get('accepted', accepted)
        if res.get('done'):
            outcome = 'target_reached'
            break
    dump(info)
    print(json.dumps(result_doc(turns + 1, accepted, outcome)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
