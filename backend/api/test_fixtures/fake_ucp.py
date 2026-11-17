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
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = 5


def _rating(product):
    r = product.get('rating')
    if not r:
        variants = product.get('variants') or []
        r = variants[0].get('rating') if variants else None
    return (r or {}).get('value')


def main(argv):
    path = None
    for i, a in enumerate(argv):
        if a == '--input' and i + 1 < len(argv):
            path = argv[i + 1]
    if not path or not path.startswith('@'):
        print('missing --input @file', file=sys.stderr)
        return 64
    with open(path[1:], encoding='utf-8') as fh:
        payload = json.load(fh)
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
