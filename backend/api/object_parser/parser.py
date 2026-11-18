"""Run the headless Haiku object-parser sub-agent with deterministic fallback
and strict schema/anti-hallucination post-validation."""
import logging
import math
import re
import unicodedata
import uuid
from pathlib import Path

from django.conf import settings

from api import claude_cli
from api.models import AgentMode, Material, ObjectType, SpaceInspiration, Style, VoteChoice
from api.searcher import runner
from api.serializers import ValidationFailed, inspiration_to_json, validate_object
from api.vibes import matching

from . import prompts

logger = logging.getLogger(__name__)

OBJECT_TYPES = set(ObjectType.values)
MATERIALS = set(Material.values)
STYLES = set(Style.values)

# Keyword evidence required in the prompt to keep a fixed Style enum value.
# Prevents near-neighbor hallucinations (e.g. forcing "scandinavian" for "Japandi").
STYLE_EVIDENCE = {
    'modern': ('modern', 'mcm'),
    'vintage': ('vintage', 'retro', 'antique'),
    'brutalist': ('brutalist', 'brutalism'),
    'scandinavian': ('scandinavian', 'scandi', 'nordic'),
    'industrial': ('industrial', 'loft'),
    'mid-century': ('mid-century', 'mid century', 'midcentury', 'mcm'),
}

MATERIAL_KEYWORDS = {
    'fabric': (
        'fabric', 'boucle', 'linen', 'velvet', 'cotton', 'wool', 'chenille', 'tweed',
        'upholster', 'upholstered', 'plush', 'canvas', 'corduroy', 'microfiber', 'woven',
        'tapestry', 'cushion', 'cushioning', 'tufted', 'jute', 'sisal', 'shag',
    ),
    'wood': (
        'wood', 'wooden', 'oak', 'walnut', 'teak', 'pine', 'ash', 'birch', 'maple',
        'acacia', 'mahogany', 'cherry', 'bamboo', 'plywood', 'veneer', 'timber',
    ),
    'metal': (
        'metal', 'steel', 'brass', 'iron', 'chrome', 'aluminum', 'aluminium',
        'bronze', 'copper', 'nickel', 'powder-coated', 'stainless',
    ),
    'glass': ('glass', 'mirrored', 'acrylic', 'lucite'),
    'ceramic': ('ceramic', 'porcelain', 'stoneware', 'terracotta', 'earthenware', 'clay'),
}

TYPE_HINTS = (
    ('wall-art', ('wall art', 'painting', 'poster', 'framed print', 'art print', 'canvas art', 'tapestry', 'mural')),
    ('rug', ('rug', 'carpet', 'runner', 'mat')),
    ('lighting', ('lamp', 'chandelier', 'pendant', 'sconce', 'lantern', 'light fixture', 'ceiling light')),
    ('storage', ('bookcase', 'bookshelf', 'shelving', 'storage', 'organizer', 'wardrobe', 'armoire', 'cubby', 'rack')),
    ('decor', ('vase', 'planter', 'mirror', 'candle', 'sculpture', 'figurine', 'throw pillow', 'pillow', 'tray', 'clock', 'bowl')),
    ('furniture', (
        'couch', 'sofa', 'sectional', 'loveseat', 'settee', 'chair', 'armchair', 'recliner',
        'table', 'desk', 'bed', 'headboard', 'nightstand', 'dresser', 'credenza', 'sideboard',
        'cabinet', 'bench', 'stool', 'ottoman', 'pouf', 'chaise', 'daybed', 'console',
    )),
)

# Realistic upper-normal footprints (W, D in inches) when neither prompt nor canvas gives size.
ITEM_FOOTPRINTS = (
    (('sectional',), (108, 84)),
    (('couch', 'sofa', 'chesterfield', 'davenport'), (90, 40)),
    (('loveseat', 'settee', 'daybed', 'chaise'), (68, 38)),
    (('armchair', 'lounge chair', 'accent chair', 'recliner'), (36, 36)),
    (('chair', 'stool', 'ottoman', 'pouf'), (26, 26)),
    (('dining table',), (78, 40)),
    (('coffee table',), (54, 30)),
    (('console', 'sideboard', 'credenza', 'dresser'), (66, 20)),
    (('desk',), (60, 30)),
    (('side table', 'end table', 'nightstand'), (24, 22)),
    (('king bed',), (80, 84)),
    (('queen bed', 'bed'), (64, 84)),
    (('bench',), (52, 18)),
    (('bookcase', 'bookshelf', 'shelving'), (36, 16)),
)

