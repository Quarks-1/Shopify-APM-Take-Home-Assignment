"""Patterns and prints (leopard print, stripe, ...) on inspiration pins.

The pin labeler names the patterns it sees, but the print often lands only in
the free-text description, so every label is also run through a small lexicon
that maps words and phrases to one canonical pattern name. `board_theme` then
looks across a whole board for a print that dominates it.
"""
import re

ANIMAL = 'animal print'
PATTERNS_MAX = 6  # canonical patterns kept per pin
THEME_SHARE = 0.3  # a theme must be on this share of the categorized pins...
THEME_MIN_PINS = 3  # ...and on at least this many
THEME_PATTERNS = 4

# phrase -> canonical pattern. Phrases are matched on lowercase words
# (punctuation and hyphens count as spaces), longest phrase first.
LEXICON = {
    'leopard print': 'leopard print', 'leopard spot': 'leopard print',
    'leopard spots': 'leopard print', 'leopard': 'leopard print',
    'zebra stripe': 'zebra print', 'zebra stripes': 'zebra print',
    'zebra print': 'zebra print', 'zebra': 'zebra print',
    'cheetah print': 'cheetah print', 'cheetah': 'cheetah print',
    'tiger stripe': 'tiger print', 'tiger stripes': 'tiger print',
    'tiger print': 'tiger print', 'tiger': 'tiger print',
    'cowhide': 'cowhide', 'cow hide': 'cowhide', 'cow print': 'cowhide',
    'cow spot': 'cowhide', 'cow spots': 'cowhide',
    'giraffe print': 'giraffe print', 'giraffe': 'giraffe print',
    'snakeskin': 'snakeskin', 'snake skin': 'snakeskin', 'snake print': 'snakeskin',
    'python print': 'snakeskin', 'snake pattern': 'snakeskin',
    'tortoiseshell': 'tortoiseshell', 'tortoise shell': 'tortoiseshell',
    'tortoise': 'tortoiseshell',
    'animal print': ANIMAL, 'animal prints': ANIMAL,
    'stripe': 'stripe', 'stripes': 'stripe', 'striped': 'stripe',
    'check': 'check', 'checks': 'check', 'checked': 'check', 'checkered': 'check',
    'checkerboard': 'check', 'gingham': 'check', 'plaid': 'check',
    'floral': 'floral',
    'polka dot': 'polka dot', 'polka dots': 'polka dot', 'polkadot': 'polka dot',
    'polkadots': 'polka dot',
}

# canonical pattern -> family. Patterns not listed here are their own family.
FAMILIES = {
    'leopard print': ANIMAL, 'zebra print': ANIMAL, 'cheetah print': ANIMAL,
    'tiger print': ANIMAL, 'cowhide': ANIMAL, 'giraffe print': ANIMAL,
    'snakeskin': ANIMAL, 'tortoiseshell': ANIMAL, ANIMAL: ANIMAL,
}

_PHRASES = sorted(LEXICON, key=lambda p: (-len(p.split()), -len(p)))
_PHRASE_RES = [(re.compile(r'(?<![a-z0-9])' + re.escape(p) + r'(?![a-z0-9])'), LEXICON[p])
               for p in _PHRASES]


def _words(text):
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


def find_patterns(text):
    """Canonical patterns named in `text`, in order of appearance."""
    work = _words(text)
    hits = []
    for regex, canonical in _PHRASE_RES:
        for match in regex.finditer(work):
            hits.append((match.start(), canonical))
        work = regex.sub(lambda m: ' ' * len(m.group()), work)  # a longer phrase hides its words
    hits.sort()
    out = []
    for _, canonical in hits:
        if canonical not in out:
            out.append(canonical)
    return out


def canonical(name):
    """One pattern name as a canonical one. Names the lexicon does not know
    stay as cleaned lowercase text ('' for empty input)."""
    found = find_patterns(name)
    if found:
        return found[0]
    return _words(name)


def canonical_list(names, limit=PATTERNS_MAX):
    """Canonical, deduped list of pattern names."""
    out = []
    for name in names if isinstance(names, (list, tuple)) else []:
        value = canonical(name) if isinstance(name, str) else ''
        if value and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def family(pattern):
    """The family a canonical pattern belongs to (itself when it has none)."""
    return FAMILIES.get(pattern, pattern)


def _get(row, key):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def derive_patterns(row):
    """Canonical patterns for a pin: the model's own list plus lexicon hits in
    its description, styles, materials and item. `row` is a model row or a dict.

    Which field is the model's list (never the stored, already text-derived
    `patterns` of a model row, or stale hits could never be dropped):
      - model row: `model_patterns` only.
      - dict: `model_patterns` if the key is present, else `patterns` (a label).
    """
    if isinstance(row, dict):
        own = row['model_patterns'] if 'model_patterns' in row else row.get('patterns')
    else:
        own = getattr(row, 'model_patterns', None)
    out = canonical_list(own or [], PATTERNS_MAX)
    texts = [_get(row, 'description'), _get(row, 'item')]
    for key in ('styles', 'materials'):
        value = _get(row, key)
        texts += list(value) if isinstance(value, (list, tuple)) else []
    for text in texts:
        for found in find_patterns(text):
            if found not in out and len(out) < PATTERNS_MAX:
                out.append(found)
    return out


def board_theme(rows):
    """The look that dominates a board, or None. `rows` are all categorized
    pins (model rows or dicts with `patterns`). A theme is a family (animal
    print) or a single pattern on at least 30% of them and at least 3.

        {'family': 'animal print', 'patterns': ['leopard print', ...],
         'pins': 66, 'boardPins': 69}
    """
    rows = list(rows)
    total = len(rows)
    if not total:
        return None
    per_pattern, per_family, members = {}, {}, {}
    for row in rows:
        pats = canonical_list(_get(row, 'patterns') or [])
        for pat in pats:
            per_pattern[pat] = per_pattern.get(pat, 0) + 1
        for fam in {family(p) for p in pats}:
            per_family[fam] = per_family.get(fam, 0) + 1
            members.setdefault(fam, set()).update(p for p in pats if family(p) == fam)
    best = None
    for fam, count in per_family.items():
        if count < THEME_MIN_PINS or count / total < THEME_SHARE:
            continue
        if best is None or count > best[1]:
            best = (fam, count)
    if best is None:
        return None
    fam, count = best
    names = sorted(members[fam], key=lambda p: (-per_pattern[p], p))
    specific = [p for p in names if p != fam]
    names = (specific or names)[:THEME_PATTERNS]
    return {'family': fam, 'patterns': names, 'pins': count, 'boardPins': total}


def carries_theme(patterns, theme):
    """True when any of the pin's patterns belongs to the theme's family."""
    if not theme:
        return False
    fam = theme.get('family')
    return any(family(p) == fam for p in patterns or [])
