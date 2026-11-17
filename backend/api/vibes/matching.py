"""Category-matched vibes for a search.

`run_vibes` picks the space's thumbs-up Pinterest pins that fit the object
being searched: a pin is only ever used when the object's `type` (furniture,
lighting, ...) is one of the pin's categories, so wall art never steers a
sofa search. Matching pins are ranked by how well the pin's item noun fits the
object's name, newest import first, and snapshotted onto the run.
"""
import re
from collections import Counter

from django.conf import settings

from ..models import Placement, SpaceInspiration, VoteChoice
from . import paths

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
TOP_SUMMARY = 5
PHOTO_TYPE = 'image/jpeg'


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


def normalize_item(text):
    """Meaningful words of an item phrase: lowercase, singular, aliased."""
    words = re.findall(r'[a-z0-9]+', str(text or '').lower())
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
    phrase or one holds the other. 1: they share a meaningful word. 0: nothing."""
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
        if set(words) & set(other):
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


def _photo(index, inspiration):
    target = paths.resolve_inside(inspiration.image_path) if inspiration.image_path else None
    if target is None:
        return None
    return {
        'index': index, 'pinId': inspiration.pin_id, 'path': str(target),
        'contentType': PHOTO_TYPE, 'bytes': target.stat().st_size,
    }


def run_vibes(obj, snapshot, mode):
    """The run's `vibes` snapshot, or None when no pin matches (or the object
    sits in no space)."""
    space_ids = object_space_ids(obj)
    if not space_ids:
        return None
    wanted = (snapshot or {}).get('type')
    if not wanted:
        return None
    rows = (SpaceInspiration.objects
            .filter(space_id__in=space_ids, vote=VoteChoice.UP, categorized_at__isnull=False)
            .select_related('vibe_import'))
    scored = []
    for row in rows:
        if wanted not in (row.categories or []):  # the category gate
            continue
        imported = row.vibe_import.created_at.timestamp() if row.vibe_import else None
        scored.append((
            -item_score(row.item, snapshot),
            imported is None, -(imported or 0),
            row.board_rank, row.pk, row,
        ))
    scored.sort(key=lambda t: t[:5])
    pins, seen = [], set()
    for key in scored:
        row = key[5]
        if row.pin_id in seen:
            continue
        seen.add(row.pin_id)
        pins.append((-key[0], row))
    if not pins:
        return None

    counters = {name: Counter() for name in ('styles', 'colors', 'materials')}
    for score, row in pins:
        for name, counter in counters.items():
            for label in _labels(getattr(row, name)):
                counter[label] += score + 1
    photos = []
    if mode != 'like':
        for _, row in pins:
            if len(photos) >= settings.VIBES_MAX_PHOTOS:
                break
            photo = _photo(len(photos), row)
            if photo is not None:
                photos.append(photo)
    return {
        'spaceIds': space_ids,
        'matched': len(pins),
        'pins': [
            {
                'pinId': row.pin_id, 'item': row.item, 'categories': list(row.categories or []),
                'styles': _labels(row.styles), 'colors': _labels(row.colors),
                'materials': _labels(row.materials), 'description': row.description,
                'score': score,
            }
            for score, row in pins[:settings.VIBES_SNAPSHOT_PINS]
        ],
        'summary': {name: _top(counter) for name, counter in counters.items()},
        'photos': photos,
    }
