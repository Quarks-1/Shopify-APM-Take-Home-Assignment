"""Push a store's cart lines to its merchant UCP cart, and open checkouts.

The merchant cart is always rebuilt from the DB, so duplicate or out-of-order
syncs are harmless. One sync per store runs at a time (per process)."""
import hashlib
import json
import logging
import threading
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import MerchantCart, MerchantCartStatus as S
from ..searcher import ucp_client
from ..searcher.ucp_client import ID_RE, UcpError
from ..serializers import iso
from .sanitize import https_url, sanitize_cart
from .stores import buy_url, current_lines, line_to_json, permalink, ucp_business

logger = logging.getLogger(__name__)

# The merchant does not offer agent carts: fall back to buy links.
BLOCKING = {'AUTH_REQUIRED', 'INSUFFICIENT_PERMISSIONS', 'OPERATION_NOT_OFFERED',
            'PROFILE_FETCH_FAILED', 'invalid_domain'}
# A failed update is retried as a fresh cart, except after a timeout (the
# update may have landed, and a second call would double the wait).
NO_RECREATE = {'timeout'}

_LOAD = object()
_locks = {}
_locks_guard = threading.Lock()


def _lock(domain):
    with _locks_guard:
        return _locks.setdefault(domain, threading.RLock())


def _store_lines(domain):
    return [(line, cand) for line, cand, store in current_lines() if store == domain]


def _items(pairs):
    """[(variant gid, qty)] summed per variant, sorted (stable signature)."""
    qty = defaultdict(int)
    for line, cand in pairs:
        if cand.variant_gid:
            qty[cand.variant_gid] += line.qty
    return sorted(qty.items())


def _signature(items):
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def _fresh(ts, seconds):
    return ts is not None and timezone.now() - ts < timedelta(seconds=seconds)


def _iso(ts):
    return iso(ts) if ts is not None else None


def serialize_merchant_cart(domain, mc=_LOAD, pairs=None):
    """ApiMerchantCart for one store. `mc` is its row (None: no row)."""
    if mc is _LOAD:
        mc = MerchantCart.objects.filter(pk=domain).first()
    if pairs is None:
        pairs = _store_lines(domain)
    if not pairs:
        status = 'empty'
    else:
        status = mc.status if mc is not None else S.DIRTY.value
    unsupported = mc is not None and mc.status == S.UNSUPPORTED
    with_variant = [(line, cand) for line, cand in pairs if cand.variant_gid]
    link = permalink([(cand, line.qty) for line, cand in pairs]) if unsupported else None
    if unsupported:
        mode = 'permalink' if link else 'links'
    elif pairs and not with_variant:
        mode = 'links'
    else:
        mode = 'checkout'
    if mode == 'links':
        fallback_pairs = pairs
    elif mode == 'checkout':
        fallback_pairs = [(line, cand) for line, cand in pairs if not cand.variant_gid]
    else:
        fallback_pairs = []
    fallback = []
    for line, cand in fallback_pairs:
        url = buy_url(cand)
        if url:
            fallback.append({'lineId': line.id, 'url': url})

    snap = (mc.snapshot if mc is not None and status != 'empty' else None) or {}
    if status in (S.DIRTY, S.ERROR):
        stale = True
    elif status == S.SYNCED:
        stale = not _fresh(mc.synced_at, settings.CART_REFRESH_AFTER)
    elif status == S.UNSUPPORTED:
        stale = not _fresh(mc.unsupported_at, settings.CART_UNSUPPORTED_TTL)
    else:  # empty: a leftover row still needs a sync to cancel its cart
        stale = mc is not None
    return {
        'storeId': domain,
        'status': str(status),
        'mode': mode,
        'cartId': (mc.ucp_cart_id or None) if mc is not None and status != 'empty' else None,
        'currency': snap.get('currency'),
        'totals': snap.get('totals') or [],
        'messages': snap.get('messages') or [],
        'lineItems': snap.get('lineItems') or [],
        'permalink': link,
        'fallback': fallback,
        'error': ({'code': mc.error_code, 'message': mc.error_message}
                  if mc is not None and mc.error_code else None),
        'syncedAt': _iso(mc.synced_at) if mc is not None else None,
        'checkoutOpenedAt': _iso(mc.checkout_opened_at) if mc is not None else None,
        'stale': stale,
    }


