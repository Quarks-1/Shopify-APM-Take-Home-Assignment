"""Thin wrapper around the `ucp` CLI. Merchant data never touches a shell."""
import json
import os
import re
import subprocess
import tempfile

from django.conf import settings

# Cart and checkout ids are passed as positional argv entries.
ID_RE = re.compile(r'^[A-Za-z0-9/:._\-?=&]{1,255}\Z')
HOST_RE = re.compile(
    r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}\Z',
    re.IGNORECASE,
)
PROFILE_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}\Z')
# The app never completes or cancels a checkout: the buyer pays on the
# merchant's page, and cancelling a checkout also destroys its cart.
FORBIDDEN = {('checkout', 'complete'), ('checkout', 'cancel')}
MERCHANT_OPS = {'cart', 'checkout'}


class UcpError(Exception):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


def _trim(text, n=400):
    text = (text or '').strip()
    return text if len(text) <= n else text[:n] + '...'


def business_url(domain):
    """`https://<domain>` for a bare hostname, else UcpError."""
    if not isinstance(domain, str) or not HOST_RE.match(domain):
        raise UcpError('invalid_domain', f'not a bare hostname: {_trim(str(domain), 80)}')
    return 'https://' + domain.lower()


def _merchant_error(result):
    for m in result.get('messages') or []:
        if isinstance(m, dict) and m.get('type') == 'error':
            code = m.get('code') if isinstance(m.get('code'), str) and m.get('code') else 'merchant_error'
            return UcpError(_trim(code, 64), _trim(m.get('content') if isinstance(m.get('content'), str) else '')
                            or 'merchant returned an error')
    return UcpError('merchant_error', 'merchant returned an error')


def _run(command, payload=None, *, positional=(), business=None, profile=False, timeout=None):
    """Run `ucp <command...>` and return the parsed envelope. Raises UcpError."""
    command = tuple(command)
    if command in FORBIDDEN:
        raise UcpError('forbidden', f'ucp {" ".join(command)} is not allowed')
    for value in positional:
        if not isinstance(value, str) or not ID_RE.match(value) or value.startswith('-'):
            raise UcpError('invalid_argument', f'unsafe argument: {_trim(str(value), 80)}')
    argv = [settings.SEARCHER_UCP_BIN, *command, *positional]
    if business is not None:
        argv += ['--business', business_url(business)]
    if profile and settings.UCP_PROFILE:
        if not PROFILE_RE.match(settings.UCP_PROFILE):
            raise UcpError('invalid_profile', 'UCP_PROFILE must match [A-Za-z0-9_-]{1,64}')
        argv += ['--profile', settings.UCP_PROFILE]
    timeout = settings.SEARCHER_UCP_TIMEOUT if timeout is None else timeout

    path = None
    try:
        if payload is not None:
            fd, path = tempfile.mkstemp(prefix='ucp-input-', suffix='.json')
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh)
            argv += ['--input', '@' + path]
        argv += ['--format', 'json']
        try:
            proc = subprocess.run(
                argv, shell=False, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise UcpError('timeout', f'ucp did not answer within {timeout:g}s')
        except FileNotFoundError:
            raise UcpError('not_found', f'ucp binary not found: {settings.SEARCHER_UCP_BIN}')
        except OSError as exc:
            raise UcpError('os_error', str(exc))
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    try:
        envelope = json.loads(proc.stdout)
    except ValueError:
        envelope = None
    # The CLI's own errors: {"code", "message", "retryable"}, any exit code.
    if (isinstance(envelope, dict) and 'result' not in envelope
            and isinstance(envelope.get('code'), str) and envelope.get('code')):
        raise UcpError(_trim(envelope['code'], 64), _trim(str(envelope.get('message') or '')))
    if proc.returncode != 0:
        raise UcpError(
            f'exit_{proc.returncode}',
            _trim(proc.stderr) or _trim(proc.stdout) or 'ucp exited with an error',
        )
    if envelope is None:
        raise UcpError('invalid_json', 'ucp stdout was not valid JSON: ' + _trim(proc.stdout, 120))
    if not isinstance(envelope, dict) or not isinstance(envelope.get('result'), dict):
        detail = ''
        if isinstance(envelope, dict):
            detail = _trim(json.dumps(
                {k: envelope[k] for k in ('error', 'message', 'messages') if k in envelope}
                or {k: v for k, v in envelope.items() if k != 'ucp'}
            ))
        raise UcpError('no_result', 'ucp response had no result' + (f': {detail}' if detail else ''))
    if command[0] in MERCHANT_OPS:
        # Merchant-level failures exit 0 with ucp.status "error" and no result id.
        ucp = envelope.get('ucp') if isinstance(envelope.get('ucp'), dict) else {}
        result = envelope['result']
        if ucp.get('status') == 'error' or not isinstance(result.get('id'), str) or not result['id']:
            raise _merchant_error(result)
    return envelope


def search_catalog(payload):
    """Run a catalog search, return the parsed envelope. Raises UcpError."""
    return _run(('catalog', 'search'), payload)


def _merchant(command, domain, payload=None, positional=()):
    business_url(domain)  # a merchant command always names its business
    return _run(command, payload, positional=positional, business=domain, profile=True,
                timeout=settings.CART_UCP_TIMEOUT)


def cart_create(domain, payload):
    return _merchant(('cart', 'create'), domain, payload)


def cart_update(domain, cart_id, payload):
    """Full replace: every line item the cart should hold."""
    return _merchant(('cart', 'update'), domain, payload, (cart_id,))


def cart_get(domain, cart_id):
    return _merchant(('cart', 'get'), domain, positional=(cart_id,))


def cart_cancel(domain, cart_id):
    return _merchant(('cart', 'cancel'), domain, positional=(cart_id,))


def checkout_create(domain, payload):
    return _merchant(('checkout', 'create'), domain, payload)
