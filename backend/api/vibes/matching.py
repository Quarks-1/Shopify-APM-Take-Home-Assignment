"""Category-matched vibes for a search.

`run_vibes` picks the space's thumbs-up Pinterest pins that fit the object
being searched: a pin is only ever used when the object's `type` (furniture,
lighting, ...) is one of the pin's categories, so wall art never steers a
sofa search. Matching pins are ranked by how well the pin's item noun fits the
object's name, newest import first, and snapshotted onto the run.
"""
import io
import logging
import re
import unicodedata
from collections import Counter

from django.conf import settings
from PIL import Image

from ..models import Placement, Space, SpaceInspiration, VoteChoice
from ..searcher import like_images
from . import patterns as pattern_lib
from . import paths
from .primary_type import primary_type

logger = logging.getLogger(__name__)

# Words that carry no meaning for matching an item against an object.
STOPWORDS = frozenset({
    'the', 'and', 'a', 'an', 'of', 'for', 'with', 'in', 'on', 'to', 'set', 'piece', 'pcs',
    'pc', 'pair', 'style', 'item', 'small', 'large', 'big', 'new',
})

# Every word on the left means the word on the right (after singularizing).
ALIASES = {
    'couch': 'sofa', 'settee': 'sofa', 'loveseat': 'sofa', 'sectional': 'sofa',
    'chesterfield': 'sofa', 'davenport': 'sofa',
    'carpet': 'rug', 'runner': 'rug',
    'chandelier': 'pendant',
    'bedside': 'nightstand',
    'cupboard': 'cabinet', 'sideboard': 'cabinet', 'credenza': 'cabinet',
    'bookshelf': 'bookcase', 'shelf': 'shelving', 'shelve': 'shelving',
    'ottoman': 'pouf', 'footstool': 'pouf',
    'painting': 'art', 'poster': 'art', 'artwork': 'art',
}
# Seating nouns (after ALIASES) that relate to each other: a corner seat is
# worth a look when the pin shows a sofa or an armchair.
SEATING = frozenset({
    'seat', 'chair', 'armchair', 'sofa', 'couch', 'loveseat', 'settee', 'sectional', 'bench',
    'stool', 'pouf', 'ottoman', 'daybed', 'chaise', 'recliner',
})
TOP_SUMMARY = 5
PHOTO_TYPE = 'image/jpeg'
CROP_PAD = 0.08  # of the box's width / height, on each side
CROP_MAX_SIDE = 1024
CROP_QUALITY = 88
CROP_SUFFIX = paths.CROP_SUFFIX
PRIMARY_WEIGHT = 2  # summary weight of pins whose own type is the object's
MAX_EXPLICIT_PHOTOS = 5
MAX_SUGGESTED_PINS = 8

# Soft style/material expansions so compound terms like "Japandi" or "bouclé"
# recognize related board labels when scoring pins deterministically.
ATTR_EXPANSIONS = {
    'japandi': ('japandi', 'scandinavian', 'minimalist', 'japanese', 'organic'),
    'boucle': ('boucle', 'textured', 'fabric', 'upholstered'),
    'mcm': ('mid', 'century', 'modern'),
}


def object_space_ids(obj):
    """Ids of the spaces the object is placed in, in placement order."""
    return list(Placement.objects.filter(object=obj).order_by('pk')
                .values_list('space_id', flat=True))


# ------------------------------------------------------------ normalization

