"""Cart line endpoints, selection/delete hooks and the cart in the snapshot."""
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import CartLine, Candidate, CatalogQuery, MerchantCart, SearchRun, SpaceObject

FAKE = str(Path(__file__).parent / 'test_fixtures' / 'fake_ucp.py')
LINE_KEYS = {'id', 'objectId', 'candidateId', 'productId', 'storeId', 'qty', 'variantId'}
STORE_KEYS = {
    'storeId', 'status', 'mode', 'cartId', 'currency', 'totals', 'messages', 'lineItems',
    'permalink', 'fallback', 'error', 'syncedAt', 'checkoutOpenedAt', 'stale',
}


def make_object(oid, **kw):
    fields = dict(id=oid, name=oid, dims={'x': 10, 'y': 10}, type='furniture',
                  created_at=timezone.now())
    fields.update(kw)
    return SpaceObject.objects.create(**fields)


def make_candidate(obj, domain='shop.example.com', variant='gid://shopify/ProductVariant/1001',
                   **kw):
    run = SearchRun.objects.create(object=obj, object_snapshot={}, target=1, max_rounds=1)
    query = CatalogQuery.objects.create(run=run, round=1, strategy='t', payload={})
    num = variant.rsplit('/', 1)[-1]
    fields = dict(
        run=run, query=query, product_gid=f'gid://shopify/p/{uuid.uuid4().hex}',
        variant_gid=variant, title=f'Product {num}', seller_domain=domain,
        checkout_url=f'https://{domain}/buy/{num}:1' if domain else '',
        pdp_url=f'https://{domain}/products/{num}' if domain else '', verdict='accepted',
    )
    fields.update(kw)
    return Candidate.objects.create(**fields)


def pick(oid, domain='shop.example.com', variant='gid://shopify/ProductVariant/1001', **kw):
    """An object whose selected candidate sells `variant` at `domain`."""
    obj = SpaceObject.objects.filter(pk=oid).first() or make_object(oid)
    obj.selected_candidate = make_candidate(obj, domain, variant, **kw)
    obj.save(update_fields=['selected_candidate'])
    return obj


