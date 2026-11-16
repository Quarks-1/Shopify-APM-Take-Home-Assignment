"""Pure parsing of raw UCP product dicts: dimensions, weight and materials.

All lengths come out in inches, weights in lb. Nothing here touches the
network or the database.
"""
import html
import re

MIN_LEN, MAX_LEN = 0.0, 600.0  # exclusive lower, inclusive upper (inches)

# ------------------------------------------------------------------ text

_TAG_RE = re.compile(r'<[^>]*>')
_CTRL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]')
_WS_RE = re.compile(r'\s+')


def clean_text(value, limit=None):
    """Strip tags/control chars, collapse whitespace, optionally truncate."""
    if value is None:
        return ''
    text = str(value)
    text = _TAG_RE.sub(' ', text)
    text = html.unescape(text)
    text = _CTRL_RE.sub(' ', text)
    text = _WS_RE.sub(' ', text).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + '…'
    return text


# ---------------------------------------------------------------- units

_UNIT_IN = {
    'in': 1.0, 'inch': 1.0, 'inches': 1.0, '"': 1.0, '″': 1.0, '”': 1.0, "''": 1.0,
    'cm': 1 / 2.54, 'centimeter': 1 / 2.54, 'centimeters': 1 / 2.54,
    'centimetre': 1 / 2.54, 'centimetres': 1 / 2.54,
    'mm': 1 / 25.4, 'millimeter': 1 / 25.4, 'millimeters': 1 / 25.4,
    'millimetre': 1 / 25.4, 'millimetres': 1 / 25.4,
    'm': 39.3701, 'meter': 39.3701, 'meters': 39.3701, 'metre': 39.3701, 'metres': 39.3701,
    'ft': 12.0, 'feet': 12.0, 'foot': 12.0, "'": 12.0, '′': 12.0,
}

_NUM = r'\d+(?:\.\d+)?'
_FT_UNIT = r"(?:'|′|ft\.?|feet|foot)"
_IN_UNIT = r'(?:"|″|”|\'\'|in\b|inch(?:es)?\b)'
_OTHER_UNIT = (
    r'(?:cm\b|mm\b|centimet(?:er|re)s?\b|millimet(?:er|re)s?\b|meters?\b|metres?\b|m\b)'
)
_LABEL = (
    r'(?:length\b|width\b|depth\b|height\b|diameter\b|dia\b|(?-i:[LWDH])\b)'
)
_UNIT_ANY = rf'(?:{_IN_UNIT}|{_OTHER_UNIT}|{_FT_UNIT})'

# One measured token: [label] number [unit] [label] [unit]
_TOKEN_RE = re.compile(
    rf'''
    (?<![\d./])
    (?:(?P<plab>{_LABEL})\s*[:=]?\s*)?
    (?:
        (?P<ft>{_NUM})\s*{_FT_UNIT}\s*(?:(?P<ftin>{_NUM})\s*(?:{_IN_UNIT})?)?
        |
        (?P<n>{_NUM})\s*(?P<u>{_IN_UNIT}|{_OTHER_UNIT})?
    )
    (?(plab)|(?:\s*(?P<slab>{_LABEL}))?(?:\s*(?P<u2>{_IN_UNIT}|{_OTHER_UNIT}))?)
    ''',
    re.IGNORECASE | re.VERBOSE,
)
_SEP_RE = re.compile(r'^\s*(?:x|×|\*|by)\s*$', re.IGNORECASE)
_PAREN_LABELS_RE = re.compile(
    rf'^\s*\(\s*({_LABEL})\s*(?:x|×)\s*({_LABEL})(?:\s*(?:x|×)\s*({_LABEL}))?\s*\)',
    re.IGNORECASE,
)
_PAREN_LABEL1_RE = re.compile(rf'^\s*\(\s*({_LABEL})\s*\)', re.IGNORECASE)


def _unit_factor(u):
    return _UNIT_IN.get(u.lower()) if u else None


