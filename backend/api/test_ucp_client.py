"""ucp_client merchant commands: argv building, refusals and error parsing."""
import json
import os
import subprocess
from unittest import mock

from django.test import SimpleTestCase, override_settings

from .searcher import ucp_client
from .searcher.ucp_client import ID_RE, UcpError
from .test_cart_api import FAKE, FakeUcpMixin

REAL_RUN = subprocess.run
LINES = {'line_items': [{'item': {'id': 'gid://shopify/ProductVariant/1'}, 'quantity': 1}]}


class Spy:
    """Records subprocess.run calls, then runs the real thing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        entry = {'argv': list(argv), 'kw': kw}
        if '--input' in argv:
            path = argv[argv.index('--input') + 1][1:]
            with open(path, encoding='utf-8') as fh:
                entry['payload'] = json.load(fh)
            entry['path'] = path
        self.calls.append(entry)
        return REAL_RUN(argv, **kw)


def completed(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class ClientTests(FakeUcpMixin, SimpleTestCase):
    def spy(self):
        spy = Spy()
        patcher = mock.patch('api.searcher.ucp_client.subprocess.run', spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return spy

    def test_cart_argv_and_round_trip(self):
        spy = self.spy()
        env = ucp_client.cart_create('Shop.Example.com', LINES)
        cart_id = env['result']['id']
        self.assertRegex(cart_id, r'^gid://shopify/Cart/[A-Za-z0-9]+\?key=[0-9a-f]{32}$')
        self.assertTrue(ID_RE.match(cart_id))
        self.assertTrue(env['result']['continue_url'].startswith('https://shop.example.com/cart/c/'))
        ucp_client.cart_update('shop.example.com', cart_id, LINES)
        ucp_client.cart_get('shop.example.com', cart_id)
        create, update, get = spy.calls
        self.assertEqual(create['argv'][:5],
                         [FAKE, 'cart', 'create', '--business', 'https://shop.example.com'])
        self.assertEqual(create['argv'][-4:-2], ['--input', '@' + create['path']])
        self.assertEqual(create['argv'][-2:], ['--format', 'json'])
        self.assertEqual(create['payload'], LINES)
        self.assertFalse(create['kw'].get('shell'))
        self.assertEqual(create['kw']['timeout'], 10)
        self.assertFalse(os.path.exists(create['path']))
        self.assertEqual(update['argv'][1:4], ['cart', 'update', cart_id])
        self.assertEqual(get['argv'], [FAKE, 'cart', 'get', cart_id, '--business',
                                       'https://shop.example.com', '--format', 'json'])
        self.assertNotIn('--profile', create['argv'])

    def test_profile_flag(self):
        spy = self.spy()
        with override_settings(UCP_PROFILE='agent_1'):
            ucp_client.cart_create('shop.example.com', LINES)
        self.assertIn(['--profile', 'agent_1'],
                      [spy.calls[0]['argv'][i:i + 2] for i in range(len(spy.calls[0]['argv']))])
        with override_settings(UCP_PROFILE='bad profile; rm'):
            with self.assertRaises(UcpError) as cm:
                ucp_client.cart_create('shop.example.com', LINES)
        self.assertEqual(cm.exception.code, 'invalid_profile')
        self.assertEqual(len(spy.calls), 1)

    def test_search_never_passes_profile_or_business(self):
        spy = self.spy()
        with override_settings(UCP_PROFILE='agent'):
            ucp_client.search_catalog({'query': 'sofa'})
        argv = spy.calls[0]['argv']
        self.assertEqual(argv[:3], [FAKE, 'catalog', 'search'])
        self.assertNotIn('--profile', argv)
        self.assertNotIn('--business', argv)

    def test_unsafe_values_are_refused_before_exec(self):
        spy = self.spy()
        for cart_id in ['-rf', '--format=x', 'a b', 'x;rm -rf /', '$(id)', '`id`', '', 'x' * 256,
                        'a\nb', 'gid://shopify/Cart/1\n', 'gid://shopify/Cart/1|cat']:
            with self.assertRaises(UcpError, msg=cart_id) as cm:
                ucp_client.cart_update('shop.example.com', cart_id, LINES)
            self.assertEqual(cm.exception.code, 'invalid_argument')
        for domain in ['https://shop.example.com', 'localhost', 'shop example.com', '-x.com',
                       'shop.example.com/evil', 'shop.example.com:8443', '', None, 'a.b;rm',
                       'unknown-seller', '127.0.0.1', 'shop.example.com\n']:
            with self.assertRaises(UcpError, msg=domain) as cm:
                ucp_client.cart_create(domain, LINES)
            self.assertEqual(cm.exception.code, 'invalid_domain')
        self.assertEqual(spy.calls, [])

    def test_complete_and_cancel_checkout_are_forbidden(self):
        spy = self.spy()
        for command in (('checkout', 'complete'), ('checkout', 'cancel')):
            with self.assertRaises(UcpError) as cm:
                ucp_client._run(command, {}, positional=('gid://shopify/Checkout/x',),
                                business='shop.example.com')
            self.assertEqual(cm.exception.code, 'forbidden')
        self.assertEqual(spy.calls, [])
        self.assertFalse(hasattr(ucp_client, 'checkout_complete'))
        self.assertFalse(hasattr(ucp_client, 'checkout_cancel'))

    def test_cli_and_merchant_errors(self):
        cases = [
            (lambda: ucp_client.cart_create('noucp.example.com', LINES), 'OPERATION_NOT_OFFERED'),
            (lambda: ucp_client.cart_create('crash.example.com', LINES), 'exit_2'),
            (lambda: ucp_client.cart_update('shop.example.com', 'gid://shopify/Cart/nope?key=1', LINES),
             'cart_not_found'),
            (lambda: ucp_client.cart_cancel('shop.example.com', 'gid://shopify/Cart/nope'),
             'cart_not_found'),
            (lambda: ucp_client.checkout_create('shop.example.com', {'cart_id': 'x', 'line_items': []}),
             'cart_not_found'),
        ]
        for fn, code in cases:
            with self.assertRaises(UcpError, msg=code) as cm:
                fn()
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(UcpError) as cm:
            ucp_client.cart_update('shop.example.com', 'gid://shopify/Cart/nope', LINES)
        self.assertEqual(cm.exception.message, 'The requested cart does not exist')

    def test_error_shapes(self):
        cases = [
            (completed(json.dumps({'code': 'PROFILE_FETCH_FAILED', 'message': 'HTTP 404'}), 1),
             'PROFILE_FETCH_FAILED'),
            (completed(json.dumps({'code': 'AUTH_REQUIRED', 'message': 'login'}), 0), 'AUTH_REQUIRED'),
            (completed(json.dumps({'ucp': {'status': 'success'}, 'result': {'messages': []}})),
             'merchant_error'),
            (completed(json.dumps({'ucp': {'status': 'error'},
                                   'result': {'id': 'gid://x', 'messages': [{'type': 'error'}]}})),
             'merchant_error'),
            (completed('not json'), 'invalid_json'),
            (completed('', 3, 'boom'), 'exit_3'),
            (completed(json.dumps({'ucp': {}})), 'no_result'),
        ]
        for proc, code in cases:
            with mock.patch('api.searcher.ucp_client.subprocess.run', return_value=proc):
                with self.assertRaises(UcpError, msg=code) as cm:
                    ucp_client.cart_create('shop.example.com', LINES)
            self.assertEqual(cm.exception.code, code)
        # Catalog search has no result id: the merchant check does not apply.
        ok = completed(json.dumps({'ucp': {'status': 'success'}, 'result': {'products': []}}))
        with mock.patch('api.searcher.ucp_client.subprocess.run', return_value=ok):
            self.assertEqual(ucp_client.search_catalog({'query': 'x'})['result'], {'products': []})

    @override_settings(CART_UCP_TIMEOUT=0.3)
    def test_slow_merchant_times_out(self):
        with mock.patch.dict(os.environ, {'FAKE_UCP_SLOW': '3'}):
            with self.assertRaises(UcpError) as cm:
                ucp_client.cart_create('slow.example.com', LINES)
        self.assertEqual(cm.exception.code, 'timeout')
