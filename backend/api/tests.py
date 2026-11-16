from django.test import TestCase
from django.urls import reverse


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

    def test_overview_endpoint_lists_routes(self):
        response = self.client.get(reverse('api:overview'))
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload['project'], 'Shopify APM Workspace')
        paths = [item['path'] for item in payload['endpoints']]
        self.assertIn('/api/health/', paths)
        self.assertIn('/api/overview/', paths)
        self.assertEqual(len(paths), 2)

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
