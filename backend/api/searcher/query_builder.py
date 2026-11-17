"""Deterministic query strategies and UCP payloads.

Hard filters (price, availability, shipping) always go in the payload; no
strategy and no caller can drop them.
"""
import base64
import copy
import re
from pathlib import Path

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


def strategies(snapshot, used_queries, previous_queries=(), like=None):
    """Available strategies in ladder order. `like` is a like run's
    like_source: it switches to the photo ladder."""
    if like is not None:
        return like_strategies(like, previous_queries)
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


# ------------------------------------------------------------- like mode
#
# A like run seeds the catalog's `like` parameter (one item per request) with
# photos of a reference product instead of building text queries. Ladder:
# like:0..N (photo + the object's name), like:product (the product id),
# like-visual:0..N (photo alone, a wider net), then more (next page).

LIKE_IMAGE_RE = re.compile(r'^<image (\d+),')
LIKE_TITLE_LEN = 120


def like_strategies(like, previous_queries=()):
    """Like-mode ladder. A strategy counts as used once any round ran it
    (like rounds share one query text, so text de-duplication can't be used)."""
    used = {_pq(q)['strategy'] for q in previous_queries or []}
    count = len(like.get('images') or [])
    ladder = [f'like:{i}' for i in range(count)]
    if like.get('productGid'):
        ladder.append('like:product')
    ladder += [f'like-visual:{i}' for i in range(count)]
    out = [s for s in ladder if s not in used]
    if pick_more_source(previous_queries) is not None:
        out.append('more')
    return out


def _like_query(snapshot):
    return _name(snapshot) or _type_noun(snapshot)


def _image_item(like, index, redact=False):
    """`like` item for cached photo `index`. With redact=True the data is a
    placeholder (`<image 2, 123456 bytes>`) and no file is read: that form is
    what gets stored, so base64 never lands in the database."""
    for image in like.get('images') or []:
        if image.get('index') != index:
            continue
        if redact:
            data = f'<image {index}, {image.get("bytes", 0)} bytes>'
        else:
            try:
                data = base64.b64encode(Path(image['path']).read_bytes()).decode('ascii')
            except OSError as exc:
                raise ValueError(f'cannot read like image {index}: {exc}')
        return {'image': {'content_type': image['contentType'], 'data': data}}
    raise ValueError(f'unknown like image: {index}')


def _like_item_from_payload(like, payload, redact=False):
    """Rebuild the `like` item of an earlier (stored, redacted) payload."""
    items = (payload or {}).get('like') or []
    item = items[0] if items and isinstance(items[0], dict) else {}
    if item.get('id'):
        return {'id': item['id']}
    match = LIKE_IMAGE_RE.match(str((item.get('image') or {}).get('data') or ''))
    if match:
        return _image_item(like, int(match.group(1)), redact)
    raise ValueError('earlier like query has no usable like item')


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
    if snapshot.get('minRating') is not None:
        parts.append(f'rated at least {_fmt(snapshot["minRating"])} stars')
    similar = [s for s in (snapshot.get('similarItems') or []) if s]
    if similar:
        parts.append('similar to: ' + ', '.join(similar))
    space = (snapshot.get('space') or {}).get('type')
    if space:
        parts.append('for a space' if space == 'other' else f'for a {space} space')
    return ', '.join(parts) + '.'


def _like_parts(snapshot, strategy, previous_queries, like, redact):
    """(query or None, like item, cursor) of a like-mode round."""
    if strategy == 'more':
        src = pick_more_source(previous_queries)
        if src is None:
            raise ValueError('no earlier query has another page')
        payload = src.get('payload') or {}
        item = _like_item_from_payload(like, payload, redact)
        return payload.get('query') or None, item, (src.get('response_meta') or {}).get('cursor')
    kind, _, arg = strategy.partition(':')
    if strategy == 'like:product' and like.get('productGid'):
        return _like_query(snapshot) or None, {'id': like['productGid']}, None
    if kind in ('like', 'like-visual') and arg.isdigit():
        item = _image_item(like, int(arg), redact)
        return (_like_query(snapshot) or None) if kind == 'like' else None, item, None
    raise ValueError(f'unknown strategy: {strategy}')


def build_stored_payload(snapshot, strategy, previous_queries=(), like=None):
    """The payload to save on CatalogQuery: `build_payload` with image data
    replaced by a placeholder."""
    return build_payload(snapshot, strategy, previous_queries, like, redact=True)


def build_payload(snapshot, strategy, previous_queries=(), like=None, redact=False):
    """The ucp catalog search payload. With `like` (a like run's like_source)
    it holds real base64 image data: never store it, use build_stored_payload."""
    cursor = None
    like_item = None
    if like is not None:
        query, like_item, cursor = _like_parts(snapshot, strategy, previous_queries, like, redact)
    elif strategy == 'more':
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
    min_rating = snapshot.get('minRating')
    if min_rating is not None:
        filters['rating'] = {'variant': {'min': float(min_rating)}}
    intent = build_intent(snapshot)
    if like is not None and like.get('title'):
        title = re.sub(r'\s+', ' ', str(like['title'])).strip()[:LIKE_TITLE_LEN]
        intent += f' Visually similar to: {title}.'
    payload = {
        'query': query,
        'context': {
            'intent': intent,
            'address_country': 'US',
            'currency': 'USD',
            'language': 'en-US',
        },
        'filters': filters,
        'pagination': {'limit': settings.SEARCHER_PAGE_LIMIT},
    }
    if cursor:
        payload['pagination']['cursor'] = cursor
    if like_item is not None:
        payload['like'] = [like_item]
        if not query:
            del payload['query']  # photo alone: the catalog allows no query with `like`
    return copy.deepcopy(payload)