class FakeUcpMixin:
    """Points `ucp` at the fake, with a per-test state file and call log."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.mkdtemp(prefix='fake-ucp-')
        self.addCleanup(shutil.rmtree, tmp, True)
        self.state = os.path.join(tmp, 'state.json')
        env = mock.patch.dict(os.environ, {'FAKE_UCP_STATE': self.state})
        env.start()
        self.addCleanup(env.stop)
        cm = override_settings(SEARCHER_UCP_BIN=FAKE, CART_UCP_TIMEOUT=10, UCP_PROFILE='')
        cm.enable()
        self.addCleanup(cm.disable)
        self.addCleanup(self.assert_no_forbidden_calls)

    def calls(self, *command):
        try:
            with open(self.state + '.log.jsonl', encoding='utf-8') as fh:
                entries = [json.loads(line) for line in fh]
        except FileNotFoundError:
            return []
        return [e for e in entries if not command or tuple(e['argv'][:len(command)]) == command]

    def assert_no_forbidden_calls(self):
        for e in self.calls('checkout'):
            self.assertNotIn(e['argv'][1], ('complete', 'cancel'))

    def fake_state(self):
        with open(self.state, encoding='utf-8') as fh:
            return json.load(fh)

    def write_fake_state(self, state):
        with open(self.state, 'w', encoding='utf-8') as fh:
            json.dump(state, fh)


class Api(FakeUcpMixin, TestCase):
    def call(self, method, path, body=None):
        data = json.dumps(body) if body is not None else ''
        return getattr(self.client, method)(path, data=data, content_type='application/json')

    def add(self, *lines):
        r = self.call('post', '/api/cart/lines/', {'lines': list(lines)})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()


class CartLineTests(Api):
    def test_add_is_an_idempotent_upsert_by_object(self):
        a = pick('a')
        pick('b', 'other.example.com', 'gid://shopify/ProductVariant/2002')
        make_object('bare')
        body = self.add({'id': 'line_a', 'objectId': 'a', 'qty': 2}, {'id': 'line_b', 'objectId': 'b'},
                        {'id': 'line_x', 'objectId': 'bare'}, {'id': 'line_y', 'objectId': 'ghost'})
        self.assertEqual(body['dirtyStores'], ['other.example.com', 'shop.example.com'])
        self.assertEqual(body['skipped'], [{'objectId': 'bare', 'reason': 'no_selection'},
                                           {'objectId': 'ghost', 'reason': 'not_found'}])
        lines = {line['id']: line for line in body['lines']}
        self.assertEqual(set(lines), {'line_a', 'line_b'})
        self.assertEqual(set(lines['line_a']), LINE_KEYS)
        self.assertEqual(lines['line_a'], {
            'id': 'line_a', 'objectId': 'a', 'candidateId': a.selected_candidate_id,
            'productId': f'cand_{a.selected_candidate_id}', 'storeId': 'shop.example.com',
            'qty': 2, 'variantId': 'gid://shopify/ProductVariant/1001',
        })
        self.assertEqual(lines['line_b']['qty'], 1)

        # Replaying a legacy cart: same ids kept, qty kept unless sent, nothing dirty.
        again = self.add({'id': 'other_id', 'objectId': 'a'}, {'id': 'line_b', 'objectId': 'b'})
        self.assertEqual(again['dirtyStores'], [])
        self.assertEqual({line['id']: line['qty'] for line in again['lines']},
                         {'line_a': 2, 'line_b': 1})
        again = self.add({'objectId': 'a', 'qty': 5})
        self.assertEqual(again['dirtyStores'], ['shop.example.com'])
        self.assertEqual(CartLine.objects.get(pk='line_a').qty, 5)
        self.assertEqual(CartLine.objects.count(), 2)

    def test_taken_or_missing_id_gets_a_fresh_one(self):
        pick('a')
        pick('b', variant='gid://shopify/ProductVariant/2002')
        self.add({'id': 'dup', 'objectId': 'a'})
        body = self.add({'id': 'dup', 'objectId': 'b'})
        ids = sorted(line['id'] for line in body['lines'])
        self.assertEqual(ids[0], 'dup')
        self.assertTrue(ids[1].startswith('line_'))

    def test_validation(self):
        pick('a')
        for body, key in [
            ({'lines': [{'objectId': 'a', 'qty': 0}]}, 'lines[0].qty'),
            ({'lines': [{'objectId': 'a', 'qty': 21}]}, 'lines[0].qty'),
            ({'lines': [{'objectId': 'a', 'qty': True}]}, 'lines[0].qty'),
            ({'lines': [{'objectId': 'a', 'qty': 1.5}]}, 'lines[0].qty'),
            ({'lines': [{'objectId': 7}]}, 'lines[0].objectId'),
            ({'lines': [{'objectId': 'a', 'id': 'has space'}]}, 'lines[0].id'),
            ({'lines': [{'objectId': 'a', 'price': 1}]}, 'lines[0].price'),
            ({'lines': 'x'}, 'lines'),
            ({'lines': [], 'extra': 1}, 'extra'),
        ]:
            r = self.call('post', '/api/cart/lines/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(key, r.json()['details'])
        self.assertEqual(CartLine.objects.count(), 0)

    def test_patch_and_delete(self):
        pick('a')
        self.add({'id': 'l1', 'objectId': 'a'})
        r = self.call('patch', '/api/cart/lines/l1/', {'qty': 4})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['line']['qty'], 4)
        self.assertEqual(r.json()['dirtyStores'], ['shop.example.com'])
        self.assertEqual(self.call('patch', '/api/cart/lines/l1/', {'qty': 4}).json()['dirtyStores'], [])
        for bad in ({'qty': 21}, {'qty': -1}, {}, {'qty': 2, 'x': 1}):
            self.assertEqual(self.call('patch', '/api/cart/lines/l1/', bad).status_code, 400)
        r = self.call('patch', '/api/cart/lines/l1/', {'qty': 0})
        self.assertEqual(r.json(), {'line': None, 'dirtyStores': ['shop.example.com']})
        self.assertFalse(CartLine.objects.exists())
        self.assertEqual(self.call('patch', '/api/cart/lines/l1/', {'qty': 1}).status_code, 404)

        self.add({'id': 'l2', 'objectId': 'a'})
        r = self.call('delete', '/api/cart/lines/l2/')
        self.assertEqual(r.json(), {'dirtyStores': ['shop.example.com']})
        r = self.call('delete', '/api/cart/lines/l2/')
        self.assertEqual((r.status_code, r.json()), (404, {'error': 'not_found'}))

    def test_clear_cart(self):
        pick('a')
        pick('b', 'other.example.com')
        self.add({'objectId': 'a'}, {'objectId': 'b'})
        before = MerchantCart.objects.get(pk='shop.example.com').revision
        r = self.call('delete', '/api/cart/')
        self.assertEqual(r.json(), {'dirtyStores': ['other.example.com', 'shop.example.com']})
        self.assertFalse(CartLine.objects.exists())
        self.assertEqual(MerchantCart.objects.get(pk='shop.example.com').revision, before + 1)
        self.assertEqual(self.call('delete', '/api/cart/').json(), {'dirtyStores': []})

    def test_get_cart_shape(self):
        pick('a')
        self.add({'id': 'l1', 'objectId': 'a', 'qty': 3})
        body = self.call('get', '/api/cart/').json()
        self.assertEqual(set(body), {'lines', 'stores'})
        self.assertEqual([line['id'] for line in body['lines']], ['l1'])
        (store,) = body['stores']
        self.assertEqual(set(store), STORE_KEYS)
        self.assertEqual((store['storeId'], store['status'], store['mode'], store['stale']),
                         ('shop.example.com', 'dirty', 'checkout', True))
        self.assertEqual(self.calls(), [])  # GET never calls the merchant


class HookTests(Api):
    def test_selection_move_keeps_the_line_and_touches_both_stores(self):
        a = pick('a')
        first = a.selected_candidate
        self.add({'id': 'l1', 'objectId': 'a', 'qty': 2})
        second = make_candidate(a, 'other.example.com', 'gid://shopify/ProductVariant/3003')
        MerchantCart.objects.update(status='synced')
        r = self.call('post', '/api/objects/a/select/', {'candidateId': second.pk})
        self.assertEqual(r.status_code, 200, r.content)
        line = self.call('get', '/api/cart/').json()['lines'][0]
        self.assertEqual((line['id'], line['qty'], line['storeId'], line['candidateId']),
                         ('l1', 2, 'other.example.com', second.pk))
        self.assertEqual(
            dict(MerchantCart.objects.values_list('domain', 'status')),
            {'shop.example.com': 'dirty', 'other.example.com': 'dirty'})
        self.assertNotEqual(first.pk, second.pk)

    def test_clearing_selection_deletes_the_line(self):
        pick('a')
        self.add({'objectId': 'a'})
        r = self.call('post', '/api/objects/a/select/', {'candidateId': None})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(CartLine.objects.exists())
        self.assertEqual(MerchantCart.objects.get(pk='shop.example.com').status, 'dirty')

    def test_select_without_a_line_touches_nothing(self):
        a = pick('a')
        other = make_candidate(a, 'other.example.com')
        self.call('post', '/api/objects/a/select/', {'candidateId': other.pk})
        self.assertFalse(MerchantCart.objects.exists())

    def test_deleting_an_object_drops_its_line_and_touches_the_store(self):
        pick('a')
        self.add({'objectId': 'a'})
        MerchantCart.objects.update(status='synced')
        self.assertEqual(self.call('delete', '/api/objects/a/').status_code, 204)
        self.assertFalse(CartLine.objects.exists())
        self.assertEqual(MerchantCart.objects.get(pk='shop.example.com').status, 'dirty')
        store = self.call('get', '/api/cart/').json()['stores'][0]
        self.assertEqual((store['status'], store['stale']), ('empty', True))

    def test_state_snapshot_carries_the_cart(self):
        pick('a')
        self.add({'id': 'l1', 'objectId': 'a'})
        body = self.call('get', '/api/state/').json()
        self.assertEqual(set(body['cart']), {'lines', 'stores'})
        self.assertEqual(body['cart']['lines'][0]['id'], 'l1')

    def test_import_keeps_lines_and_ignores_a_cart_key(self):
        a = pick('a')
        self.add({'id': 'l1', 'objectId': 'a'})
        payload = {
            'version': 1, 'spaces': [],
            'objects': [{'id': 'a', 'name': 'Sofa', 'type': 'furniture', 'dims': {'x': 1, 'y': 1},
                         'createdAt': a.created_at.isoformat()}],
            'cart': [{'id': 'old', 'productId': 'cand_1', 'qty': 1}],
        }
        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('cart', r.json()['ignored'])
        self.assertEqual(list(CartLine.objects.values_list('pk', flat=True)), ['l1'])
        self.assertEqual(self.call('get', '/api/cart/').json()['lines'][0]['storeId'],
                         'shop.example.com')
