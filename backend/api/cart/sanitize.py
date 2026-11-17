"""Whitelist what the app keeps of a merchant's UCP cart response."""
import re
from urllib.parse import urlsplit

MAX_URL = 2000
MAX_ITEMS = 100
MAX_MESSAGES = 20
MESSAGE_TYPES = {'info', 'warning', 'error'}
PRESENTATIONS = {'notice', 'disclosure'}
LINE_PATH_RE = re.compile(r'^\$\.line_items\[(\d{1,4})\]')


def clip(value, n):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:n]


def amount(value):
    """An integer amount in minor units, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def https_url(value):
    """The URL if it is a well-formed https URL, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value.lower().startswith('https://') or len(value) > MAX_URL:
        return None
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    try:
        if not urlsplit(value).hostname:
            return None
    except ValueError:
        return None
    return value


def _totals(raw):
    out = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        value = amount(t.get('amount'))
        kind = clip(t.get('type'), 40)
        if value is None or not kind:
            continue
        out.append({'type': kind, 'displayText': clip(t.get('display_text'), 80) or kind,
                    'amount': value})
    return out[:10]


def _line_variant(line):
    item = line.get('item') if isinstance(line, dict) else None
    variant = item.get('id') if isinstance(item, dict) else None
    return clip(variant, 255) or None


def _line_items(raw):
    out = []
    for line in raw[:MAX_ITEMS]:
        variant = _line_variant(line)
        qty = amount(line.get('quantity')) if isinstance(line, dict) else None
        if variant is None or qty is None or qty < 0:
            continue
        entry = {'variantId': variant, 'quantity': qty}
        title = clip(line['item'].get('title'), 300)
        if title:
            entry['title'] = title
        out.append(entry)
    return out


def _messages(raw, lines):
    out = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict) or m.get('type') not in MESSAGE_TYPES:
            continue
        content = clip(m.get('content'), 1000)
        if not content:
            continue
        msg = {'type': m['type'], 'content': content}
        code = clip(m.get('code'), 64)
        if code:
            msg['code'] = code
        if m.get('presentation') in PRESENTATIONS:
            msg['presentation'] = m['presentation']
        # `$.line_items[i]` indexes the merchant's line order, not ours.
        match = LINE_PATH_RE.match(m.get('path') or '') if isinstance(m.get('path'), str) else None
        if match and int(match.group(1)) < len(lines):
            variant = _line_variant(lines[int(match.group(1))])
            if variant:
                msg['variantId'] = variant
        for src, dst in (('url', 'url'), ('image_url', 'imageUrl')):
            url = https_url(m.get(src))
            if url:
                msg[dst] = url
        out.append(msg)
        if len(out) >= MAX_MESSAGES:
            break
    return out


def sanitize_cart(result):
    """The snapshot stored for a UCP cart `result`."""
    lines = result.get('line_items') if isinstance(result.get('line_items'), list) else []
    currency = clip(result.get('currency'), 8).upper()
    return {
        'currency': currency if re.fullmatch(r'[A-Z]{3}', currency) else None,
        'totals': _totals(result.get('totals')),
        'lineItems': _line_items(lines),
        'messages': _messages(result.get('messages'), lines),
    }