def _singular(word):
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith(('sses', 'ches', 'shes', 'xes')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def _ascii_fold(text):
    norm = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(ch for ch in norm if not unicodedata.combining(ch))


def normalize_item(text):
    """Meaningful words of an item phrase: lowercase, accent-folded, singular, aliased."""
    words = re.findall(r'[a-z0-9]+', _ascii_fold(text).lower())
    out = []
    for word in words:
        if word in STOPWORDS:
            continue
        word = _singular(word)
        out.append(ALIASES.get(word, word))
    return tuple(out)


def _contains(big, small):
    """True when `small` is a contiguous run of words inside `big`."""
    n = len(small)
    return n > 0 and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def item_score(item, snapshot):
    """2: the pin's item and the object's name (or a similar item) are the same
    phrase or one holds the other. 1: they share a meaningful word, or both
    name a kind of seating. 0: nothing."""
    words = normalize_item(item)
    if not words:
        return 0
    targets = [snapshot.get('name')] + list(snapshot.get('similarItems') or [])
    best = 0
    for target in targets:
        other = normalize_item(target)
        if not other:
            continue
        if words == other or _contains(words, other) or _contains(other, words):
            return 2
        if set(words) & set(other) or (set(words) & SEATING and set(other) & SEATING):
            best = 1
    return best


# ------------------------------------------------------------------ vibes

def _top(counter):
    return [value for value, _ in counter.most_common(TOP_SUMMARY)]


def _labels(values):
    out = []
    for value in values or []:
        text = str(value).strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def _crop_file(target, row):
    """Path of the cached crop of `target` around the row's item box, written
    when missing or older than the row's labels; None without a usable box or
    when anything goes wrong."""
    box = row.item_box
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    try:
        out = target.with_name(f'{target.stem}{CROP_SUFFIX}')
        if paths.vibes_dir() not in out.parents:
            return None
        stamp = row.categorized_at.timestamp() if row.categorized_at else None
        if out.is_file() and stamp is not None and out.stat().st_mtime >= stamp:
            return out
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
        if x1 <= x0 or y1 <= y0:
            return None
        pad_x, pad_y = (x1 - x0) * CROP_PAD, (y1 - y0) * CROP_PAD
        x0, y0 = max(0.0, x0 - pad_x), max(0.0, y0 - pad_y)
        x1, y1 = min(1.0, x1 + pad_x), min(1.0, y1 + pad_y)
        with Image.open(target) as image:
            image.load()
            width, height = image.size
            left, top = int(x0 * width), int(y0 * height)
            right, bottom = max(left + 1, round(x1 * width)), max(top + 1, round(y1 * height))
            part = image.crop((left, top, min(width, right), min(height, bottom))).convert('RGB')
        part.thumbnail((CROP_MAX_SIDE, CROP_MAX_SIDE))
        buf = io.BytesIO()
        part.save(buf, format='JPEG', quality=CROP_QUALITY)
        out.write_bytes(buf.getvalue())
        if out.stat().st_size > like_images.MAX_BYTES:
            return None
        return out
    except Exception:  # a bad box or image must never break a search
        logger.exception('vibes: could not crop pin %s', row.pin_id)
        return None


def _photo(index, inspiration):
    target = paths.resolve_inside(inspiration.image_path) if inspiration.image_path else None
    if target is None:
        return None
    crop = _crop_file(target, inspiration)
    chosen = crop or target
    return {
        'index': index, 'pinId': inspiration.pin_id, 'path': str(chosen),
        'contentType': PHOTO_TYPE, 'bytes': chosen.stat().st_size, 'cropped': crop is not None,
    }


def _choose_photos(pins, limit=None):
    """Up to `limit` (default VIBES_MAX_PHOTOS) photos from the ranked pins,
    pins with an item box first (their photo can be cropped to the item)."""
    cap = settings.VIBES_MAX_PHOTOS if limit is None else limit
    photos = []
    ordered = [row for _, row in pins if row.item_box] + [row for _, row in pins if not row.item_box]
    for row in ordered:
        if len(photos) >= cap:
            break
        photo = _photo(len(photos), row)
        if photo is not None:
            photos.append(photo)
    return photos


def _choose_ordered_photos(rows, limit=MAX_EXPLICIT_PHOTOS):
    """Photos in the user's explicit selection order (up to `limit`)."""
    photos = []
    for row in rows:
        if len(photos) >= limit:
            break
        photo = _photo(len(photos), row)
        if photo is not None:
            photos.append(photo)
    return photos


def _attr_tokens(*texts):
    tokens = set()
    for text in texts:
        for word in re.findall(r'[a-z0-9]+', _ascii_fold(text).lower()):
            if len(word) < 3 or word in STOPWORDS:
                continue
            s = _singular(word)
            tokens.add(s)
            tokens.add(ALIASES.get(s, s))
            for exp in ATTR_EXPANSIONS.get(s, ()):
                tokens.add(exp)
    return tokens


def suggest_pins(rows, snapshot, prompt_text=''):
    """Deterministically split `rows` into (similar_rows, category_rows) for
    `snapshot`. Only pins whose categories include `snapshot['type']` appear.
    `similar_rows` holds up to MAX_SUGGESTED_PINS close item/attribute matches;
    `category_rows` holds the remaining pins in that category."""
    wanted = (snapshot or {}).get('type')
    if not wanted:
        return [], []
    aug = dict(snapshot or {})
    if prompt_text:
        aug['similarItems'] = list(aug.get('similarItems') or []) + [prompt_text]
    query_tokens = _attr_tokens(
        aug.get('name'), aug.get('description'), aug.get('style'), aug.get('material'),
        prompt_text, *(aug.get('similarItems') or []),
    )
    scored = []
    seen = set()
    for row in rows:
        if row.pin_id in seen or wanted not in (row.categories or []):
            continue
        seen.add(row.pin_id)
        iscore = item_score(row.item, aug)
        own = primary_type(row.item, row.categories) == wanted
        pin_tokens = _attr_tokens(
            row.item, row.description,
            *(row.styles or []), *(row.colors or []),
            *(row.materials or []), *(row.patterns or []),
        )
        attr_hits = len(query_tokens & pin_tokens)
        is_similar = iscore >= 1 or (own and attr_hits >= 2)
        scored.append((is_similar, iscore, own, attr_hits, row.board_rank, row.pk, row))

    similar_scored = [t for t in scored if t[0]]
    similar_scored.sort(key=lambda t: (-t[1], not t[2], -t[3], t[4], t[5]))
    similar_rows = [t[6] for t in similar_scored[:MAX_SUGGESTED_PINS]]
    similar_ids = {r.pin_id for r in similar_rows}

    rest_scored = [t for t in scored if t[6].pin_id not in similar_ids]
    rest_scored.sort(key=lambda t: (not t[2], -t[3], t[4], t[5]))
    category_rows = [t[6] for t in rest_scored]
    return similar_rows, category_rows


def _explicit_vibes(space_ids, ref_ids, snapshot, mode):
    """Build a `vibes` snapshot solely from the user's explicitly selected
    `referencePins` (preserving selection order; no board backfill)."""
    board_by_space = dict(
        Space.objects.filter(pk__in=space_ids, vibe_board_id__isnull=False)
        .values_list('pk', 'vibe_board_id')
    )
    preferred_boards = []
    for sid in space_ids:
        bid = board_by_space.get(sid)
        if bid is not None and bid not in preferred_boards:
            preferred_boards.append(bid)
    board_rank = {bid: idx for idx, bid in enumerate(preferred_boards)}

    qs = SpaceInspiration.objects.filter(
        pin_id__in=ref_ids, vote=VoteChoice.UP, categorized_at__isnull=False
    ).select_related('vibe_import', 'board')
    rows = list(qs)
    if not rows:
        return None
    rows.sort(key=lambda r: (board_rank.get(r.board_id, len(board_rank)), r.board_rank, r.pk))
    by_pin = {}
    for row in rows:
        by_pin.setdefault(row.pin_id, row)
    selected = [by_pin[pid] for pid in ref_ids if pid in by_pin][:MAX_EXPLICIT_PHOTOS]
    if not selected:
        return None

    theme = pattern_lib.board_theme(selected)
    counters = {name: Counter() for name in ('styles', 'colors', 'materials', 'patterns')}
    for row in selected:
        for name, counter in counters.items():
            for label in _labels(getattr(row, name)):
                counter[label] += 2
    photos = [] if mode == 'like' else _choose_ordered_photos(selected, limit=MAX_EXPLICIT_PHOTOS)
    return {
        'spaceIds': space_ids,
        'matched': len(selected),
        'explicitReferences': True,
        'theme': theme,
        'pins': [
            {
                'pinId': row.pin_id, 'item': row.item, 'categories': list(row.categories or []),
                'styles': _labels(row.styles), 'colors': _labels(row.colors),
                'materials': _labels(row.materials), 'patterns': _labels(row.patterns),
                'description': row.description, 'score': 2,
            }
            for row in selected
        ],
        'summary': {name: _top(counter) for name, counter in counters.items()},
        'photos': photos,
    }


def run_vibes(obj, snapshot, mode):
    """The run's `vibes` snapshot, or None when no pin matches (or the object
    sits in no space with a selected vibe board and has no explicit referencePins)."""
    space_ids = object_space_ids(obj)
    raw_refs = (snapshot or {}).get('referencePins') or []
    ref_ids = [p for p in raw_refs if isinstance(p, str) and p]
    if ref_ids:
        return _explicit_vibes(space_ids, ref_ids, snapshot, mode)
    if not space_ids:
        return None
    wanted = (snapshot or {}).get('type')
    if not wanted:
        return None
    board_by_space = dict(
        Space.objects.filter(pk__in=space_ids, vibe_board_id__isnull=False)
        .values_list('pk', 'vibe_board_id')
    )
    board_ids = []
    for sid in space_ids:
        bid = board_by_space.get(sid)
        if bid is not None and bid not in board_ids:
            board_ids.append(bid)
    if not board_ids:
        return None
    rows = list(SpaceInspiration.objects
                .filter(board_id__in=board_ids, vote=VoteChoice.UP, categorized_at__isnull=False)
                .select_related('vibe_import', 'board'))
    unique = {}
    for row in rows:
        unique.setdefault(row.pin_id, row)
    theme = pattern_lib.board_theme(unique.values())
    scored = []
    for row in rows:
        if wanted not in (row.categories or []):  # the category gate
            continue
        imported = row.vibe_import.created_at.timestamp() if row.vibe_import else None
        own = primary_type(row.item, row.categories) == wanted
        themed = pattern_lib.carries_theme(row.patterns, theme)
        score = item_score(row.item, snapshot)
        scored.append((
            not (own and themed), -score, not own, not themed,
            imported is None, -(imported or 0),
            row.board_rank, row.pk, row, score, own,
        ))
    scored.sort(key=lambda t: t[:8])
    pins, seen = [], set()
    for key in scored:
        row = key[8]
        if row.pin_id in seen:
            continue
        seen.add(row.pin_id)
        pins.append((key[9], row, key[10]))
    if not pins:
        return None

    counters = {name: Counter() for name in ('styles', 'colors', 'materials', 'patterns')}
    for score, row, own in pins:
        weight = (score + 1) * (PRIMARY_WEIGHT if own else 1)
        for name, counter in counters.items():
            for label in _labels(getattr(row, name)):
                counter[label] += weight
    ranked = [(score, row) for score, row, _ in pins]
    photos = [] if mode == 'like' else _choose_photos(ranked)
    return {
        'spaceIds': space_ids,
        'matched': len(pins),
        'theme': theme,
        'pins': [
            {
                'pinId': row.pin_id, 'item': row.item, 'categories': list(row.categories or []),
                'styles': _labels(row.styles), 'colors': _labels(row.colors),
                'materials': _labels(row.materials), 'patterns': _labels(row.patterns),
                'description': row.description, 'score': score,
            }
            for score, row in ranked[:settings.VIBES_SNAPSHOT_PINS]
        ],
        'summary': {name: _top(counter) for name, counter in counters.items()},
        'photos': photos,
    }
