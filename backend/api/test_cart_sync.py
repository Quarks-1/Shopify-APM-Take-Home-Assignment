"""Merchant cart sync and checkout against the fake `ucp`."""
import os
import threading
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .cart import sync
from .cart.sanitize import sanitize_cart
from .cart.stores import touch_stores
from .models import CartLine, MerchantCart
from .searcher.ucp_client import UcpError
from .test_cart_api import STORE_KEYS, Api, FakeUcpMixin, pick

V1 = 'gid://shopify/ProductVariant/1001'
V2 = 'gid://shopify/ProductVariant/2002'
SHOP = 'shop.example.com'


class SyncBase(Api):
    def sync(self, domain=SHOP, **body):
        r = self.call('post', f'/api/cart/stores/{domain}/sync/', body or None)
        self.assertEqual(r.status_code, 200, r.content)
        store = r.json()
        self.assertEqual(set(store), STORE_KEYS)
        return store

    def checkout(self, domain=SHOP):
        return self.call('post', f'/api/cart/stores/{domain}/checkout/')

    def fill(self, domain=SHOP, *variants_qty):
        """One object + line per (variant, qty)."""
        lines = []
        for i, (variant, qty) in enumerate(variants_qty or [(V1, 1)]):
            oid = f'{domain.split(".")[0]}_{i}'
            pick(oid, domain, variant)
            lines.append({'id': f'line_{oid}', 'objectId': oid, 'qty': qty})
        return self.add(*lines)