def _canon_label(raw):
    """Canonical axis letter: L, W, D, H (diameter -> 'O')."""
    if not raw:
        return None
    r = raw.lower()
    if r.startswith('di'):
        return 'O'
    return r[0].upper()


class _Tok:
    __slots__ = ('start', 'end', 'value', 'factor', 'label')

    def __init__(self, start, end, value, factor, label):
        self.start, self.end, self.value, self.factor, self.label = (
            start, end, value, factor, label,
        )


def _tokens(text):
    out = []
    for m in _TOKEN_RE.finditer(text):
        label = _canon_label(m.group('plab') or m.group('slab'))
        if m.group('ft') is not None:
            value = float(m.group('ft')) * 12.0 + (
                float(m.group('ftin')) if m.group('ftin') else 0.0
            )
            factor = 1.0  # already inches
        else:
            value = float(m.group('n'))
            u = m.group('u') or m.group('u2')
            factor = _unit_factor(u)
        out.append(_Tok(m.start(), m.end(), value, factor, label))
    return out


def _chains(text):
    """Group tokens joined by x / by separators. Returns list of (tokens, end)."""
    toks = _tokens(text)
    chains, cur = [], []
    for t in toks:
        if cur and _SEP_RE.match(text[cur[-1].end:t.start]):
            cur.append(t)
        else:
            if cur:
                chains.append(cur)
            cur = [t]
    if cur:
        chains.append(cur)
    return chains


def _resolve(chain, default_unit):
    """Convert chain values to inches. Returns list of values or None."""
    explicit = [t.factor for t in chain if t.factor is not None]
    fallback = explicit[-1] if explicit else default_unit
    vals = []
    for t in chain:
        f = t.factor if t.factor is not None else fallback
        if f is None:
            return None
        v = t.value * f
        if not (MIN_LEN < v <= MAX_LEN):
            return None
        vals.append(v)
    return vals


def _r(v):
    return round(v, 2)


def _map_labels(pairs):
    """pairs: [(letter, inches)] -> dims dict (x/y/z)."""
    letters = [l for l, _ in pairs]
    dims = {}
    for letter, v in pairs:
        if letter == 'L':
            dims.setdefault('x', v)
        elif letter == 'W':
            if 'L' in letters:
                dims.setdefault('y', v)
            else:
                dims.setdefault('x', v)
        elif letter == 'D':
            dims.setdefault('y', v)
        elif letter == 'H':
            dims.setdefault('z', v)
        elif letter == 'O':  # diameter
            dims.setdefault('x', v)
            dims.setdefault('y', v)
    return dims


def parse_dimensions_text(text, default_unit=None, allow_longest=True):
    """Parse free text. Returns (dims_dict, longest) in inches; both may be empty/None.

    default_unit: inch factor used for unitless numbers (1.0 for spec lines),
    or None to ignore unitless numbers.
    """
    text = clean_text(text)
    if not text:
        return {}, None
    chains = _chains(text)
    # 1. multi-value chains (a x b [x c]) or labeled values
    for chain in chains:
        labeled_any = any(t.label for t in chain)
        if len(chain) < 2:
            continue
        vals = _resolve(chain, default_unit)
        if vals is None:
            continue
        if labeled_any:
            if len(chain) > 3:
                continue
            dims = _map_labels([(t.label, v) for t, v in zip(chain, vals) if t.label])
            if dims:
                return {k: _r(v) for k, v in dims.items()}, None
            continue
        if len(chain) > 3:
            continue
        tail = text[chain[-1].end:]
        pm = _PAREN_LABELS_RE.match(tail)
        if pm:
            letters = [_canon_label(g) for g in pm.groups() if g]
            if len(letters) == len(vals):
                dims = _map_labels(list(zip(letters, vals)))
                if dims:
                    return {k: _r(v) for k, v in dims.items()}, None
        keys = ['x', 'y', 'z']
        return {k: _r(v) for k, v in zip(keys, vals)}, None
    # 1b. several separately labeled single values: "Width 22 cm, Height 150 cm, Depth 22 cm"
    singles = [c[0] for c in chains if len(c) == 1 and c[0].label]
    if len(singles) >= 2:
        pairs = []
        for t in singles:
            vals = _resolve([t], default_unit)
            if vals is None:
                pairs = []
                break
            pairs.append((t.label, vals[0]))
        dims = _map_labels(pairs) if pairs else {}
        if dims:
            return {k: _r(v) for k, v in dims.items()}, None
    # 2. unlabeled single values and "(length)" style suffixes: one value
    for chain in chains:
        if len(chain) != 1:
            continue
        t = chain[0]
        if t.factor is None and default_unit is None:
            continue
        vals = _resolve(chain, default_unit)
        if vals is None:
            continue
        label = t.label
        if label is None:
            pm = _PAREN_LABEL1_RE.match(text[t.end:])
            label = _canon_label(pm.group(1)) if pm else None
        if label:
            return {k: _r(v) for k, v in _map_labels([(label, vals[0])]).items()}, None
        if allow_longest:
            return {}, _r(vals[0])
    return {}, None


