#!/usr/bin/env python3
"""Fake `ucp` CLI for tests. Reads `catalog search --input @file --format json`
and prints a saved fixture envelope chosen by the query text.

Query keywords (case-insensitive):
  ERRENV  -> error envelope (no "result"), exit 0
  CRASH   -> exit 2 with a message on stderr
  GARBAGE -> exit 0 with invalid JSON
  EMPTY   -> an envelope with zero products
  sofa / lamp / rug / table -> matching fixture (table is the default)

Fixtures with more than 5 products are paged: no cursor returns the first 5
with cursor "page2"; cursor "page2" returns the rest with no next page.

`catalog get_product <gid>` prints a product with media: two jpgs (one repeated
with another ?v=), a video, an off-allowlist host and a variant photo. A gid
containing FAIL answers with a CLI error. A search payload with `like` is
validated (one item, `id` or `image`); a like id containing NOEMB answers
"similarity seed embedding is missing". Every search payload is appended to
$FAKE_UCP_STATE.log.jsonl when that is set.

Merchant commands (`cart create|update|get|cancel`, `checkout create`) keep
their carts in the JSON file at $FAKE_UCP_STATE and append every call to
$FAKE_UCP_STATE.log.jsonl as {argv, payload}. Behaviour by --business host:
  noucp.*  -> exit 1, {"code": "OPERATION_NOT_OFFERED", ...} on stdout
  crash.*  -> exit 2 with a message on stderr
  badurl.* -> continue_url is http://
  slow.*   -> sleeps $FAKE_UCP_SLOW seconds (default 5) first
An unknown cart id answers like a real merchant: exit 0, ucp.status "error",
a cart_not_found message and no result id. A variant gid containing
DISCLOSURE adds a disclosure message for its line. Merchant line order is the
reverse of the request order. `checkout complete|cancel` exit 99.
"""
import fcntl
import json
import os
import secrets
import sys
import time
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = 5


def _rating(product):
    r = product.get('rating')
    if not r:
        variants = product.get('variants') or []
        r = variants[0].get('rating') if variants else None
    return (r or {}).get('value')


UNIT_PRICE = 1250
TAX_RATE = 0.08


def _arg(argv, flag):
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _log(argv, payload):
    state = os.environ.get('FAKE_UCP_STATE')
    if state:
        with open(state + '.log.jsonl', 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'argv': argv, 'payload': payload}) + '\n')


def _envelope(host, result, status='success'):
    return {
        'business': f'https://{host}',
        'endpoint': f'https://{host}/api/ucp/mcp',
        'transport': 'mcp',
        'ucp': {'version': '2026-08-25', 'status': status},
        'result': result,
    }


def _not_found(host):
    return _envelope(host, {
        'messages': [{
            'type': 'error', 'content_type': 'plain', 'code': 'cart_not_found',
            'content': 'The requested cart does not exist', 'severity': 'unrecoverable',
        }],
        'continue_url': f'https://{host}/',
    }, status='error')


def _totals(subtotal, tax=None):
    out = [{'type': 'subtotal', 'amount': subtotal, 'display_text': 'Subtotal'}]
    if tax is not None:
        out.append({'type': 'tax', 'amount': tax, 'display_text': 'Tax'})
    out.append({'type': 'total', 'amount': subtotal + (tax or 0), 'display_text': 'Total'})
    return out