def cart_payload():
    """{lines, stores}: stores with lines in line order, then leftover rows."""
    entries = current_lines()
    rows = {mc.domain: mc for mc in MerchantCart.objects.all()}
    by_store = defaultdict(list)
    for line, cand, store in entries:
        by_store[store].append((line, cand))
    order = list(by_store) + sorted(d for d in rows if d not in by_store)
    return {
        'lines': [line_to_json(*entry) for entry in entries],
        'stores': [serialize_merchant_cart(d, rows.get(d), by_store.get(d, [])) for d in order],
    }


def _save(domain, revision, status, **fields):
    """Record a sync outcome. Edits made since `revision` was read keep the
    store dirty; a row deleted mid-sync is recreated so its cart can be cancelled."""
    with transaction.atomic():
        row = MerchantCart.objects.filter(pk=domain).first()
        if row is None:
            row = MerchantCart(domain=domain, revision=revision)
        elif row.revision != revision and status == S.SYNCED:
            status = S.DIRTY
        if status in (S.SYNCED, S.DIRTY):
            fields.setdefault('synced_revision', revision)
        for k, v in fields.items():
            setattr(row, k, v)
        row.status = status
        row.save()
    return row


def _mark_unsupported(domain, revision, code, message):
    return _save(
        domain, revision, S.UNSUPPORTED, ucp_cart_id='', snapshot=None, synced_signature='',
        error_code=code[:64], error_message=message, unsupported_at=timezone.now(), synced_at=None,
    )


def _sync(domain, force):
    """Bring the merchant cart in line with the DB. Returns (row or None, pairs)."""
    pairs = _store_lines(domain)
    mc = MerchantCart.objects.filter(pk=domain).first()
    revision = mc.revision if mc is not None else 0
    items = _items(pairs)

    if not items:
        if mc is not None and mc.ucp_cart_id and ucp_business(domain):
            try:
                ucp_client.cart_cancel(domain, mc.ucp_cart_id)
            except UcpError as exc:
                logger.info('cart cancel for %s failed: %s', domain, exc)
        if not pairs:
            with transaction.atomic():
                deleted, _ = MerchantCart.objects.filter(pk=domain, revision=revision).delete()
            if deleted or mc is None:
                return None, pairs
            return _sync(domain, force)  # lines arrived mid-sync
        if mc is not None and mc.status == S.UNSUPPORTED:
            return mc, pairs
        return _save(domain, revision, S.SYNCED, ucp_cart_id='', snapshot=None,
                     synced_signature=_signature([]), synced_at=timezone.now(),
                     error_code='', error_message=''), pairs

    if ucp_business(domain) is None:
        return _mark_unsupported(domain, revision, 'invalid_domain',
                                 'store id is not a merchant hostname'), pairs
    if (mc is not None and mc.status == S.UNSUPPORTED and not force
            and _fresh(mc.unsupported_at, settings.CART_UNSUPPORTED_TTL)):
        return mc, pairs

    signature = _signature(items)
    if (not force and mc is not None and mc.ucp_cart_id and mc.synced_signature == signature
            and mc.status in (S.SYNCED, S.DIRTY)
            and _fresh(mc.synced_at, settings.CART_REFRESH_AFTER)):
        return _save(domain, revision, S.SYNCED), pairs

    payload = {
        'line_items': [{'item': {'id': v}, 'quantity': q} for v, q in items],
        'context': {'address_country': settings.CART_COUNTRY},
    }
    try:
        env = None
        if mc is not None and mc.ucp_cart_id:
            try:
                env = ucp_client.cart_update(domain, mc.ucp_cart_id, payload)
            except UcpError as exc:
                if exc.code in BLOCKING or exc.code in NO_RECREATE:
                    raise
                logger.info('recreating cart for %s after %s', domain, exc.code)
        if env is None:
            env = ucp_client.cart_create(domain, payload)
    except UcpError as exc:
        if exc.code in BLOCKING:
            return _mark_unsupported(domain, revision, exc.code, exc.message), pairs
        return _save(domain, revision, S.ERROR, error_code=exc.code[:64],
                     error_message=exc.message), pairs

    result = env['result']
    cart_id = result['id']
    if not ID_RE.match(cart_id) or cart_id.startswith('-'):
        return _save(domain, revision, S.ERROR, error_code='invalid_cart_id',
                     error_message='merchant returned an unusable cart id'), pairs
    return _save(
        domain, revision, S.SYNCED, ucp_cart_id=cart_id, snapshot=sanitize_cart(result),
        synced_signature=signature, synced_at=timezone.now(), unsupported_at=None,
        error_code='', error_message='',
    ), pairs