# ------------------------------------------------------------ dimensions

_DIM_KEY_RE = re.compile(
    r'^(?:(?:overall|product|assembled|item|unit|finished|full|total|approx\.?|approximate)\s+)*'
    r'(?:dimensions?|size)(?:\s*\([^)]*\))?$',
    re.IGNORECASE,
)
_DIM_TAIL_RE = re.compile(r'(?:dimensions?|size)(?:\s*\([^)]*\))?$', re.IGNORECASE)
_DIM_KEY_EXCLUDE_RE = re.compile(
    r'\b(?:package|packaging|shipping|carton|box|seat|cushion|cord|cable)\b',
    re.IGNORECASE,
)
_AXIS_KEY_RE = re.compile(
    r'^(?:(?:overall|product|assembled|item|unit|finished|total)\s+)*'
    r'(width|depth|height|length|diameter)$',
    re.IGNORECASE,
)
_QUALIFIED_AXIS_KEY_RE = re.compile(
    r'^(?:(?:overall|product|assembled|item|unit|finished|total)\s+)*'
    r'(width|depth|height|length|diameter)'
    r'(?:\s*(?:\([^)]*\)|[-–]\s*.+))?$',
    re.IGNORECASE,
)
_QUALIFIED_AXIS_BAD_RE = re.compile(
    r'\b(?:mattress|compatible|clearance|underbed|underside)\b', re.IGNORECASE,
)
_MEASURE_RANGE_RE = re.compile(r'\d+(?:\.\d+)?\s*-\s*\d')
_BEDDING_SIZE_OPT_RE = re.compile(r'bed(?:ding)?\s*size|mattress\s*size', re.IGNORECASE)
_QUALIFIED_ROW_START_RE = re.compile(
    r'((?:(?:overall|product)\s+)?(?:width|length|height|depth)\b'
    r'(?:\s*(?:\([^)]*\)|[-–]\s*[^:]{2,50}))?\s*:\s*)',
    re.IGNORECASE,
)
_DESC_ANCHOR_RE = re.compile(
    r'(?:dimensions?|measures|measuring|overall size|overall)\s*[:\-]?\s*', re.IGNORECASE
)


def _split_spec(entry):
    key, sep, val = entry.partition(':')
    if not sep:
        return None, entry
    return key.strip(), val.strip()


def _specs(product):
    md = product.get('metadata') or {}
    raw = md.get('tech_specs') or []
    return [clean_text(s) for s in raw if isinstance(s, (str, int, float)) and clean_text(s)]


def _axis_letter_from_key(key):
    if not key or _QUALIFIED_AXIS_BAD_RE.search(key):
        return None
    m = _QUALIFIED_AXIS_KEY_RE.match(key)
    if not m:
        return None
    axis = m.group(1).lower()
    return {'width': 'W', 'depth': 'D', 'height': 'H', 'length': 'L', 'diameter': 'O'}[axis]