def _cart_result(host, cart_id, cart):
    token = cart['token']
    lines, messages = [], []
    for i, (variant, qty) in enumerate(reversed(cart['lines'])):
        n = variant.rsplit('/', 1)[-1]
        lines.append({
            'id': f'gid://shopify/CartLine/{secrets.token_hex(8)}?cart={token}',
            'item': {'id': variant, 'title': f'Item {n}', 'price': UNIT_PRICE,
                     'image_url': f'https://cdn.shopify.com/fake/{n}.jpg'},
            'quantity': qty,
            'totals': _totals(UNIT_PRICE * qty),
        })
        if 'DISCLOSURE' in variant:
            messages.append({
                'type': 'warning', 'content_type': 'plain', 'code': 'disclosure',
                'presentation': 'disclosure', 'path': f'$.line_items[{i}]',
                'content': 'WARNING: This product can expose you to chemicals.',
                'url': f'https://{host}/pages/prop65',
            })
    scheme = 'http' if host.startswith('badurl.') else 'https'
    return {
        'id': cart_id,
        'line_items': lines,
        'currency': 'USD',
        'totals': _totals(sum(UNIT_PRICE * q for _, q in cart['lines'])),
        'messages': messages,
        'links': [],
        'expires_at': '2099-01-01T00:00:00Z',
        'continue_url': f'{scheme}://{host}/cart/c/{token}?key={cart["key"]}',
    }


def _lines(payload):
    return [[li['item']['id'], li['quantity']] for li in payload.get('line_items') or []]


def merchant(argv):
    command = tuple(argv[:2])
    business = _arg(argv, '--business') or ''
    host = urlsplit(business).hostname or ''
    path = _arg(argv, '--input')
    payload = None
    if path and path.startswith('@'):
        with open(path[1:], encoding='utf-8') as fh:
            payload = json.load(fh)
    _log(argv, payload)
    if command in (('checkout', 'complete'), ('checkout', 'cancel')):
        print('fake ucp: forbidden command', file=sys.stderr)
        return 99
    if host.startswith('slow.'):
        time.sleep(float(os.environ.get('FAKE_UCP_SLOW', '5')))
    if host.startswith('crash.'):
        print('fake ucp crashed', file=sys.stderr)
        return 2
    if host.startswith('noucp.'):
        print(json.dumps({'code': 'OPERATION_NOT_OFFERED',
                          'message': f'{host} does not offer agent carts', 'retryable': False}))
        return 1

    state_path = os.environ.get('FAKE_UCP_STATE') or os.devnull
    lock = open(state_path + '.lock', 'w') if state_path != os.devnull else None
    if lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        try:
            with open(state_path, encoding='utf-8') as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        carts = state.setdefault('carts', {})
        positional = argv[2] if len(argv) > 2 and not argv[2].startswith('--') else None

        if command == ('cart', 'create'):
            token = secrets.token_urlsafe(18).replace('-', 'A').replace('_', 'B')
            cart_id = f'gid://shopify/Cart/{token}?key={secrets.token_hex(16)}'
            carts[cart_id] = {'host': host, 'token': token, 'key': cart_id.split('key=')[1],
                              'lines': _lines(payload)}
            out = _envelope(host, _cart_result(host, cart_id, carts[cart_id]))
        elif command in (('cart', 'update'), ('cart', 'get'), ('cart', 'cancel')):
            cart = carts.get(positional)
            if cart is None or cart['host'] != host:
                out = _not_found(host)
            else:
                if command[1] == 'update':
                    cart['lines'] = _lines(payload)
                out = _envelope(host, _cart_result(host, positional, cart))
                if command[1] == 'cancel':
                    del carts[positional]
        elif command == ('checkout', 'create'):
            cart_id = (payload or {}).get('cart_id')
            cart = carts.get(cart_id)
            if cart is None or cart['host'] != host:
                out = _not_found(host)
            else:
                result = _cart_result(host, cart_id, cart)
                subtotal = result['totals'][0]['amount']
                result.update({
                    'id': f'gid://shopify/Checkout/{cart["token"]}?key={cart["key"]}',
                    'status': 'incomplete',
                    'totals': _totals(subtotal, round(subtotal * TAX_RATE)),
                    'messages': [{
                        'type': 'error', 'content_type': 'plain',
                        'code': 'buyer_identity_contact_method_required',
                        'content': 'Missing a valid contact method.', 'severity': 'recoverable',
                    }],
                })
                state.setdefault('checkouts', []).append(result['id'])
                out = _envelope(host, result)
        else:
            print(f'fake ucp: unknown command {" ".join(command)}', file=sys.stderr)
            return 64
        if state_path != os.devnull:
            with open(state_path, 'w', encoding='utf-8') as fh:
                json.dump(state, fh)
    finally:
        if lock:
            lock.close()
    print(json.dumps(out))
    return 0