class SyncTests(SyncBase):
    def test_create_then_full_replace_update(self):
        self.fill(SHOP, (V1, 1), (V2, 2))
        pick('dup', SHOP, V1)
        self.add({'id': 'line_dup', 'objectId': 'dup', 'qty': 3})
        store = self.sync()
        self.assertEqual((store['status'], store['mode'], store['stale']), ('synced', 'checkout', False))
        self.assertRegex(store['cartId'], r'^gid://shopify/Cart/')
        self.assertEqual(store['currency'], 'USD')
        # Merchant order, never re-sorted; amounts in minor units.
        self.assertEqual(store['totals'], [
            {'type': 'subtotal', 'displayText': 'Subtotal', 'amount': 1250 * 6},
            {'type': 'total', 'displayText': 'Total', 'amount': 1250 * 6},
        ])
        self.assertEqual(sorted((i['variantId'], i['quantity']) for i in store['lineItems']),
                         [(V1, 4), (V2, 2)])
        self.assertIsNotNone(store['syncedAt'])
        (create,) = self.calls('cart', 'create')
        self.assertEqual(create['payload'], {
            'line_items': [{'item': {'id': V1}, 'quantity': 4}, {'item': {'id': V2}, 'quantity': 2}],
            'context': {'address_country': 'US'},
        })

        self.call('patch', '/api/cart/lines/line_shop_1/', {'qty': 5})
        updated = self.sync()
        (update,) = self.calls('cart', 'update')
        self.assertEqual(update['argv'][2], store['cartId'])
        self.assertEqual(update['payload']['line_items'],
                         [{'item': {'id': V1}, 'quantity': 4}, {'item': {'id': V2}, 'quantity': 5}])
        self.assertEqual(updated['cartId'], store['cartId'])
        self.assertEqual(updated['totals'][-1]['amount'], 1250 * 9)

    def test_unchanged_cart_is_skipped_unless_forced_or_old(self):
        self.fill()
        self.sync()
        self.assertEqual(self.sync()['status'], 'synced')
        self.call('patch', '/api/cart/lines/line_shop_0/', {'qty': 2})
        self.call('patch', '/api/cart/lines/line_shop_0/', {'qty': 1})
        self.assertEqual(self.sync()['status'], 'synced')  # same lines as last push
        self.assertEqual(len(self.calls()), 1)
        self.sync(force=True)
        self.assertEqual(len(self.calls('cart', 'update')), 1)
        MerchantCart.objects.update(synced_at=timezone.now() - timedelta(hours=1))
        self.assertTrue(self.call('get', '/api/cart/').json()['stores'][0]['stale'])
        self.sync()
        self.assertEqual(len(self.calls('cart', 'update')), 2)

    def test_sync_body_validation(self):
        r = self.call('post', f'/api/cart/stores/{SHOP}/sync/', {'force': 'yes'})
        self.assertEqual(r.status_code, 400)
        r = self.call('post', f'/api/cart/stores/{SHOP}/sync/', {'nope': 1})
        self.assertEqual(r.status_code, 400)

    def test_empty_store_cancels_the_cart_and_drops_the_row(self):
        self.fill()
        cart_id = self.sync()['cartId']
        self.call('delete', '/api/cart/')
        store = self.sync()
        self.assertEqual((store['status'], store['cartId'], store['stale']), ('empty', None, False))
        (cancel,) = self.calls('cart', 'cancel')
        self.assertEqual(cancel['argv'][2], cart_id)
        self.assertFalse(MerchantCart.objects.exists())
        self.assertEqual(self.fake_state()['carts'], {})
        # Unknown store, nothing to do.
        self.assertEqual(self.sync('never.example.com')['status'], 'empty')
        self.assertEqual(len(self.calls()), 2)

    def test_recreates_a_cart_the_merchant_lost(self):
        self.fill()
        old = self.sync()['cartId']
        self.write_fake_state({'carts': {}})  # e.g. destroyed by a checkout cancel
        self.call('patch', '/api/cart/lines/line_shop_0/', {'qty': 2})
        store = self.sync()
        self.assertEqual(store['status'], 'synced')
        self.assertNotEqual(store['cartId'], old)
        self.assertEqual([c['argv'][1] for c in self.calls('cart')], ['create', 'update', 'create'])

    def test_unsupported_store_uses_a_permalink_and_respects_the_ttl(self):
        self.fill('noucp.example.com', (V1, 1), (V2, 3))
        store = self.sync('noucp.example.com')
        self.assertEqual((store['status'], store['mode']), ('unsupported', 'permalink'))
        self.assertEqual(store['permalink'], 'https://noucp.example.com/cart/1001:1,2002:3')
        self.assertEqual(store['error']['code'], 'OPERATION_NOT_OFFERED')
        self.assertEqual((store['fallback'], store['cartId'], store['stale']), ([], None, False))

        # Edits keep it unsupported (and the permalink current) without calls.
        self.call('patch', '/api/cart/lines/line_noucp_0/', {'qty': 2})
        store = self.sync('noucp.example.com')
        self.assertEqual(store['permalink'], 'https://noucp.example.com/cart/1001:2,2002:3')
        self.assertEqual(len(self.calls()), 1)
        self.sync('noucp.example.com', force=True)
        self.assertEqual(len(self.calls()), 2)
        MerchantCart.objects.update(unsupported_at=timezone.now() - timedelta(days=2))
        self.assertTrue(self.call('get', '/api/cart/').json()['stores'][0]['stale'])
        self.sync('noucp.example.com')
        self.assertEqual(len(self.calls()), 3)

    def test_links_mode_when_no_permalink_is_possible(self):
        pick('a', 'noucp.example.com', 'gid://shopify/ProductVariant/abc')
        self.add({'id': 'la', 'objectId': 'a'})
        store = self.sync('noucp.example.com')
        self.assertEqual((store['status'], store['mode'], store['permalink']),
                         ('unsupported', 'links', None))
        self.assertEqual(store['fallback'],
                         [{'lineId': 'la', 'url': 'https://noucp.example.com/buy/abc:1'}])

    def test_store_without_a_hostname_is_unsupported_without_calls(self):
        pick('a', '', V1, seller_url='', pdp_url='', checkout_url='')
        self.add({'id': 'la', 'objectId': 'a'})
        store = self.sync('unknown-seller')
        self.assertEqual((store['status'], store['mode'], store['error']['code']),
                         ('unsupported', 'links', 'invalid_domain'))
        self.assertEqual(store['fallback'], [])
        self.assertEqual(self.calls(), [])

    def test_lines_without_variant_ids_become_links(self):
        self.fill()
        pick('nov', SHOP, '')
        self.add({'id': 'lnov', 'objectId': 'nov'})
        store = self.sync()
        self.assertEqual((store['status'], store['mode']), ('synced', 'checkout'))
        self.assertEqual([i['variantId'] for i in store['lineItems']], [V1])
        self.assertEqual([f['lineId'] for f in store['fallback']], ['lnov'])

    def test_failure_keeps_the_last_snapshot(self):
        self.fill()
        good = self.sync()
        self.call('patch', '/api/cart/lines/line_shop_0/', {'qty': 2})
        boom = UcpError('exit_2', 'merchant exploded')
        with mock.patch.object(sync.ucp_client, 'cart_update', side_effect=boom), \
                mock.patch.object(sync.ucp_client, 'cart_create', side_effect=boom):
            store = self.sync()
        self.assertEqual((store['status'], store['stale']), ('error', True))
        self.assertEqual(store['error'], {'code': 'exit_2', 'message': 'merchant exploded'})
        self.assertEqual((store['cartId'], store['totals'], store['lineItems']),
                         (good['cartId'], good['totals'], good['lineItems']))
        self.assertEqual(self.sync()['status'], 'synced')

    @override_settings(CART_UCP_TIMEOUT=0.3)
    def test_timeout_is_an_error(self):
        self.fill('slow.example.com')
        with mock.patch.dict(os.environ, {'FAKE_UCP_SLOW': '3'}):
            store = self.sync('slow.example.com')
        self.assertEqual((store['status'], store['error']['code']), ('error', 'timeout'))

    def test_edit_during_a_sync_leaves_the_store_dirty(self):
        self.fill()
        real = sync.ucp_client.cart_create

        def create_and_edit(domain, payload):
            touch_stores([domain])
            return real(domain, payload)

        with mock.patch.object(sync.ucp_client, 'cart_create', create_and_edit):
            store = self.sync()
        self.assertEqual((store['status'], store['stale']), ('dirty', True))
        self.assertIsNotNone(store['cartId'])

    def test_disclosure_maps_to_its_variant_in_merchant_order(self):
        disclosed = 'gid://shopify/ProductVariant/4242DISCLOSURE'
        self.fill(SHOP, (disclosed, 1), (V1, 1), (V2, 1))
        store = self.sync()
        (msg,) = store['messages']
        self.assertEqual(msg, {
            'type': 'warning', 'code': 'disclosure', 'presentation': 'disclosure',
            'content': 'WARNING: This product can expose you to chemicals.',
            'variantId': disclosed, 'url': 'https://shop.example.com/pages/prop65',
        })


