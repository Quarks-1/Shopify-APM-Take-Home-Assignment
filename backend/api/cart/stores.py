"""Which store a cart line belongs to, and per-store bookkeeping."""
import re
from urllib.parse import urlsplit

from django.db.models import F

from ..models import CartLine, MerchantCart, MerchantCartStatus
from ..searcher.ucp_client import HOST_RE, UcpError, business_url
from .sanitize import https_url

UNKNOWN_STORE = 'unknown-seller'
VARIANT_NUM_RE = re.compile(r'^gid://shopify/ProductVariant/(\d{1,20})\Z')


def host_of(url):
    if not url:
        return None
    try:
        return urlsplit(url).hostname or None
    except ValueError:
        return None


def store_key(candidate):
    """Mirrors the frontend's candidateStore() in frontend/src/state/remote.ts."""
    return (candidate.seller_domain or None) or host_of(candidate.seller_url) \
        or host_of(candidate.pdp_url) or UNKNOWN_STORE


def ucp_business(domain):
    """`https://<domain>` when the store id is a bare hostname, else None."""
    try:
        return business_url(domain)
    except UcpError:
        return None


def buy_url(candidate):
    """The per-item fallback link: checkout_url, else the product page."""
    return https_url(candidate.checkout_url) or https_url(candidate.pdp_url)


def variant_number(gid):
    match = VARIANT_NUM_RE.match(gid or '')
    return match.group(1) if match else None


def permalink(lines):
    """One Shopify cart permalink for (candidate, qty) pairs, or None unless
    every line has a numeric variant id and an https host."""
    if not lines:
        return None
    host = None
    parts = []
    for cand, qty in lines:
        num = variant_number(cand.variant_gid)
        line_host = host_of(buy_url(cand))
        if num is None or line_host is None or not HOST_RE.match(line_host):
            return None
        host = host or line_host.lower()
        parts.append(f'{num}:{qty}')
    return f'https://{host}/cart/{",".join(parts)}'


def current_lines():
    """[(line, candidate, store)] for every line whose object has a pick."""
    out = []
    for line in CartLine.objects.select_related('object__selected_candidate'):
        cand = line.object.selected_candidate
        if cand is not None:
            out.append((line, cand, store_key(cand)))
    return out


def line_store(line):
    cand = line.object.selected_candidate
    return store_key(cand) if cand is not None else None


def line_to_json(line, cand, store):
    return {
        'id': line.id,
        'objectId': line.object_id,
        'candidateId': cand.pk,
        'productId': f'cand_{cand.pk}',
        'storeId': store,
        'qty': line.qty,
        'variantId': cand.variant_gid or None,
    }


def touch_stores(domains):
    """Mark stores as changed (call inside the edit's transaction). Returns
    the sorted store ids. Unsupported stores keep their status until the TTL."""
    domains = sorted({d for d in domains if d})
    if not domains:
        return []
    rows = MerchantCart.objects.filter(pk__in=domains)
    rows.exclude(status=MerchantCartStatus.UNSUPPORTED).update(
        revision=F('revision') + 1, status=MerchantCartStatus.DIRTY)
    rows.filter(status=MerchantCartStatus.UNSUPPORTED).update(revision=F('revision') + 1)
    existing = set(rows.values_list('pk', flat=True))
    MerchantCart.objects.bulk_create(
        [MerchantCart(domain=d, revision=1) for d in domains if d not in existing],
        ignore_conflicts=True,
    )
    return domains
