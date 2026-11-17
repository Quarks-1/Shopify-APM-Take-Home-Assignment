"""Deterministic query strategies and UCP payloads.

Hard filters (price, availability, shipping) always go in the payload; no
strategy and no caller can drop them.
"""
import base64
import copy
import math
import re
from collections import Counter
from pathlib import Path

from django.conf import settings

from .filters import price_bounds

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
        'accepted_count': getattr(q, 'accepted_count', None),
    }


def _photo_round(q):
    """True for a vibe photo round or a `more` page chained from one."""
    if str(q.get('strategy') or '').startswith('vibe-photo:'):
        return True
    return q.get('strategy') == 'more' and is_vibe_query(q)


def pick_more_source(previous_queries):
    """The earlier query to continue: most passing candidates among those that
    still have a next page and a cursor which no later query has consumed.
    Ties go to the earliest query. A vibe photo round (or a `more` page of one)
    that the agent has accepted nothing from is not continued; an unknown
    accepted count (None) allows it. Returns a dict or None."""
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
        if q.get('accepted_count') == 0 and _photo_round(q):
            continue
        if best is None or (q.get('passing_count') or 0) > (best.get('passing_count') or 0):
            best = q
    return best


def strategies(snapshot, used_queries, previous_queries=(), like=None, refine=None, vibes=None):
    """Available strategies in ladder order. `like` is a like run's
    like_source: it switches to the photo ladder; `refine` (a refine run's
    feedback) puts the vote-driven rounds first; `vibes` (a run's space
    inspiration) puts the vibe rounds before the text ladder."""
    if like is not None:
        return like_strategies(like, previous_queries)
    if refine is not None:
        return refine_strategies(snapshot, refine, previous_queries, vibes)
    if vibes is not None:
        return (vibe_strategies(vibes, previous_queries)
                + strategies(snapshot, used_queries, previous_queries))
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
    min_txt = None if minimum is None else (
        f'around {_usd(minimum)}' if snapshot.get('priceMinFuzzy') else _usd(minimum))
    max_txt = None if limit is None else (
        f'around {_usd(limit)}' if snapshot.get('priceLimitFuzzy') else _usd(limit))
    if minimum is not None and limit is not None:
        parts.append(f'priced between {min_txt} and {max_txt}')
    elif limit is not None:
        parts.append(f'priced around {_usd(limit)}' if snapshot.get('priceLimitFuzzy')
                     else f'priced under {_usd(limit)}')
    elif minimum is not None:
        parts.append(f'priced around {_usd(minimum)} or more' if snapshot.get('priceMinFuzzy')
                     else f'priced over {_usd(minimum)}')
    if snapshot.get('minRating') is not None:
        parts.append(f'rated at least {_fmt(snapshot["minRating"])} stars')
    similar = [s for s in (snapshot.get('similarItems') or []) if s]
    if similar:
        parts.append('similar to: ' + ', '.join(similar))
    space = (snapshot.get('space') or {}).get('type')
    if space:
        parts.append('for a space' if space == 'other' else f'for a {space} space')
    return ', '.join(parts) + '.'


# ------------------------------------------------------------ vibe rounds
#
# A run whose object matched the space's Pinterest vibes gets extra rounds:
# `vibe` (a text query shaped by the board's dominant pattern, else the pins'
# top style and material), `vibe:2` (the same with the next pattern; only when
# the board has a theme) and vibe-photo:N (catalog `like` seeded with the Nth
# matching pin photo, cropped to its item, plus the theme query). The vibe
# sentence goes into the intent of every round.

VIBE_QUERY_LEN = 120
VIBE_INTENT_LEN = 300
VIBE_SENTENCE_LABELS = 3
VIBE_TEXT_ROUNDS = ('vibe', 'vibe:2')


def _is_vibe_round(strategy):
    return strategy in VIBE_TEXT_ROUNDS or strategy.startswith('vibe-photo:')


def _vibe_image_item(vibes, payload, redact):
    """The real (or redacted) image item for a payload whose `like` item is a
    vibe photo placeholder, or None when it holds no such placeholder."""
    items = (payload or {}).get('like') or []
    item = items[0] if items and isinstance(items[0], dict) else {}
    match = LIKE_IMAGE_RE.match(str((item.get('image') or {}).get('data') or ''))
    if not match:
        return None
    return _image_item({'images': (vibes or {}).get('photos') or []}, int(match.group(1)), redact)


