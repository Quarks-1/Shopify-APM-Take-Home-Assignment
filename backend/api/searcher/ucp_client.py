"""Thin wrapper around the `ucp` CLI. Merchant data never touches a shell."""
import json
import os
import subprocess
import tempfile

from django.conf import settings


class UcpError(Exception):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


def _trim(text, n=400):
    text = (text or '').strip()
    return text if len(text) <= n else text[:n] + '...'


def search_catalog(payload):
    """Run a catalog search, return the parsed envelope. Raises UcpError."""
    fd, path = tempfile.mkstemp(prefix='ucp-input-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)
        argv = [
            settings.SEARCHER_UCP_BIN, 'catalog', 'search',
            '--input', '@' + path, '--format', 'json',
        ]
        try:
            proc = subprocess.run(
                argv, shell=False, capture_output=True, text=True,
                timeout=settings.SEARCHER_UCP_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise UcpError('timeout', f'ucp did not answer within {settings.SEARCHER_UCP_TIMEOUT:g}s')
        except FileNotFoundError:
            raise UcpError('not_found', f'ucp binary not found: {settings.SEARCHER_UCP_BIN}')
        except OSError as exc:
            raise UcpError('os_error', str(exc))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if proc.returncode != 0:
        raise UcpError(
            f'exit_{proc.returncode}',
            _trim(proc.stderr) or _trim(proc.stdout) or 'ucp exited with an error',
        )
    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        raise UcpError('invalid_json', 'ucp stdout was not valid JSON: ' + _trim(proc.stdout, 120))
    if not isinstance(envelope, dict) or not isinstance(envelope.get('result'), dict):
        detail = ''
        if isinstance(envelope, dict):
            detail = _trim(json.dumps(
                {k: envelope[k] for k in ('error', 'message', 'messages') if k in envelope}
                or {k: v for k, v in envelope.items() if k != 'ucp'}
            ))
        raise UcpError('no_result', 'ucp response had no result' + (f': {detail}' if detail else ''))
    return envelope
