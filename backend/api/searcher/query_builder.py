"""Deterministic query strategies and UCP payloads.

Hard filters (price, availability, shipping) always go in the payload; no
strategy and no caller can drop them.
"""
import copy
import re

from django.conf import settings

TYPE_NOUNS = {
    'lighting': 'lamp',
    'rug': 'area rug',
    'wall-art': 'wall art',
    'storage': 'storage',
    'decor': 'home decor',
}


def normalize_query(q):
    return re.sub(r'\s+', ' ', (q or '').strip().lower())


def _join(*parts):
    return re.sub(r'\s+', ' ', ' '.join(p for p in parts if p)).strip()


def _style(snapshot):
    return snapshot.get('style') or ''


def _material(snapshot):
    m = snapshot.get('material')
    return '' if not m or m == 'mixed' else m


def _name(snapshot):
    return (snapshot.get('name') or '').strip()


def _type_noun(snapshot):
    t = snapshot.get('type')
    if t in TYPE_NOUNS:
        return TYPE_NOUNS[t]
    words = _name(snapshot).split()
    return words[-1] if words else ''


def strategy_query(snapshot, strategy):
    """Query string for a non-`more` strategy."""
    if strategy == 'exact':
        return _join(_style(snapshot), _material(snapshot), _name(snapshot))
    if strategy == 'relax-style':
        return _join(_material(snapshot), _name(snapshot))
    if strategy == 'name':
        return _name(snapshot)
    if strategy.startswith('similar:'):
        try:
            idx = int(strategy.split(':', 1)[1])
            item = (snapshot.get('similarItems') or [])[idx]
        except (ValueError, IndexError):
            raise ValueError(f'unknown strategy: {strategy}')
        return _join(_style(snapshot), str(item).strip())
    if strategy == 'broad':
        return _join(_style(snapshot), _material(snapshot), _type_noun(snapshot))
    raise ValueError(f'unknown strategy: {strategy}')


def _pq(q):
    """Accept dicts or CatalogQuery-like objects."""
    if isinstance(q, dict):
        return q
    return {
        'round': q.round, 'strategy': q.strategy,
        'query': (q.payload or {}).get('query', ''),
        'payload': q.payload, 'response_meta': q.response_meta,
        'passing_count': q.passing_count,
    }


def pick_more_source(previous_queries):
    """The earlier query to continue: most passing candidates among those that
    still have a next page and a cursor which no later query has consumed.
    Ties go to the earliest query. Returns a dict or None."""
    prev = [_pq(q) for q in previous_queries or []]
    consumed = {
        ((q.get('payload') or {}).get('pagination') or {}).get('cursor') for q in prev
    }
    consumed.discard(None)
    best = None
    for q in prev:
        meta = q.get('response_meta') or {}
        cursor = meta.get('cursor')
        if not (meta.get('has_next_page') and cursor) or cursor in consumed:
            continue
        if best is None or (q.get('passing_count') or 0) > (best.get('passing_count') or 0):
            best = q
    return best


def strategies(snapshot, used_queries, previous_queries=()):
    """Available strategies in ladder order."""
    used = {normalize_query(q) for q in used_queries or []}
    ladder = ['exact', 'relax-style', 'name']
    ladder += [f'similar:{i}' for i in range(len(snapshot.get('similarItems') or []))]
    ladder.append('broad')
    out, seen = [], set(used)
    for s in ladder:
        q = normalize_query(strategy_query(snapshot, s))
        if not q or q in seen:
            continue
        seen.add(q)
        out.append(s)
    if pick_more_source(previous_queries) is not None:
        out.append('more')
    return out


def _usd(value):
    value = float(value)
    return f'${value:,.0f}' if value == int(value) else f'${value:,.2f}'


def _fmt(v):
    return f'{round(float(v), 2):g}'


def _article(word):
    return 'an' if word[:1].lower() in 'aeiou' else 'a'


def _size_phrase(snapshot):
    """'46 W x 24 D x 17 H', or on a wall '24 W x 36 H'. Only states the
    sides the user gave: an object may have no depth (y) or height (z)."""
    from .filters import is_wall_mode
    d = snapshot.get('dims') or {}
    if d.get('x') is None:
        return ''
    sides = [f'{_fmt(d["x"])} W']
    if is_wall_mode(snapshot):
        height = d.get('z') if d.get('z') is not None else d.get('y')
        if height is not None:
            sides.append(f'{_fmt(height)} H')
    else:
        if d.get('y') is not None:
            sides.append(f'{_fmt(d["y"])} D')
        if d.get('z') is not None:
            sides.append(f'{_fmt(d["z"])} H')
    return ' x '.join(sides)


def build_intent(snapshot):
    name = _name(snapshot).lower()
    parts = []
    head = f'Looking for {_article(name)} {name}' if name else 'Looking for a product'
    if snapshot.get('type'):
        head += f' ({snapshot["type"]})'
    parts.append(head)
    if snapshot.get('style'):
        parts.append(f'in {snapshot["style"]} style')
    material = snapshot.get('material')
    if material:
        parts.append('made of mixed materials' if material == 'mixed' else f'made of {material}')
    size = _size_phrase(snapshot)
    if size:
        parts.append(f'fitting within {size} inches')
    if snapshot.get('weight') is not None:
        parts.append(f'weighing at most {_fmt(snapshot["weight"])} lb')
    minimum = snapshot.get('priceMin')
    limit = snapshot.get('priceLimit')
    if minimum is not None and limit is not None:
        parts.append(f'priced between {_usd(minimum)} and {_usd(limit)}')
    elif limit is not None:
        parts.append(f'priced under {_usd(limit)}')
    elif minimum is not None:
        parts.append(f'priced over {_usd(minimum)}')
    similar = [s for s in (snapshot.get('similarItems') or []) if s]
    if similar:
        parts.append('similar to: ' + ', '.join(similar))
    space = (snapshot.get('space') or {}).get('type')
    if space:
        parts.append('for a space' if space == 'other' else f'for a {space} space')
    return ', '.join(parts) + '.'


def build_payload(snapshot, strategy, previous_queries=()):
    cursor = None
    if strategy == 'more':
        src = pick_more_source(previous_queries)
        if src is None:
            raise ValueError('no earlier query has another page')
        query = (src.get('payload') or {}).get('query') or src.get('query')
        cursor = (src.get('response_meta') or {}).get('cursor')
    else:
        query = strategy_query(snapshot, strategy)
    filters = {'available': True, 'ships_to': {'country': 'US'}}
    price = {}
    minimum = snapshot.get('priceMin')
    if minimum is not None:
        price['min'] = int(round(float(minimum) * 100))
    limit = snapshot.get('priceLimit')
    if limit is not None:
        price['max'] = int(round(float(limit) * 100))
    if price:
        filters['price'] = price
    payload = {
        'query': query,
        'context': {
            'intent': build_intent(snapshot),
            'address_country': 'US',
            'currency': 'USD',
            'language': 'en-US',
        },
        'filters': filters,
        'pagination': {'limit': settings.SEARCHER_PAGE_LIMIT},
    }
    if cursor:
        payload['pagination']['cursor'] = cursor
    return copy.deepcopy(payload)