def is_vibe_query(q):
    """True for a vibe round, or a `more` page of a photo round (its query is
    the theme query or the object name, which the plain `name` rung must not be
    skipped for)."""
    q = _pq(q)
    if _is_vibe_round(q['strategy']):
        return True
    if q['strategy'] != 'more':
        return False
    items = (q.get('payload') or {}).get('like') or []
    return bool(items and isinstance(items[0], dict)
                and LIKE_IMAGE_RE.match(str((items[0].get('image') or {}).get('data') or '')))


def _theme(vibes):
    """The board's dominant look ({family, patterns, ...}) or None."""
    theme = (vibes or {}).get('theme')
    return theme if isinstance(theme, dict) and theme.get('family') else None


def theme_terms(vibes):
    """(first, second) pattern words for the theme queries: the top specific
    pattern, then the next distinct one or the family name. ('', '') without a
    theme; the second is '' when there is nothing different to ask for."""
    theme = _theme(vibes)
    if theme is None:
        return '', ''
    found = [str(p).strip() for p in theme.get('patterns') or [] if str(p).strip()]
    family = str(theme['family']).strip()
    first = found[0] if found else family
    if len(found) > 1:
        return first, found[1]
    return first, family if family != first else ''


def vibe_strategies(vibes, previous_queries=()):
    """vibe, vibe:2 (with a theme), vibe-photo:N (each once, minus those
    already run)."""
    used = {_pq(q)['strategy'] for q in previous_queries or []}
    ladder = ['vibe']
    if theme_terms(vibes)[1]:
        ladder.append('vibe:2')
    ladder += [f'vibe-photo:{p.get("index")}' for p in (vibes or {}).get('photos') or []]
    return [s for s in ladder if s not in used]


def _summary(vibes, key):
    return [str(v) for v in (((vibes or {}).get('summary') or {}).get(key) or []) if v]


def _vibe_name(snapshot):
    return _name(snapshot) or _type_noun(snapshot)


def vibe_query(snapshot, vibes, second=False):
    """With a theme: its top pattern (second=True: the next one) then the
    object's name. Without: top style + top material, then the name."""
    name = _vibe_name(snapshot)
    first, other = theme_terms(vibes)
    if first:
        term = other if second else first
        if not term:
            raise ValueError('no second theme term')
        return _join(term, name)[:VIBE_QUERY_LEN]
    if second:
        raise ValueError('no theme for vibe:2')
    styles, materials = _summary(vibes, 'styles'), _summary(vibes, 'materials')
    return _join(styles[0] if styles else '', materials[0] if materials else '', name
                 )[:VIBE_QUERY_LEN]


def vibe_photo_query(snapshot, vibes):
    """Query sent with a vibe photo: the theme query, else the object's name.
    Never empty while the object has a name."""
    first, _ = theme_terms(vibes)
    return (_join(first, _vibe_name(snapshot))[:VIBE_QUERY_LEN] if first
            else _like_query(snapshot)) or None


def vibe_sentence(vibes):
    """With a theme: ' Their space inspiration is dominated by animal print
    (leopard print, zebra print) - strongly prefer pieces in these prints. It
    also favors <styles>, <colors>.' Without: ' Their space inspiration favors
    <styles>, <colors>, <materials>.' ('' when there is nothing to say.)"""
    theme = _theme(vibes)
    if theme is not None:
        family = str(theme['family'])
        found = [str(p) for p in theme.get('patterns') or [] if p and str(p) != family]
        listed = f' ({", ".join(found[:VIBE_SENTENCE_LABELS])})' if found else ''
        text = (f' Their space inspiration is dominated by {family}{listed} \u2014 strongly '
                f'prefer pieces in {"these prints" if found else "this print"}.')
        labels = _summary(vibes, 'styles')[:VIBE_SENTENCE_LABELS]
        labels += _summary(vibes, 'colors')[:VIBE_SENTENCE_LABELS]
        if labels:
            text += ' It also favors ' + ', '.join(labels) + '.'
        return text[:VIBE_INTENT_LEN]
    labels = []
    for key in ('styles', 'colors', 'materials'):
        labels += _summary(vibes, key)[:VIBE_SENTENCE_LABELS]
    if not labels:
        return ''
    return (' Their space inspiration favors ' + ', '.join(labels) + '.')[:VIBE_INTENT_LEN]


# ------------------------------------------------------------ refine mode
#
# A refine run ("Refresh") is shaped by the row's thumbs votes. Ladder:
# pref (a vote-derived query, or one the agent wrote, with the preferences in
# the intent), liked:N (catalog `like` seeded by liked product N), then the
# normal text ladder with the preference sentence appended to the intent.
# pref and liked:N rounds narrow the price filter to the liked items' range.

