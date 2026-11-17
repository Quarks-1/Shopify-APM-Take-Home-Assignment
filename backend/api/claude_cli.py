"""Run the `claude` CLI headless as a subprocess.

Generic plumbing shared by the searcher runner and the Pinterest categorizer:
a locked-down environment, a process-group-aware spawn/poll/terminate loop and
parsing of the CLI's JSON output. No module-level mutable state, so `run` is
safe to call from several threads at once.
"""
import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SYSTEM_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
KILL_GRACE_SECONDS = 5
POLL_SECONDS = 1.0
RESULT_TEXT_LEN = 1000
AGENT_RESULT_KEYS = (
    'type', 'subtype', 'is_error', 'session_id', 'num_turns', 'total_cost_usd', 'duration_ms',
    'permission_denials', 'structured_output',
)


@dataclass
class CliRun:
    rc: int | None
    data: dict | None
    timed_out: bool = False
    canceled: bool = False
    spawn_error: str | None = None  # OSError text when the process could not start


def signal_group(pid, sig):
    """Signal a process group we started. Never our own group."""
    if not pid or pid in (os.getpid(), os.getpgrp()):
        return False
    try:
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def terminate(proc):
    """SIGTERM the process group, SIGKILL after a grace period."""
    signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    signal_group(proc.pid, signal.SIGKILL)  # also any stragglers left in the group
    proc.wait()


def parse_output(raw):
    """The CLI's result object. `--output-format json` prints one object;
    stream-json prints one event per line with the result object last. Take
    the last line that is a {"type": "result"} dict, else the whole output or
    its last line as JSON."""
    raw = (raw or '').strip()
    if not raw:
        return None
    lines = raw.splitlines()
    if len(lines) > 1:
        for line in reversed(lines):
            if '"result"' not in line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get('type') == 'result':
                return data
    for text in (raw, lines[-1]):
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def trim_result(data):
    out = {k: data[k] for k in AGENT_RESULT_KEYS if k in data}
    if isinstance(data.get('result'), str):
        out['result'] = data['result'][:RESULT_TEXT_LEN]
    return out


def base_env(extra=None):
    """Built from scratch (explicit allowlist): nothing CLAUDE*/ANTHROPIC* leaks in.
    `extra` is merged last (it may override PATH); empty values are dropped."""
    src = os.environ
    env = {
        'HOME': src.get('HOME') or os.path.expanduser('~'),
        'USER': src.get('USER') or src.get('LOGNAME') or '',
        'LOGNAME': src.get('LOGNAME') or src.get('USER') or '',
        'TMPDIR': src.get('TMPDIR') or tempfile.gettempdir(),
        'SHELL': '/bin/bash',
        'LANG': 'en_US.UTF-8',
        'PATH': SYSTEM_PATH,
    }
    env.update({k: v for k, v in src.items() if k.startswith('DJANGO_')})
    if extra:
        env.update(extra)
    return {k: v for k, v in env.items() if v != ''}


def run(argv, *, cwd, env, out_path, err_path, timeout, stdin_path=None,
        is_canceled=lambda: False, on_spawn=lambda pid: None):
    """Spawn `argv` in its own session and wait for it, polling every
    POLL_SECONDS for `is_canceled()`. Timeout or cancel terminates the whole
    process group. stdout goes to out_path (truncated), stderr is appended to
    err_path, stdin comes from stdin_path or /dev/null."""
    try:
        with open(out_path, 'wb') as out, open(err_path, 'ab') as err, \
                open(stdin_path if stdin_path else os.devnull, 'rb') as inp:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), env=env, shell=False, start_new_session=True,
                stdin=inp, stdout=out, stderr=err,
            )
    except OSError as exc:
        return CliRun(rc=None, data=None, spawn_error=str(exc))
    on_spawn(proc.pid)

    timed_out = canceled = False
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            proc.wait(timeout=min(remaining, POLL_SECONDS))
            break
        except subprocess.TimeoutExpired:
            if is_canceled():
                canceled = True
                break
    if timed_out or canceled:
        terminate(proc)
    else:
        signal_group(proc.pid, signal.SIGTERM)  # stragglers, if any; usually a no-op

    try:
        data = parse_output(Path(out_path).read_text(encoding='utf-8', errors='replace'))
    except OSError:
        data = None
    return CliRun(rc=proc.returncode, data=data, timed_out=timed_out, canceled=canceled)