DEFAULT_FOOTPRINTS = {
    'furniture': (48, 30),
    'lighting': (20, 20),
    'rug': (96, 120),
    'wall-art': (36, 48),  # (x, z) on walls
    'storage': (36, 18),
    'decor': (16, 16),
}

NUMBER_WORDS = frozenset({
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
    'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen',
    'eighteen', 'nineteen', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy',
    'eighty', 'ninety', 'hundred', 'thousand', 'half', 'quarter',
})

FILLER_PREFIX_RE = re.compile(
    r'^(?:i\s+(?:want|need|would\s+like)|looking\s+for|find\s+me|add|a|an|the)\s+',
    re.IGNORECASE,
)


def _ascii_fold(text):
    norm = unicodedata.normalize('NFKD', str(text or ''))
    return ''.join(ch for ch in norm if not unicodedata.combining(ch))


def clean_prompt(text):
    cleaned = ''.join(ch if ch.isprintable() or ch in '\n\t' else ' ' for ch in str(text or ''))
    return re.sub(r'\s+', ' ', cleaned).strip()[:prompts.PROMPT_MAX_LEN]


def _has_numeric_cue(prompt_text):
    lower = _ascii_fold(prompt_text).lower()
    if re.search(r'\d', lower):
        return True
    words = set(re.findall(r'[a-z]+', lower))
    return bool(words & NUMBER_WORDS)


def _style_supported(style, prompt_text):
    if style not in STYLES:
        return False
    lower = _ascii_fold(prompt_text).lower()
    return any(
        re.search(rf'(?<![a-z]){re.escape(kw)}(?![a-z])', lower)
        for kw in STYLE_EVIDENCE.get(style, ())
    )


def _detect_style(prompt_text):
    for style in ('mid-century', 'scandinavian', 'brutalist', 'industrial', 'vintage', 'modern'):
        if _style_supported(style, prompt_text):
            return style
    return None


def _detect_material(prompt_text):
    lower = _ascii_fold(prompt_text).lower()
    hits = []
    for mat, keywords in MATERIAL_KEYWORDS.items():
        if any(re.search(rf'(?<![a-z]){re.escape(kw)}(?![a-z])', lower) for kw in keywords):
            hits.append(mat)
    if len(hits) > 1:
        return 'mixed'
    return hits[0] if len(hits) == 1 else None


def _material_supported(material, prompt_text):
    if material not in MATERIALS:
        return False
    detected = _detect_material(prompt_text)
    if detected is None:
        return False
    if material == 'mixed':
        return True
    return material == detected or detected == 'mixed'


def _detect_type(prompt_text, primary_space=None):
    lower = _ascii_fold(prompt_text).lower()
    for otype, phrases in TYPE_HINTS:
        if any(re.search(rf'(?<![a-z]){re.escape(p)}s?(?![a-z])', lower) for p in phrases):
            return otype
    if primary_space is not None and primary_space.type == 'wall':
        return 'wall-art'
    return 'furniture'


def _estimate_footprint(prompt_text, name, otype, wall_mode):
    haystack = f'{_ascii_fold(name).lower()} {_ascii_fold(prompt_text).lower()}'
    if not wall_mode:
        for keywords, (w, d) in ITEM_FOOTPRINTS:
            if any(re.search(rf'(?<![a-z]){re.escape(kw)}s?(?![a-z])', haystack) for kw in keywords):
                return {'x': w, 'y': d, 'z': None}
    w, second = DEFAULT_FOOTPRINTS.get(otype, (36, 24))
    if wall_mode:
        return {'x': w, 'y': None, 'z': second}
    return {'x': w, 'y': second, 'z': None}