REFINE_LIKED_ROUNDS = 3
PREF_QUERY_LEN = 120
PREF_INTENT_LEN = 400
PREF_ITEMS = 3
PREF_TITLE_LEN = 60
PRICE_WINDOW = (0.85, 1.15)
STOPWORDS = {
    'and', 'the', 'with', 'for', 'from', 'set', 'piece', 'pcs', 'inch', 'inches', 'cm',
    'new', 'sale', 'free', 'shipping', 'your', 'our',
}


def _is_refine_round(strategy):
    return strategy == 'pref' or strategy.startswith('liked:')


def liked_gids(feedback):
    """Products the liked:N rounds seed with, in order."""
    gids = [i.get('productGid') for i in (feedback or {}).get('liked') or []]
    return [g for g in gids if g][:REFINE_LIKED_ROUNDS]


def refine_strategies(snapshot, feedback, previous_queries=(), vibes=None):
    """pref, liked:N (each once), the vibe rounds when the run has vibes, then
    the normal ladder. The normal rungs are de-duplicated against earlier text
    rounds only, not pref/liked/vibe ones."""
    prev = [_pq(q) for q in previous_queries or []]
    used = {q['strategy'] for q in prev}
    ladder = ['pref'] + [f'liked:{i}' for i in range(len(liked_gids(feedback)))]
    out = [s for s in ladder if s not in used]
    if vibes is not None:
        out += vibe_strategies(vibes, prev)
    text = [q['query'] for q in prev
            if not _is_refine_round(q['strategy']) and not is_vibe_query(q)]
    return out + strategies(snapshot, text, prev)


def _clean_title(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:PREF_TITLE_LEN]


def _common(lists, threshold, limit):
    """Words present in at least `threshold` of the lists, most common first."""
    counts = Counter(w for words in lists for w in dict.fromkeys(words))
    return [w for w, n in counts.most_common() if n >= threshold][:limit]


def pref_query(snapshot, feedback):
    """Object name plus the materials and title words the liked items share."""
    liked = (feedback or {}).get('liked') or []
    name = _name(snapshot) or _type_noun(snapshot)
    if not liked:
        return strategy_query(snapshot, 'exact') or name
    threshold = 1 if len(liked) == 1 else max(2, math.ceil(len(liked) / 2))
    materials = _common(
        [[m.lower() for m in i.get('materials') or [] if isinstance(m, str)] for i in liked],
        threshold, 2)
    words = []
    if len(liked) > 1:
        skip = set(normalize_query(name).split()) | set(' '.join(materials).split())
        titles = [
            [w for w in re.findall(r'[a-z][a-z-]{2,}', str(i.get('title') or '').lower())
             if w not in STOPWORDS and w not in skip]
            for i in liked
        ]
        words = _common(titles, threshold, 2)
    return _join(*words, *materials, name)[:PREF_QUERY_LEN]


def preference_sentence(feedback):
    """' Prefers items like: ... Avoid: ...' from the row's votes, or ''."""
    feedback = feedback or {}
    likes = []
    for item in (feedback.get('liked') or [])[:PREF_ITEMS]:
        title = _clean_title(item.get('title'))
        mats = ', '.join((item.get('materials') or [])[:2])
        if title:
            likes.append(f'{title} ({mats})' if mats else title)
    avoids = [_clean_title(i.get('title')) for i in (feedback.get('disliked') or [])[:PREF_ITEMS]]
    parts = []
    if likes:
        parts.append('Prefers items like: ' + '; '.join(likes) + '.')
    avoids = [a for a in avoids if a]
    if avoids:
        parts.append('Avoid: ' + '; '.join(avoids) + '.')
    return (' ' + ' '.join(parts))[:PREF_INTENT_LEN] if parts else ''


def liked_price_window(snapshot, feedback):
    """{min, max} cents around the liked prices (with two or more priced liked
    items), clipped to the object's own bounds; None otherwise."""
    prices = [i['price'] for i in (feedback or {}).get('liked') or []
              if isinstance(i.get('price'), (int, float)) and i['price'] > 0]
    if len(prices) < 2:
        return None
    lo = round(min(prices) * PRICE_WINDOW[0] * 100)
    hi = round(max(prices) * PRICE_WINDOW[1] * 100)
    obj_lo, obj_hi = price_bounds(snapshot, settings.SEARCHER_PRICE_FUZZ)
    if obj_lo is not None:
        lo = max(lo, obj_lo)
    if obj_hi is not None:
        hi = min(hi, obj_hi)
    return {'min': lo, 'max': hi} if lo <= hi else None