def sync_store(domain, force=False):
    """Sync one store; returns its ApiMerchantCart (the outcome is in `status`)."""
    with _lock(domain):
        mc, pairs = _sync(domain, force)
        return serialize_merchant_cart(domain, mc, pairs)


class CheckoutUnavailable(Exception):
    def __init__(self, reason, store):
        super().__init__(reason)
        self.reason = reason
        self.store = store


def _opened(domain, url, kind, checkout_id):
    MerchantCart.objects.filter(pk=domain).update(checkout_opened_at=timezone.now())
    return {'url': url, 'kind': kind, 'checkoutId': checkout_id,
            'store': serialize_merchant_cart(domain)}


def _create_checkout(domain, mc):
    return ucp_client.checkout_create(domain, {'cart_id': mc.ucp_cart_id, 'line_items': []})


def checkout_store(domain):
    """A fresh checkout for the store: {url, kind, checkoutId, store}, else
    CheckoutUnavailable. Never completes or cancels a checkout."""
    with _lock(domain):
        mc, pairs = _sync(domain, False)
        store = serialize_merchant_cart(domain, mc, pairs)
        if store['status'] == 'empty':
            raise CheckoutUnavailable('empty', store)
        if store['mode'] == 'checkout' and store['status'] != S.SYNCED:
            mc, pairs = _sync(domain, True)
            store = serialize_merchant_cart(domain, mc, pairs)
        if store['mode'] == 'permalink':
            return _opened(domain, store['permalink'], 'permalink', None)
        if store['mode'] == 'links':
            raise CheckoutUnavailable(store['error']['code'] if store['error'] else 'links', store)
        if store['status'] != S.SYNCED:
            raise CheckoutUnavailable(store['error']['code'] if store['error'] else store['status'],
                                      store)
        try:
            env = _create_checkout(domain, mc)
        except UcpError as exc:
            if exc.code in BLOCKING:
                mc = _mark_unsupported(domain, mc.revision, exc.code, exc.message)
            else:
                logger.info('checkout for %s failed (%s); re-syncing once', domain, exc.code)
                mc, pairs = _sync(domain, True)
            store = serialize_merchant_cart(domain, mc, pairs)
            if store['mode'] == 'permalink':
                return _opened(domain, store['permalink'], 'permalink', None)
            if store['mode'] != 'checkout' or store['status'] != S.SYNCED:
                raise CheckoutUnavailable(store['error']['code'] if store['error'] else exc.code,
                                          store)
            try:
                env = _create_checkout(domain, mc)
            except UcpError as exc2:
                raise CheckoutUnavailable(exc2.code, store)
        result = env['result']
        url = https_url(result.get('continue_url'))
        if url is None:
            raise CheckoutUnavailable('invalid_continue_url', store)
        checkout_id = result['id'] if ID_RE.match(result['id']) else None
        return _opened(domain, url, 'ucp', checkout_id)