def _discrete_inches(val):
    """Inch measurements from a spec value, skipping ranges and cm duplicates."""
    val = clean_text(val)
    if not val or _MEASURE_RANGE_RE.search(val):
        return None
    out = []
    for t in _tokens(val):
        factor = t.factor if t.factor is not None else 1.0
        v = _r(t.value * factor)
        if not (MIN_LEN < v <= MAX_LEN):
            continue
        if out and abs(v - out[-1]) / max(out[-1], 0.01) < 0.06:
            continue
        out.append(v)
    return out or None


def _pick_discrete(vals, size_index):
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    if 0 <= size_index < len(vals):
        return vals[size_index]
    return None


def _bedding_size_index(product, variant):
    """Index of the selected mattress/bedding size among product option values."""
    selected = ''
    for o in variant.get('options') or []:
        if not isinstance(o, dict):
            continue
        name = str(o.get('name') or '')
        if _BEDDING_SIZE_OPT_RE.search(name):
            selected = clean_text(str(o.get('label') or ''))
            break
    if not selected:
        return 0
    for opt in product.get('options') or []:
        if not isinstance(opt, dict) or not _BEDDING_SIZE_OPT_RE.search(str(opt.get('name') or '')):
            continue
        labels = [
            clean_text(str(v.get('label') or ''))
            for v in (opt.get('values') or [])
            if isinstance(v, dict)
        ]
        for i, label in enumerate(labels):
            if label.lower() == selected.lower():
                return i
        return 0
    return 0


def _qualified_axis_pairs(entries, size_index):
    """entries: list of (key, val). Returns [(letter, inches), ...]."""
    pairs = []
    for key, val in entries:
        if key is None:
            continue
        letter = _axis_letter_from_key(key)
        if not letter:
            continue
        vals = _discrete_inches(val)
        v = _pick_discrete(vals, size_index)
        if v is not None:
            pairs.append((letter, v))
    return pairs


def _qualified_rows_from_text(text):
    """Split free text into (key, value) rows for overall width/length/height lines."""
    text = clean_text(text)
    if not text:
        return []
    rows = []
    matches = list(_QUALIFIED_ROW_START_RE.finditer(text))
    for i, m in enumerate(matches):
        key = clean_text(m.group(1).rstrip(':').strip())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[m.end():end].strip()
        if key and val:
            rows.append((key, val))
    return rows


def _dims_from_qualified_pairs(pairs):
    if not pairs:
        return {}
    return {k: _r(v) for k, v in _map_labels(pairs).items()}


def _footprint_dims(dims):
    return dims.get('x') is not None and dims.get('y') is not None


def _from_qualified_overall_axes(product):
    """Per-size overall width/length/height rows (e.g. Clearhalo bedding tables)."""
    variant = _first_variant(product)
    size_index = _bedding_size_index(product, variant)
    desc = clean_text((product.get('description') or {}).get('plain'))
    desc_rows = _qualified_rows_from_text(desc)
    desc_dims = _dims_from_qualified_pairs(_qualified_axis_pairs(desc_rows, size_index))
    if _footprint_dims(desc_dims):
        return desc_dims, 'description'
    spec_rows = []
    for entry in _specs(product):
        key, val = _split_spec(entry)
        if key is not None and _axis_letter_from_key(key):
            spec_rows.append((key, val))
    spec_dims = _dims_from_qualified_pairs(_qualified_axis_pairs(spec_rows, size_index))
    if _footprint_dims(spec_dims):
        return spec_dims, 'tech_specs'
    merged_pairs = list(_qualified_axis_pairs(desc_rows, size_index))
    seen = {letter for letter, _ in merged_pairs}
    for key, val in spec_rows:
        letter = _axis_letter_from_key(key)
        if not letter or letter in seen:
            continue
        v = _pick_discrete(_discrete_inches(val), size_index)
        if v is not None:
            merged_pairs.append((letter, v))
            seen.add(letter)
    merged = _dims_from_qualified_pairs(merged_pairs)
    if _footprint_dims(merged):
        return merged, 'description' if desc_rows else 'tech_specs'
    return {}, None


