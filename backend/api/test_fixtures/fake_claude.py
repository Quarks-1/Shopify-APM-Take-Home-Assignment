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
  fake=prefs        like the agent, plus `preferences` in the structured output

Two invocation styles, like the real CLI:
  text      `-p <prompt> --output-format json`: prints the result object only
  stream    `-p --input-format stream-json --output-format stream-json --verbose`:
            the first user message (JSON line) is read from stdin, its text and
            image blocks are recorded as `stdin_text` / `image_types`, and a few
            event lines precede the result object, which is the last line
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
    stream = '--input-format' in argv
    if not argv or argv[0] != '-p':
        problems.append('-p must come first')
    elif stream:
        if flag_value(argv, '--input-format') != 'stream-json':
            problems.append('--input-format must be stream-json')
        if '--verbose' not in argv:
            problems.append('stream-json output needs --verbose')
        if len(argv) > 1 and not argv[1].startswith('-'):
            problems.append('no positional prompt in stream-json input mode')
    elif len(argv) < 2 or argv[1].startswith('-'):
        problems.append('-p must be followed by the prompt')
    if variadic(argv, '--allowedTools') != ALLOWED:
        problems.append(f'--allowedTools must be exactly {ALLOWED}')
    if variadic(argv, '--tools') != ['Bash']:
        problems.append('--tools must be exactly Bash')
    expect = {
        '--setting-sources': 'project,local', '--permission-mode': 'dontAsk',
        '--output-format': 'stream-json' if stream else 'json',
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


def read_stdin_message():
    """The first user message of a stream-json session: (text, [image media types])."""
    line = sys.stdin.readline()
    msg = json.loads(line)
    content = msg['message']['content']
    if isinstance(content, str):
        return content, [], []
    text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
    images = [b['source'] for b in content if b.get('type') == 'image']
    return text, [i.get('media_type') for i in images], [len(i.get('data', '')) for i in images]


def emit(doc, stream):
    """Print the result; in stream mode, behind a few other events like the real CLI."""
    if stream:
        print(json.dumps({'type': 'system', 'subtype': 'init', 'tools': ['Bash', 'StructuredOutput']}))
        print(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'working on it'}]}}))
        print(json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed'}}))
    print(json.dumps(doc))


def main(argv):
    info = {'argv': argv, 'env': dict(os.environ), 'pid': os.getpid(), 'children': []}
    problems = check_flags(argv)
    info['problems'] = problems
    stream = '--input-format' in argv
    info['stream'] = stream
    prompt = argv[1] if len(argv) > 1 and not argv[1].startswith('-') else ''
    if stream and not problems:
        try:
            prompt, info['image_types'], info['image_chars'] = read_stdin_message()
            info['stdin_text'] = prompt
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f'stdin is not a stream-json user message: {exc}')
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
        emit(result_doc(2, 0, 'gave_up', is_error=True, subtype='error_during_execution'), stream)
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
    doc = result_doc(turns + 1, accepted, outcome)
    if 'fake=prefs' in prompt:
        doc['structured_output']['preferences'] = {
            'likes': ['warm walnut wood', 'tapered\nlegs'], 'avoids': ['glossy white']}
    emit(doc, stream)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