def _fallback_name(prompt_text):
    text = prompt_text.strip().rstrip('.!;:,')
    for _ in range(3):
        nxt = FILLER_PREFIX_RE.sub('', text).strip()
        if nxt == text:
            break
        text = nxt
    # Trim trailing price/size clauses like "under $1000" when building a clean title.
    text = re.split(r'\b(?:under|over|below|around|about|priced|budget|between)\s+\$?\d', text, maxsplit=1, flags=re.I)[0].strip(' ,;-')
    text = re.sub(r'\bstyle\b', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        text = prompt_text.strip()[:prompts.NAME_MAX_LEN]
    capped = text[:prompts.NAME_MAX_LEN].strip()
    return capped[0].upper() + capped[1:] if capped else 'Object'


def _parse_deterministic_prices(prompt_text):
    lower = prompt_text.lower()
    between = re.search(
        r'between\s+\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:and|-|to)\s*\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)',
        lower,
    )
    if between:
        a = float(between.group(1).replace(',', ''))
        b = float(between.group(2).replace(',', ''))
        return min(a, b), max(a, b), False, False
    around = re.search(r'(?:around|about|roughly|~)\s*\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)', lower)
    if around:
        return None, float(around.group(1).replace(',', '')), False, True
    under = re.search(r'(?:under|below|max|up\s+to|at\s+most|less\s+than)\s*\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)', lower)
    if under:
        return None, float(under.group(1).replace(',', '')), False, False
    over = re.search(r'(?:over|above|at\s+least|min|minimum)\s*\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)', lower)
    if over:
        return float(over.group(1).replace(',', '')), None, False, False
    return None, None, False, False


def _parse_deterministic_dims(prompt_text, wall_mode):
    lower = prompt_text.lower()
    unit_scale = {'in': 1.0, 'inch': 1.0, 'inches': 1.0, '"': 1.0, 'ft': 12.0, 'feet': 12.0, 'foot': 12.0, "'": 12.0, 'cm': 1 / 2.54}
    m = re.search(
        r'(\d+(?:\.\d+)?)\s*(?:x|by|×)\s*(\d+(?:\.\d+)?)(?:\s*(?:x|by|×)\s*(\d+(?:\.\d+)?))?\s*(inches|inch|in|feet|foot|ft|cm|"|\')',
        lower,
    )
    if m:
        scale = unit_scale.get(m.group(4), 1.0)
        a = round(float(m.group(1)) * scale, 2)
        b = round(float(m.group(2)) * scale, 2)
        c = round(float(m.group(3)) * scale, 2) if m.group(3) else None
        if wall_mode and c is None:
            return {'x': a, 'y': None, 'z': b}
        return {'x': a, 'y': b, 'z': c}
    w_match = re.search(r'(\d+(?:\.\d+)?)\s*(inches|inch|in|feet|foot|ft|cm)\s*wide', lower)
    if w_match:
        scale = unit_scale.get(w_match.group(2), 1.0)
        return {'x': round(float(w_match.group(1)) * scale, 2), 'y': None, 'z': None}
    return {'x': None, 'y': None, 'z': None}


def parse_deterministic(prompt_text, primary_space=None):
    """Deterministic extraction used when SEARCHER_AGENT=deterministic or CLI fails."""
    otype = _detect_type(prompt_text, primary_space)
    wall_mode = otype == 'wall-art' or (primary_space is not None and primary_space.type == 'wall')
    name = _fallback_name(prompt_text)
    price_min, price_limit, min_fuzzy, limit_fuzzy = _parse_deterministic_prices(prompt_text)
    rating_match = re.search(r'(\d(?:\.\d)?)\s*\+?\s*stars?', prompt_text.lower())
    min_rating = float(rating_match.group(1)) if rating_match and 0 < float(rating_match.group(1)) <= 5 else None
    return {
        'name': name,
        'type': otype,
        'explicitDims': _parse_deterministic_dims(prompt_text, wall_mode),
        'estimatedFootprint': _estimate_footprint(prompt_text, name, otype, wall_mode),
        'weight': None,
        'material': _detect_material(prompt_text),
        'style': _detect_style(prompt_text),
        'priceMin': price_min,
        'priceLimit': price_limit,
        'priceMinFuzzy': min_fuzzy,
        'priceLimitFuzzy': limit_fuzzy,
        'minRating': min_rating,
        'similarItems': [],
        'recommendedPinIds': None,  # None signals caller to use deterministic pin ranking
    }


def _pos_num(v, maximum=100000.0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    val = round(float(v), 2)
    return val if 0 < val <= maximum else None


def _clamp_to_space(val, bound):
    if val is None:
        return None
    if bound is not None and bound > 0:
        return round(min(val, float(bound)), 2)
    return val


def _resolve_dims(raw_explicit, raw_est, drawn_dims, *, otype, primary_space, name, prompt_text, has_numbers):
    wall_mode = otype == 'wall-art' or (primary_space is not None and primary_space.type == 'wall')
    exp = raw_explicit if (isinstance(raw_explicit, dict) and has_numbers) else {}
    est = raw_est if isinstance(raw_est, dict) else {}
    drawn = drawn_dims if isinstance(drawn_dims, dict) else {}
    det_est = _estimate_footprint(prompt_text, name, otype, wall_mode)

    space_x = float(primary_space.dims['x']) if primary_space and primary_space.dims.get('x') else None
    space_y = float(primary_space.dims['y']) if primary_space and primary_space.dims.get('y') else None

    exp_x = _pos_num(exp.get('x'), 1200)
    exp_y = _pos_num(exp.get('y'), 1200)
    exp_z = _pos_num(exp.get('z'), 1200)

    drawn_x = _pos_num(drawn.get('x'), 1200)
    drawn_y = _pos_num(drawn.get('y'), 1200)
    drawn_z = _pos_num(drawn.get('z'), 1200)

    est_x = _clamp_to_space(_pos_num(est.get('x'), 600) or det_est['x'], space_x)
    x = exp_x or drawn_x or est_x or 24.0

    if wall_mode:
        est_z = _clamp_to_space(_pos_num(est.get('z') or est.get('y'), 600) or det_est['z'], space_y)
        z = exp_z or (exp_y if exp_z is None and exp_x is not None else None) or drawn_z or drawn_y or est_z or 24.0
        dims = {'x': x, 'z': z}
        if exp_z is not None and exp_y is not None:
            dims['y'] = exp_y
        return dims

    est_y = _clamp_to_space(_pos_num(est.get('y'), 600) or det_est['y'], space_y)
    y = exp_y or drawn_y or est_y or 24.0
    dims = {'x': x, 'y': y}
    if exp_z is not None:
        dims['z'] = exp_z
    return dims


def _clean_similar(raw_list, prompt_text):
    if not isinstance(raw_list, list):
        return []
    lower_prompt = _ascii_fold(prompt_text).lower()
    out = []
    for entry in raw_list:
        if not isinstance(entry, str):
            continue
        item = ''.join(ch if ch.isprintable() else ' ' for ch in entry)
        item = re.sub(r'\s+', ' ', item).strip()[:prompts.SIMILAR_ITEM_LEN]
        if not item:
            continue
        # Anti-hallucination: keep only if at least one distinctive word appears in the prompt.
        tokens = [w for w in re.findall(r'[a-z0-9]{3,}', _ascii_fold(item).lower())
                  if w not in matching.STOPWORDS]
        if not tokens or not any(t in lower_prompt for t in tokens):
            continue
        if all(existing.lower() != item.lower() for existing in out):
            out.append(item)
        if len(out) >= prompts.SIMILAR_MAX_ITEMS:
            break
    return out


def _parses_dir():
    path = runner.var_dir() / 'parses'
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_argv(user_message):
    return [
        settings.SEARCHER_CLAUDE_BIN, '-p', user_message,
        '--output-format', 'json',
        '--model', settings.OBJECT_PARSER_MODEL,
        '--json-schema', prompts.schema_json(),
        '--system-prompt', prompts.SYSTEM_PROMPT,
        '--tools', '',
        '--permission-mode', 'dontAsk',
        '--strict-mcp-config',
        '--setting-sources', 'project,local',
        '--safe-mode',
        '--max-turns', str(settings.OBJECT_PARSER_MAX_TURNS),
        '--no-session-persistence',
    ]


def _invoke_haiku(user_message, parse_id):
    work_dir = _parses_dir()
    out_path = work_dir / f'{parse_id}.out.json'
    err_path = work_dir / f'{parse_id}.err.log'
    try:
        argv = build_argv(user_message)
        env = claude_cli.base_env({'OBJECT_PARSE_ID': parse_id})
        run = claude_cli.run(
            argv,
            cwd=runner.agent_cwd(),
            env=env,
            out_path=out_path,
            err_path=err_path,
            timeout=settings.OBJECT_PARSER_TIMEOUT,
        )
        if run.spawn_error or run.timed_out or run.canceled or run.rc != 0:
            logger.warning(
                'object_parser claude failed (rc=%s, timed_out=%s, spawn_error=%s)',
                run.rc, run.timed_out, run.spawn_error,
            )
            return None
        if not isinstance(run.data, dict) or run.data.get('is_error'):
            return None
        structured = run.data.get('structured_output')
        return structured if isinstance(structured, dict) else None
    finally:
        for p in (out_path, err_path):
            try:
                p.unlink()
            except OSError:
                pass


def _load_board_rows(spaces):
    space_ids = [s.id for s in spaces]
    if not space_ids:
        return []
    rank_by_space = {sid: idx for idx, sid in enumerate(space_ids)}
    rows = list(
        SpaceInspiration.objects.filter(
            space_id__in=space_ids, vote=VoteChoice.UP, categorized_at__isnull=False
        )
    )
    rows.sort(key=lambda r: (rank_by_space.get(r.space_id, 999), r.board_rank, r.pk))
    seen = set()
    unique = []
    for row in rows:
        if row.pin_id in seen:
            continue
        seen.add(row.pin_id)
        unique.append(row)
    return unique


def _pin_prompt_entry(row):
    entry = {'pinId': row.pin_id, 'categories': list(row.categories or [])}
    if row.item:
        entry['item'] = row.item
    if row.styles:
        entry['styles'] = list(row.styles[:4])
    if row.colors:
        entry['colors'] = list(row.colors[:4])
    if row.materials:
        entry['materials'] = list(row.materials[:4])
    if row.patterns:
        entry['patterns'] = list(row.patterns[:3])
    if row.description:
        entry['description'] = row.description[:120]
    return entry


def _sanitize_structured(raw, *, prompt_text, primary_space, drawn_dims):
    has_numbers = _has_numeric_cue(prompt_text)

    raw_name = raw.get('name') if isinstance(raw.get('name'), str) else ''
    name = ''.join(ch if ch.isprintable() else ' ' for ch in raw_name)
    name = re.sub(r'\s+', ' ', name).strip()[:prompts.NAME_MAX_LEN]
    if not name or name.lower() == 'new object':
        name = _fallback_name(prompt_text)

    otype = raw.get('type') if raw.get('type') in OBJECT_TYPES else _detect_type(prompt_text, primary_space)

    dims = _resolve_dims(
        raw.get('explicitDims'),
        raw.get('estimatedFootprint'),
        drawn_dims,
        otype=otype,
        primary_space=primary_space,
        name=name,
        prompt_text=prompt_text,
        has_numbers=has_numbers,
    )

    weight = _pos_num(raw.get('weight'), 5000) if has_numbers else None

    raw_mat = raw.get('material')
    material = raw_mat if _material_supported(raw_mat, prompt_text) else _detect_material(prompt_text)

    raw_style = raw.get('style')
    style = raw_style if _style_supported(raw_style, prompt_text) else None

    price_min = _pos_num(raw.get('priceMin'), 1_000_000) if has_numbers else None
    price_limit = _pos_num(raw.get('priceLimit'), 1_000_000) if has_numbers else None
    if price_min is not None and price_limit is not None and price_min > price_limit:
        price_min, price_limit = price_limit, price_min
    price_min_fuzzy = bool(raw.get('priceMinFuzzy')) and price_min is not None
    price_limit_fuzzy = bool(raw.get('priceLimitFuzzy')) and price_limit is not None

    raw_rating = _pos_num(raw.get('minRating'), 5.0) if has_numbers else None
    min_rating = raw_rating if (raw_rating is not None and 0 < raw_rating <= 5.0) else None

    similar_items = _clean_similar(raw.get('similarItems'), prompt_text)

    parsed = {
        'name': name,
        'description': prompt_text,
        'type': otype,
        'dims': dims,
        'weight': weight,
        'material': material,
        'style': style,
        'priceMin': price_min,
        'priceLimit': price_limit,
        'priceMinFuzzy': price_min_fuzzy,
        'priceLimitFuzzy': price_limit_fuzzy,
        'minRating': min_rating,
        'similarItems': similar_items,
    }

    # Guarantee DB serializer compatibility before returning to the client.
    try:
        validate_object(parsed)
    except ValidationFailed:
        logger.warning('object_parser sanitized output failed validate_object; falling back to minimal')
        parsed = {
            'name': _fallback_name(prompt_text),
            'description': prompt_text,
            'type': otype,
            'dims': {'x': dims['x'], **({'z': dims['z']} if 'z' in dims and 'y' not in dims else {'y': dims.get('y', 24.0)})},
            'weight': None,
            'material': _detect_material(prompt_text),
            'style': _detect_style(prompt_text),
            'priceMin': None,
            'priceLimit': None,
            'priceMinFuzzy': False,
            'priceLimitFuzzy': False,
            'minRating': None,
            'similarItems': [],
        }
        validate_object(parsed)
    return parsed


def _rank_board_pins(board_rows, parsed, prompt_text, recommended_ids):
    wanted = parsed['type']
    cat_rows = [r for r in board_rows if wanted in (r.categories or [])]
    if not cat_rows:
        return [], []

    det_similar, det_rest = matching.suggest_pins(cat_rows, parsed, prompt_text)
    if recommended_ids is None:
        return det_similar, det_rest

    by_id = {r.pin_id: r for r in cat_rows}
    similar = []
    seen = set()
    for pid in recommended_ids:
        if isinstance(pid, str) and pid in by_id and pid not in seen:
            seen.add(pid)
            similar.append(by_id[pid])
            if len(similar) >= prompts.RECOMMENDED_PINS_MAX:
                break

    # Also surface exact item-noun matches (score == 2) that the model omitted.
    aug = dict(parsed, similarItems=list(parsed.get('similarItems') or []) + [prompt_text])
    for row in det_similar:
        if len(similar) >= prompts.RECOMMENDED_PINS_MAX:
            break
        if row.pin_id not in seen and matching.item_score(row.item, aug) == 2:
            seen.add(row.pin_id)
            similar.append(row)

    category_rest = [r for r in (det_similar + det_rest) if r.pin_id not in seen]
    return similar, category_rest


def parse_prompt(prompt_text, *, spaces=(), drawn_dims=None):
    """Parse `prompt_text` into `{parsed, similarPins, categoryPins, agentMode}`."""
    cleaned = clean_prompt(prompt_text)
    primary_space = spaces[0] if spaces else None
    board_rows = _load_board_rows(spaces)

    space_info = None
    if primary_space is not None:
        space_info = {
            'name': primary_space.name,
            'type': primary_space.type,
            'dimsInches': primary_space.dims,
            'weightLimitLb': primary_space.weight_limit,
        }
    pin_entries = [_pin_prompt_entry(r) for r in board_rows if r.categories]

    agent_mode = 'deterministic'
    raw = None
    if settings.SEARCHER_AGENT != AgentMode.DETERMINISTIC:
        parse_id = uuid.uuid4().hex[:12]
        msg = prompts.build_user_message(cleaned, space_info=space_info, board_pins=pin_entries)
        raw = _invoke_haiku(msg, parse_id)
        if raw is not None:
            agent_mode = 'claude'

    if raw is None:
        raw = parse_deterministic(cleaned, primary_space)
        recommended_ids = None
    else:
        rec = raw.get('recommendedPinIds')
        recommended_ids = rec if isinstance(rec, list) else []

    parsed = _sanitize_structured(
        raw,
        prompt_text=cleaned,
        primary_space=primary_space,
        drawn_dims=drawn_dims,
    )
    similar_rows, category_rows = _rank_board_pins(board_rows, parsed, cleaned, recommended_ids)

    return {
        'parsed': parsed,
        'similarPins': [inspiration_to_json(r) for r in similar_rows],
        'categoryPins': [inspiration_to_json(r) for r in category_rows],
        'agentMode': agent_mode,
    }