def _from_specs(product):
    specs = _specs(product)
    # 1. dimension / size lines
    for entry in specs:
        key, val = _split_spec(entry)
        if key is None or not _DIM_KEY_RE.match(key):
            continue
        dims, longest = parse_dimensions_text(val, default_unit=1.0)
        if dims or longest:
            return dims, longest
    # 1b. other dimension / size keys (e.g. lampshade), not sub-part or package lines
    for entry in specs:
        key, val = _split_spec(entry)
        if key is None or _DIM_KEY_RE.match(key):
            continue
        if not _DIM_TAIL_RE.search(key) or _DIM_KEY_EXCLUDE_RE.search(key):
            continue
        dims, longest = parse_dimensions_text(val, default_unit=1.0)
        if dims or longest:
            return dims, longest
    # 2. separate Width/Depth/Height/Length/Diameter lines
    dims = {}
    for entry in specs:
        key, val = _split_spec(entry)
        if key is None:
            continue
        m = _AXIS_KEY_RE.match(key)
        if not m:
            continue
        d, longest = parse_dimensions_text(val, default_unit=1.0)
        v = longest if longest is not None else None
        if v is None and len(d) == 1:
            v = next(iter(d.values()))
        if v is None:
            continue
        for k, axis in _map_labels([({'width': 'W', 'depth': 'D', 'height': 'H', 'length': 'L',
                                      'diameter': 'O'}[m.group(1).lower()], v)]).items():
            dims.setdefault(k, _r(axis))
    if dims:
        return dims, None
    return {}, None


def _first_variant(product):
    variants = product.get('variants') or []
    return variants[0] if variants and isinstance(variants[0], dict) else {}


_OPTION_MEASURE_NAME_RE = re.compile(
    r'^(?:width|length|depth|height|size|bedding\s*size|mattress\s*size)$',
    re.IGNORECASE,
)


def _option_labels(variant):
    out = []
    for o in variant.get('options') or []:
        if isinstance(o, dict):
            label = o.get('label')
            if isinstance(label, str):
                out.append(label)
    return out


def _from_variant_options(variant):
    """Pair variant option name + label (e.g. Length: 200 cm, Width: 120 cm)."""
    parts = []
    for o in variant.get('options') or []:
        if not isinstance(o, dict):
            continue
        name = clean_text(str(o.get('name') or ''))
        label = o.get('label')
        if not name or not isinstance(label, str) or not label.strip():
            continue
        if not _OPTION_MEASURE_NAME_RE.match(name):
            continue
        parts.append(f'{name}: {clean_text(label)}')
    if len(parts) < 2:
        return {}, None
    return parse_dimensions_text(', '.join(parts), default_unit=None)


def _from_description(product):
    desc = clean_text((product.get('description') or {}).get('plain'))
    if not desc:
        return {}, None
    for m in _DESC_ANCHOR_RE.finditer(desc):
        window = desc[m.end(): m.end() + 90]
        dims, longest = parse_dimensions_text(window, default_unit=None, allow_longest=False)
        if dims:
            return dims, None
    return {}, None


def parse_dimensions(product):
    """Returns (dims, longest, source). dims has any subset of x/y/z (inches)."""
    dims, longest = _from_specs(product)
    if dims or longest:
        return dims, longest, 'tech_specs'
    dims, qsrc = _from_qualified_overall_axes(product)
    if dims:
        return dims, None, qsrc
    dims, longest = parse_dimensions_text(product.get('title') or '', default_unit=None)
    if dims or longest:
        return dims, longest, 'title'
    variant = _first_variant(product)
    dims, longest = _from_variant_options(variant)
    if dims or longest:
        return dims, longest, 'options'
    best_dims, best_longest = {}, None
    for label in _option_labels(variant):
        d, lg = parse_dimensions_text(label, default_unit=None)
        if d and not best_dims:
            best_dims = d
        if lg and best_longest is None:
            best_longest = lg
    if best_dims or best_longest:
        return best_dims, best_longest, 'options'
    dims, longest = _from_description(product)
    if dims or longest:
        return dims, longest, 'description'
    return {}, None, None