class SanitizeTests(TestCase):
    def test_whitelist_and_https_only(self):
        result = {
            'id': 'gid://shopify/Cart/x', 'currency': 'usd', 'secret': 'drop me',
            'totals': [{'type': 'subtotal', 'amount': 100, 'display_text': 'Subtotal'},
                       {'type': 'tax', 'amount': '12', 'display_text': 'Tax'},
                       {'type': 'fee', 'amount': True},
                       {'type': 'total', 'amount': 100.0, 'display_text': '  Total\n'}],
            'line_items': [
                {'item': {'id': 'gid://v/2', 'title': 'B'}, 'quantity': 1},
                {'item': {'id': 'gid://v/1', 'title': 'A' * 500}, 'quantity': 2},
                {'item': {}, 'quantity': 1},
            ],
            'messages': [
                {'type': 'info', 'content': 'hi', 'url': 'http://evil.example.com',
                 'image_url': 'https://cdn.example.com/a.png', 'path': '$.line_items[1].quantity'},
                {'type': 'warning', 'content': 'x', 'presentation': 'popup',
                 'url': 'javascript:alert(1)', 'path': '$.line_items[9]'},
                {'type': 'debug', 'content': 'dropped'},
                {'type': 'error', 'content': ''},
            ],
        }
        snap = sanitize_cart(result)
        self.assertEqual(set(snap), {'currency', 'totals', 'lineItems', 'messages'})
        self.assertEqual(snap['currency'], 'USD')
        self.assertEqual(snap['totals'], [
            {'type': 'subtotal', 'displayText': 'Subtotal', 'amount': 100},
            {'type': 'total', 'displayText': 'Total', 'amount': 100},
        ])
        self.assertEqual([i['variantId'] for i in snap['lineItems']], ['gid://v/2', 'gid://v/1'])
        self.assertEqual(len(snap['lineItems'][1]['title']), 300)
        self.assertEqual(snap['messages'], [
            {'type': 'info', 'content': 'hi', 'variantId': 'gid://v/1',
             'imageUrl': 'https://cdn.example.com/a.png'},
            {'type': 'warning', 'content': 'x'},
        ])