def _refine_more(previous_queries, vibes=None, redact=False):
    """The next page of a refine run's earlier round: its exact payload
    (query, like item, price window, intent) with the cursor. A vibe photo
    round's stored placeholder becomes the real image again."""
    src = pick_more_source(previous_queries)
    if src is None:
        raise ValueError('no earlier query has another page')
    payload = copy.deepcopy(src.get('payload') or {})
    payload.setdefault('pagination', {})['cursor'] = (src.get('response_meta') or {}).get('cursor')
    if vibes is not None:
        item = _vibe_image_item(vibes, payload, redact)
        if item is not None:
            payload['like'] = [item]
    return payload


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


def build_stored_payload(snapshot, strategy, previous_queries=(), like=None, refine=None,
                         vibes=None):
    """The payload to save on CatalogQuery: `build_payload` with image data
    replaced by a placeholder."""
    return build_payload(snapshot, strategy, previous_queries, like, redact=True, refine=refine,
                         vibes=vibes)


def build_payload(snapshot, strategy, previous_queries=(), like=None, redact=False, refine=None,
                  vibes=None):
    """The ucp catalog search payload. With `like` (a like run's like_source)
    it holds real base64 image data: never store it, use build_stored_payload.
    `refine` is {feedback, query?, intent?} of a refine run (query and intent
    are the agent's own, for the pref round only). `vibes` is the run's space
    inspiration snapshot: vibe-photo rounds then hold real base64 as well."""
    cursor = None
    like_item = None
    feedback = (refine or {}).get('feedback') or {}
    if refine is not None and strategy == 'more':
        return _refine_more(previous_queries, vibes, redact)
    if like is not None:
        query, like_item, cursor = _like_parts(snapshot, strategy, previous_queries, like, redact)
    elif refine is not None and strategy == 'pref':
        query = refine.get('query') or pref_query(snapshot, feedback)
    elif refine is not None and strategy.startswith('liked:'):
        gids = liked_gids(feedback)
        arg = strategy.partition(':')[2]
        if not arg.isdigit() or int(arg) >= len(gids):
            raise ValueError(f'unknown strategy: {strategy}')
        query, like_item = _like_query(snapshot) or None, {'id': gids[int(arg)]}
    elif vibes is not None and strategy in VIBE_TEXT_ROUNDS:
        query = vibe_query(snapshot, vibes, second=strategy == 'vibe:2')
    elif vibes is not None and strategy.startswith('vibe-photo:'):
        arg = strategy.partition(':')[2]
        if not arg.isdigit():
            raise ValueError(f'unknown strategy: {strategy}')
        like_item = _image_item({'images': vibes.get('photos') or []}, int(arg), redact)
        query = vibe_photo_query(snapshot, vibes)
    elif strategy == 'more':
        src = pick_more_source(previous_queries)
        if src is None:
            raise ValueError('no earlier query has another page')
        query = (src.get('payload') or {}).get('query') or src.get('query')
        cursor = (src.get('response_meta') or {}).get('cursor')
        if vibes is not None:
            like_item = _vibe_image_item(vibes, src.get('payload'), redact)
    else:
        query = strategy_query(snapshot, strategy)
    filters = {'available': True, 'ships_to': {'country': 'US'}}
    price = {}
    lo_cents, hi_cents = price_bounds(snapshot, settings.SEARCHER_PRICE_FUZZ)
    if lo_cents is not None:
        price['min'] = lo_cents
    if hi_cents is not None:
        price['max'] = hi_cents
    if price:
        filters['price'] = price
    min_rating = snapshot.get('minRating')
    if min_rating is not None:
        filters['rating'] = {'variant': {'min': float(min_rating)}}
    intent = build_intent(snapshot)
    if like is not None and like.get('title'):
        title = re.sub(r'\s+', ' ', str(like['title'])).strip()[:LIKE_TITLE_LEN]
        intent += f' Visually similar to: {title}.'
    if refine is not None:
        if strategy == 'pref' and refine.get('intent'):
            intent += ' ' + refine['intent']
        else:
            intent += preference_sentence(feedback)
        window = liked_price_window(snapshot, feedback) if _is_refine_round(strategy) else None
        if window:
            filters['price'] = window
    if vibes is not None:
        intent += vibe_sentence(vibes)
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
