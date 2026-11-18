import codecs
import re
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from config import settings as project_settings
from . import urls as api_urls


def _normalize_url_pattern(route):
    """Map Django path converters to `/api/overview/` display placeholders."""
    path = '/api/' + route
    path = path.replace('/placements/<str:object_id>/', '/placements/<object_id>/')
    path = re.sub(r'<(?:str|uuid):(?:space_id|object_id|run_id|render_id|import_id|line_id)>', '<id>', path)
    path = re.sub(r'<(?:int|str):(\w+)>', r'<\1>', path)
    return path


class UnicodeEscapeCodecTests(SimpleTestCase):
    SAMPLES = (
        '',
        'plain ascii /api/spaces/nope/',
        'line1\nline2\r\ttab\\slash\'single"double',
        '\x00\x07\x08\x0b\x0c\x1b\x7f\x80\xe9\xff',
        'Bad Request: /api/objects/café ☃ 🎉',
    )

    def test_pure_python_and_c_codec_infos_match_stdlib(self):
        c_info = project_settings._build_unicode_escape_codec_info(use_c=True)
        py_info = project_settings._build_unicode_escape_codec_info(use_c=False)
        for sample in self.SAMPLES:
            with self.subTest(sample=sample):
                expected = sample.encode('unicode_escape')
                self.assertEqual(c_info.encode(sample), (expected, len(sample)))
                self.assertEqual(py_info.encode(sample), (expected, len(sample)))
                self.assertEqual(c_info.decode(expected), (sample, len(expected)))
                self.assertEqual(py_info.decode(expected), (sample, len(expected)))
                # Matches django.utils.log.log_response's exact escaping chain.
                self.assertTrue(py_info.encode(sample)[0].decode('ascii').isascii())

    def test_pure_python_decoder_handles_escapes_errors_and_streaming(self):
        py_info = project_settings._build_unicode_escape_codec_info(use_c=False)
        raw = b'\\101\\a\\b\\f\\v\\\'\\"\\N{SNOWMAN}line1\\\nline2\\q'
        self.assertEqual(py_info.decode(raw), (raw.decode('unicode_escape'), len(raw)))
        with self.assertRaises(UnicodeDecodeError):
            py_info.decode(b'bad\\xZZ', 'strict')
        self.assertEqual(
            py_info.decode(b'bad\\xZZ!', 'ignore'),
            (b'bad\\xZZ!'.decode('unicode_escape', 'ignore'), len(b'bad\\xZZ!')),
        )
        self.assertIn('\ufffd', py_info.decode(b'bad\\u12!', 'replace')[0])

        inc = py_info.incrementaldecoder()
        chunks = [b'caf\\xe', b'9 \\u26', b'03 \\U0001f3', b'89!']
        decoded = ''.join(
            inc.decode(chunk, final=(i == len(chunks) - 1)) for i, chunk in enumerate(chunks)
        )
        self.assertEqual(decoded, 'café ☃ 🎉!')

    def test_registers_fallback_only_when_lookup_fails(self):
        with mock.patch.object(codecs, 'register') as reg:
            project_settings._ensure_unicode_escape_codec()
            reg.assert_not_called()

        with mock.patch.object(codecs, 'lookup', side_effect=LookupError('unknown encoding: unicode_escape')), \
                mock.patch.object(codecs, 'register') as reg:
            project_settings._ensure_unicode_escape_codec()
            reg.assert_called_once_with(project_settings._unicode_escape_search)

        self.assertIsNotNone(project_settings._unicode_escape_search('unicode_escape'))
        self.assertIsNotNone(project_settings._unicode_escape_search('unicode-escape'))
        self.assertIsNone(project_settings._unicode_escape_search('utf-8'))


class ApiEndpointsTests(TestCase):
    def test_health_endpoint_returns_ok_payload(self):
        response = self.client.get(reverse('api:health'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')

        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['service'], 'shopify-apm-backend')
        self.assertEqual(payload['database'], 'connected')
        self.assertIn('timestamp', payload)

    def test_health_endpoint_supports_head_and_rejects_post(self):
        head_response = self.client.head(reverse('api:health'))
        self.assertEqual(head_response.status_code, 200)

        post_response = self.client.post(reverse('api:health'))
        self.assertEqual(post_response.status_code, 405)

    def test_4xx_responses_log_cleanly_with_unicode_escape(self):
        # Exercises django.utils.log.log_response across 400, 404, 405, 409 (including non-ASCII paths).
        r404 = self.client.get('/api/spaces/café-☃/')
        self.assertEqual(r404.status_code, 404)
        self.assertEqual(r404.json(), {'error': 'not_found'})

        r400 = self.client.post('/api/spaces/', data='{bad-☃', content_type='application/json')
        self.assertEqual(r400.status_code, 400)
        self.assertEqual(r400.json(), {'error': 'invalid_json'})

    def test_overview_endpoint_lists_all_registered_routes_with_unique_ids(self):
        response = self.client.get(reverse('api:overview'))
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload['project'], 'Shopify APM Workspace')
        endpoints = payload['endpoints']
        ids = [item['id'] for item in endpoints]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate endpoint ids in /api/overview/')
        method_paths = [(item['method'], item['path']) for item in endpoints]
        self.assertEqual(len(method_paths), len(set(method_paths)), 'duplicate (method, path) in /api/overview/')

        listed_paths = {item['path'] for item in endpoints}
        for pattern in api_urls.urlpatterns:
            expected_path = _normalize_url_pattern(str(pattern.pattern))
            self.assertIn(expected_path, listed_paths)

        for required in (
            ('POST', '/api/state/reset/'),
            ('DELETE', '/api/spaces/<id>/inspirations/'),
            ('GET', '/api/inspirations/'),
            ('GET', '/api/vibe-imports/'),
            ('GET', '/api/vibe-imports/<id>/'),
            ('POST', '/api/vibe-imports/<id>/dismiss/'),
            ('GET', '/api/cart/'),
            ('DELETE', '/api/cart/'),
            ('POST', '/api/cart/lines/'),
            ('PATCH', '/api/cart/lines/<id>/'),
            ('DELETE', '/api/cart/lines/<id>/'),
            ('POST', '/api/cart/stores/<domain>/sync/'),
            ('POST', '/api/cart/stores/<domain>/checkout/'),
        ):
            self.assertIn(required, method_paths)

    def test_cors_header_included_for_frontend_dev_origin(self):
        response = self.client.get(
            reverse('api:health'),
            HTTP_ORIGIN='http://localhost:5173',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get('Access-Control-Allow-Origin'),
            'http://localhost:5173',
        )