# ---------------------------------------------------------------- weight

_WEIGHT_UNITS = [
    (re.compile(r'^(?:lbs?\.?|pounds?)$', re.I), 1.0),
    (re.compile(r'^(?:oz\.?|ounces?)$', re.I), 1 / 16),
    (re.compile(r'^(?:kgs?\.?|kilograms?|kilos?)$', re.I), 2.20462),
    (re.compile(r'^(?:g|grams?)$', re.I), 0.00220462),
]
_WEIGHT_VAL_RE = re.compile(rf'({_NUM})\s*([A-Za-z]+\.?)')
_WEIGHT_KEY_BAD = re.compile(
    r'capacity|limit|max|load|support|shipping|package|packag|box|carton|gross|cushion|per\b',
    re.I,
)


def parse_weight(product):
    """Weight in lb from specs like 'Weight: 38 lb', or None."""
    for entry in _specs(product):
        key, val = _split_spec(entry)
        if key is None or 'weight' not in key.lower() or _WEIGHT_KEY_BAD.search(key):
            continue
        for m in _WEIGHT_VAL_RE.finditer(val):
            for rx, factor in _WEIGHT_UNITS:
                if rx.match(m.group(2)):
                    lb = float(m.group(1)) * factor
                    if 0 < lb <= 5000:
                        return round(lb, 2)
                    break
    return None


# -------------------------------------------------------------- materials

MATERIAL_KEYWORDS = {
    'wood': [
        'wood', 'wooden', 'oak', 'walnut', 'pine', 'teak', 'acacia', 'mango', 'birch', 'ash',
        'bamboo', 'mdf', 'plywood', 'rattan', 'maple', 'cherry', 'beech', 'elm', 'hardwood',
        'veneer', 'mahogany', 'cedar', 'poplar', 'cane',
    ],
    'metal': [
        'metal', 'steel', 'iron', 'aluminum', 'aluminium', 'brass', 'chrome', 'zinc', 'wrought',
    ],
    'fabric': [
        'fabric', 'linen', 'velvet', 'polyester', 'cotton', 'wool', 'boucle', 'bouclé',
        'upholstered', 'chenille', 'silk', 'viscose', 'jute', 'canvas', 'tweed', 'microfiber',
    ],
    'glass': ['glass'],
    'ceramic': ['ceramic', 'porcelain', 'stoneware', 'terracotta', 'earthenware'],
}
_MATERIAL_RES = {
    mat: re.compile(r'\b(?:' + '|'.join(words) + r')\b', re.IGNORECASE)
    for mat, words in MATERIAL_KEYWORDS.items()
}


def materials_from_text(text):
    """Material enums found in text, in order of first appearance, deduped."""
    found = []
    for mat, rx in _MATERIAL_RES.items():
        m = rx.search(text or '')
        if m:
            found.append((m.start(), mat))
    return [mat for _, mat in sorted(found)]


def parse_materials(product):
    """Returns (materials, material_text). 'mixed' is never emitted."""
    values = []
    for entry in _specs(product):
        key, val = _split_spec(entry)
        if key is not None and 'material' in key.lower() and val:
            values.append(val)
    seen, deduped = set(), []
    for v in values:
        if v.lower() not in seen:
            seen.add(v.lower())
            deduped.append(v)
    material_text = ', '.join(deduped)
    mats = materials_from_text(material_text)
    for mat in materials_from_text(clean_text(product.get('title'))):
        if mat not in mats:
            mats.append(mat)
    return mats, material_text