class CheckoutTests(SyncBase):
    def test_checkout_opens_the_merchant_continue_url(self):
        self.fill()
        r = self.checkout()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(set(body), {'url', 'kind', 'checkoutId', 'store'})
        self.assertEqual(body['kind'], 'ucp')
        self.assertRegex(body['url'], r'^https://shop\.example\.com/cart/c/')
        self.assertRegex(body['checkoutId'], r'^gid://shopify/Checkout/')
        self.assertIsNotNone(body['store']['checkoutOpenedAt'])
        (create,) = self.calls('checkout', 'create')
        self.assertEqual(create['payload'], {'cart_id': body['store']['cartId'], 'line_items': []})
        # Every click makes a fresh checkout from the same (unchanged) cart.
        self.assertEqual(self.checkout().status_code, 200)
        self.assertEqual(len(self.calls('checkout', 'create')), 2)
        self.assertEqual(len(self.calls('cart', 'create')), 1)

    def test_checkout_retries_once_after_a_lost_cart(self):
        self.fill()
        self.sync()
        self.write_fake_state({'carts': {}})
        r = self.checkout()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([c['argv'][:2] for c in self.calls()][1:], [
            ['checkout', 'create'], ['cart', 'update'], ['cart', 'create'], ['checkout', 'create'],
        ])

    def test_http_continue_url_is_refused(self):
        self.fill('badurl.example.com')
        r = self.checkout('badurl.example.com')
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertEqual(body['error'], 'checkout_unavailable')
        self.assertEqual(body['details'], {'reason': 'invalid_continue_url'})
        self.assertEqual(set(body['store']), STORE_KEYS)
        self.assertIsNone(body['store']['checkoutOpenedAt'])

    def test_permalink_store_checks_out_without_ucp(self):
        self.fill('noucp.example.com', (V1, 2))
        r = self.checkout('noucp.example.com')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['kind'], r.json()['url'], r.json()['checkoutId']),
                         ('permalink', 'https://noucp.example.com/cart/1001:2', None))
        self.assertEqual(self.calls('checkout'), [])

    def test_unavailable_reasons(self):
        r = self.checkout('nothing.example.com')
        self.assertEqual((r.status_code, r.json()['details']['reason']), (409, 'empty'))
        pick('a', 'noucp.example.com', 'gid://shopify/ProductVariant/abc')
        self.add({'objectId': 'a'})
        r = self.checkout('noucp.example.com')
        self.assertEqual((r.status_code, r.json()['details']['reason']),
                         (409, 'OPERATION_NOT_OFFERED'))
        self.fill('crash.example.com')
        r = self.checkout('crash.example.com')
        self.assertEqual((r.status_code, r.json()['details']['reason']), (409, 'exit_2'))
        self.assertEqual(r.json()['store']['status'], 'error')
        self.assertEqual(self.calls('checkout'), [])


class ConcurrentSyncTests(FakeUcpMixin, TransactionTestCase):
    def test_two_threads_create_exactly_one_cart(self):
        obj = pick('a', SHOP, V1)
        CartLine.objects.create(id='l1', object=obj, qty=1, created_at=timezone.now())
        touch_stores([SHOP])
        results, errors = [], []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait()
                results.append(sync.sync_store(SHOP))
            except Exception as exc:  # surfaced below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual([r['status'] for r in results], ['synced', 'synced'])
        self.assertEqual(len({r['cartId'] for r in results}), 1)
        self.assertEqual(len(self.calls('cart', 'create')), 1)
        self.assertEqual(len(self.calls()), 1)
