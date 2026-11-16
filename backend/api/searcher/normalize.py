"""Turn a raw UCP product into Candidate field values.

All merchant-supplied text is untrusted (it is later shown to an LLM): tags
and control characters are stripped, whitespace collapsed, lengths capped.
"""
from .parse import (
    clean_text,
    parse_dimensions,
    parse_materials,
    parse_weight,
)

MAX_SPECS = 12
MAX_SPEC_LEN = 120
DESCRIPTION_LEN = 500


def _safe_url(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value.lower().startswith(('http://', 'https://')):
        return ''
    # No whitespace or control characters in URLs.
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        return ''
    return value[:2000]


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    return None


def _float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def normalize_product(product):
    """Returns a dict of Candidate field values (no run/query/verdict fields)."""
    variants = product.get('variants') or []
    variant = variants[0] if variants and isinstance(variants[0], dict) else {}
    seller = variant.get('seller') or {}
    price = variant.get('price') or ((product.get('price_range') or {}).get('min')) or {}
    rating = product.get('rating') or {}
    media = product.get('media') or variant.get('media') or []
    image = media[0].get('url') if media and isinstance(media[0], dict) else ''

    dims, longest, source = parse_dimensions(product)
    materials, material_text = parse_materials(product)

    specs = []
    raw_specs = (product.get('metadata') or {}).get('tech_specs') or []
    for entry in raw_specs:
        text = clean_text(entry, MAX_SPEC_LEN)
        if text:
            specs.append(text)
        if len(specs) >= MAX_SPECS:
            break

    description = (product.get('description') or {}).get('plain') or (
        (variant.get('description') or {}).get('plain')
    )

    return {
        'product_gid': clean_text(product.get('id'), 255),
        'variant_gid': clean_text(variant.get('id'), 255),
        'title': clean_text(product.get('title') or variant.get('title'), 300),
        'description': clean_text(description, DESCRIPTION_LEN),
        'seller_name': clean_text(seller.get('name'), 200),
        'seller_domain': clean_text(seller.get('domain'), 200),
        'seller_url': _safe_url(seller.get('url')),
        'price_cents': _int(price.get('amount')) or 0,
        'currency': clean_text(price.get('currency'), 8).upper(),
        'pdp_url': _safe_url(variant.get('url')),
        'checkout_url': _safe_url(variant.get('checkout_url')),
        'image_url': _safe_url(image),
        'rating_value': _float(rating.get('value')),
        'rating_count': _int(rating.get('count')),
        'dims': dims or None,
        'dims_longest': longest,
        'dims_source': source,
        'weight_lb': parse_weight(product),
        'materials': materials,
        'material_text': clean_text(material_text, 300),
        'specs': specs,
    }