def get_product(argv):
    gid = argv[2]
    _log(argv, None)
    if 'FAIL' in gid:
        print(json.dumps({'code': 'NOT_FOUND', 'message': 'no such product', 'retryable': False}))
        return 1
    cdn = 'https://cdn.shopify.com/s/files/1/0001/files/'
    img = lambda name, host=None: {'type': 'image', 'url': f'{host or cdn}{name}'}  # noqa: E731
    product = {
        'id': gid, 'title': 'Reference table',
        'media': [img('a.jpg?v=1'), img('a.jpg?v=2'),
                  {'type': 'video', 'url': cdn + 'clip.mp4'},
                  img('b.png'), img('c.jpg', 'https://evil.example.com/files/')],
        'variants': [{'media': [img('d.jpg')]}, {'media': [img('b.png?v=9'), img('e.jpg')]}],
    }
    print(json.dumps({'ucp': {'status': 'success'}, 'result': {'product': product}}))
    return 0


def main(argv):
    if argv[:1] in (['cart'], ['checkout']):
        return merchant(argv)
    if argv[:2] == ['catalog', 'get_product']:
        return get_product(argv)
    path = None
    for i, a in enumerate(argv):
        if a == '--input' and i + 1 < len(argv):
            path = argv[i + 1]
    if not path or not path.startswith('@'):
        print('missing --input @file', file=sys.stderr)
        return 64
    with open(path[1:], encoding='utf-8') as fh:
        payload = json.load(fh)
    _log(argv, payload)
    like = payload.get('like')
    if like is not None:
        if not isinstance(like, list) or len(like) != 1 or not (
                like[0].get('id') or (like[0].get('image') or {}).get('data')):
            print(json.dumps({'code': 'SCHEMA_VALIDATION_FAILED', 'message': 'bad like',
                              'retryable': False}))
            return 1
        if 'NOEMB' in str(like[0].get('id')):
            print(json.dumps({'code': 'UPSTREAM', 'retryable': False,
                              'message': 'similarity seed embedding is missing'}))
            return 1
    q = payload.get('query', '').lower()
    if 'crash' in q:
        print('fake ucp crashed', file=sys.stderr)
        return 2
    if 'garbage' in q:
        print('not json at all')
        return 0
    if 'errenv' in q:
        print(json.dumps({'business': 'x', 'error': {'code': 'bad_request', 'message': 'nope'}}))
        return 0
    name = 'coffee-table'
    for key, fixture in (('sofa', 'sofa'), ('lamp', 'floor-lamp'), ('rug', 'area-rug')):
        if key in q:
            name = fixture
    with open(os.path.join(HERE, 'ucp', name + '.json'), encoding='utf-8') as fh:
        env = json.load(fh)
    result = env['result']
    products = result['products']
    min_rating = (((payload.get('filters') or {}).get('rating') or {}).get('variant') or {}).get('min')
    if min_rating is not None:
        products = [p for p in products if _rating(p) is not None and _rating(p) >= min_rating]
    cursor = (payload.get('pagination') or {}).get('cursor')
    if 'empty' in q:
        products = []
        result['pagination'] = {'has_next_page': False, 'total_count': 0}
    elif len(products) > PAGE:
        if cursor == 'page2':
            products = products[PAGE:]
            result['pagination'] = {'has_next_page': False, 'total_count': len(products) + PAGE}
        else:
            products = products[:PAGE]
            result['pagination'] = {'has_next_page': True, 'total_count': 585, 'cursor': 'page2'}
    result['products'] = products
    print(json.dumps(env))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
