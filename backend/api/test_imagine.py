import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .imagine import layout, photos, prompt, providers, runner
from .models import (
    Candidate,
    CatalogQuery,
    Placement,
    RenderStatus,
    SearchRun,
    Space,
    SpaceObject,
    SpaceRender,
)
from .test_state_api import Base

FAKE = dict(IMAGINE_PROVIDER='fake', IMAGINE_FAKE_DELAY=0, IMAGINE_RUN_INLINE=True)
TINY_JPEG = (b'\xff\xd8\xff\xe0fake', 'image/jpeg')
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


@override_settings(**FAKE)
class RenderTests(Base):
    def setUp(self):
        self.space = self.make_space()
        self.sofa = self.make_object()
        self.lamp = self.make_object(name='Lamp', type='lighting', dims={'x': 12, 'y': 12})
        self.place(self.sofa, 10, 10)
        self.place(self.lamp, 100, 40)
        self.url = f'/api/spaces/{self.space["id"]}/renders/'

    def place(self, obj, x, y):
        return Placement.objects.create(
            space_id=self.space['id'], object_id=obj['id'], x=x, y=y
        )

    def post(self, body=None):
        return self.call('post', self.url, body or {})

    def test_create_serves_png(self):
        r = self.post()
        self.assertEqual(r.status_code, 202, r.content)
        body = r.json()
        self.assertFalse(body['reused'])
        render = body['render']
        self.assertEqual(render['status'], 'done')
        self.assertEqual(render['itemCount'], 2)
        self.assertEqual(render['photoCount'], 0)
        self.assertEqual(render['imageUrl'], f'/api/renders/{render["id"]}/image/')
        self.assertEqual(set(render['items'][0]), {
            'number', 'objectId', 'name', 'productKey', 'productTitle', 'hasPhoto'})

        img = self.client.get(render['imageUrl'])
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img['Content-Type'], 'image/png')
        self.assertTrue(img.content.startswith(PNG_MAGIC))
        self.assertIn('immutable', img['Cache-Control'])

    def test_reuse_identical_layout(self):
        first = self.post().json()['render']
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['reused'])
        self.assertEqual(r.json()['render']['id'], first['id'])

    def test_move_creates_new_render(self):
        first = self.post().json()['render']
        Placement.objects.filter(object_id=self.sofa['id']).update(x=20)
        r = self.post()
        self.assertEqual(r.status_code, 202)
        self.assertNotEqual(r.json()['render']['id'], first['id'])

    def test_force_creates_new_render(self):
        first = self.post().json()['render']
        r = self.post({'force': True})
        self.assertEqual(r.status_code, 202)
        self.assertNotEqual(r.json()['render']['id'], first['id'])

    def test_force_must_be_bool(self):
        r = self.post({'force': 'yes'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'validation_failed')

    def test_no_placements(self):
        empty = self.make_space(name='Empty')
        r = self.call('post', f'/api/spaces/{empty["id"]}/renders/', {})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'error': 'no_items'})

    def test_unknown_space(self):
        self.assertEqual(self.call('post', '/api/spaces/nope/renders/', {}).status_code, 404)
        self.assertEqual(
            self.call('get', '/api/spaces/nope/renders/latest/').status_code, 404)

    def test_gemini_without_key(self):
        with override_settings(IMAGINE_PROVIDER='gemini', GEMINI_API_KEY=''):
            r = self.post()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {'error': 'provider_not_configured'})
        self.assertEqual(SpaceRender.objects.count(), 0)

    def test_latest_current_flips_after_move(self):
        self.post()
        latest_url = f'/api/spaces/{self.space["id"]}/renders/latest/'
        body = self.call('get', latest_url).json()
        self.assertTrue(body['current'])
        self.assertEqual(body['render']['status'], 'done')

        Placement.objects.filter(object_id=self.lamp['id']).update(y=90)
        self.assertFalse(self.call('get', latest_url).json()['current'])

    def test_latest_without_renders(self):
        self.assertEqual(
            self.call('get', f'/api/spaces/{self.space["id"]}/renders/latest/').status_code, 404)

    def test_render_detail_and_image_404(self):
        render = self.post().json()['render']
        detail = self.call('get', f'/api/renders/{render["id"]}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['render']['id'], render['id'])
        self.assertNotIn('image', detail.json()['render'])
        self.assertEqual(
            self.call('get', '/api/renders/00000000-0000-0000-0000-000000000000/').status_code,
            404)

        running = SpaceRender.objects.create(
            space_id=self.space['id'], fingerprint='x', status=RenderStatus.RUNNING)
        self.assertEqual(
            self.client.get(f'/api/renders/{running.id}/image/').status_code, 404)

    def test_photo_prompt_and_no_photo_item(self):
        cand_run = SearchRun.objects.create(
            object=self.sofa_obj(), object_snapshot={}, target=1, max_rounds=1)
        query = CatalogQuery.objects.create(run=cand_run, round=1, strategy='s', payload={})
        cand = Candidate.objects.create(
            run=cand_run, query=query, product_gid='gid', title='Linen Sofa',
            image_url='https://example.com/sofa.jpg')
        obj = self.sofa_obj()
        obj.selected_candidate = cand
        obj.save()

        with mock.patch('api.imagine.photos.fetch_photo', return_value=TINY_JPEG):
            render = self.post({'force': True}).json()['render']
        stored = SpaceRender.objects.get(pk=render['id'])
        self.assertIn('image 2', stored.prompt)
        self.assertIn('Linen Sofa', stored.prompt)
        self.assertIn('No photo: render a plausible lighting', stored.prompt)
        self.assertEqual(render['photoCount'], 1)
        self.assertEqual(render['items'][0]['productKey'], f'cand_{cand.id}')

    def test_photo_fetch_failure_falls_back_to_no_photo(self):
        obj = self.sofa_obj()
        cand_run = SearchRun.objects.create(
            object=obj, object_snapshot={}, target=1, max_rounds=1)
        query = CatalogQuery.objects.create(run=cand_run, round=1, strategy='s', payload={})
        obj.selected_candidate = Candidate.objects.create(
            run=cand_run, query=query, product_gid='g', title='Sofa',
            image_url='https://example.com/x.jpg')
        obj.save()
        with mock.patch('api.imagine.photos.fetch_photo', return_value=None):
            render = self.post({'force': True}).json()['render']
        self.assertEqual(render['photoCount'], 0)
        self.assertEqual(render['status'], 'done')

    def sofa_obj(self):
        return SpaceObject.objects.get(pk=self.sofa['id'])

    def test_stale_running_render_is_interrupted(self):
        render = SpaceRender.objects.create(
            space_id=self.space['id'], fingerprint='stale', status=RenderStatus.RUNNING)
        SpaceRender.objects.filter(pk=render.pk).update(
            created_at=timezone.now() - timedelta(hours=1))
        r = self.call('get', f'/api/renders/{render.pk}/').json()['render']
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['error'], 'interrupted')
        self.assertIsNotNone(r['finishedAt'])

    def test_gemini_failure_is_recorded(self):
        with override_settings(IMAGINE_PROVIDER='gemini', GEMINI_API_KEY='k'), \
                mock.patch('api.imagine.providers.urllib.request.urlopen',
                           side_effect=OSError('boom')):
            render = self.post().json()['render']
        self.assertEqual(render['status'], 'failed')
        self.assertIn('boom', render['error'])


class LayoutTests(Base):
    def test_wall_footprint_uses_z(self):
        space = self.make_space(type='wall', dims={'x': 96, 'y': 96})
        obj = self.make_object(name='Art', type='wall-art', dims={'x': 40, 'y': 2, 'z': 30})
        Placement.objects.create(space_id=space['id'], object_id=obj['id'], x=5, y=6)
        items = layout.render_items(Space.objects.get(pk=space['id']))
        self.assertEqual((items[0]['w'], items[0]['h']), (40, 30))

    def test_floor_footprint_uses_y(self):
        self.assertEqual(layout.footprint('floor', {'x': 80, 'y': 36, 'z': 30}), (80, 36))
        self.assertEqual(layout.footprint('floor', {'x': 0.2}), (1, 1))

    def test_fingerprint_ignores_order(self):
        space = self.make_space()
        a = self.make_object(name='A')
        b = self.make_object(name='B')
        Placement.objects.create(space_id=space['id'], object_id=a['id'], x=0, y=0)
        Placement.objects.create(space_id=space['id'], object_id=b['id'], x=50, y=0)
        model = Space.objects.get(pk=space['id'])
        items = layout.render_items(model)
        self.assertEqual(
            layout.fingerprint(model, items), layout.fingerprint(model, list(reversed(items))))

    def test_aspect_ratio(self):
        space = Space.objects.get(pk=self.make_space(dims={'x': 160, 'y': 90})['id'])
        self.assertEqual(layout.aspect_ratio(space), '16:9')

    def test_prompt_wall_wording(self):
        space = Space.objects.get(pk=self.make_space(type='wall', dims={'x': 96, 'y': 84})['id'])
        text, index = prompt.build_prompt(space, [], set())
        self.assertIn('front elevation', text)
        self.assertIn('96 in wide by 84 in tall', text)
        self.assertEqual(index, {})


class ProviderParseTests(TestCase):
    def test_skips_thought_parts(self):
        data = {'candidates': [{'content': {'parts': [
            {'inlineData': {'mimeType': 'image/png', 'data': 'AAAA'}, 'thought': True},
            {'text': 'thinking'},
            {'inlineData': {'mimeType': 'image/png', 'data': 'AQID'}},
        ]}}]}
        image, mime = providers.parse_gemini_response(data)
        self.assertEqual(image, b'\x01\x02\x03')
        self.assertEqual(mime, 'image/png')

    def test_snake_case_keys(self):
        data = {'candidates': [{'content': {'parts': [
            {'inline_data': {'mime_type': 'image/jpeg', 'data': 'AQI='}}]}}]}
        self.assertEqual(providers.parse_gemini_response(data), (b'\x01\x02', 'image/jpeg'))

    def test_blocked_response_raises(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.parse_gemini_response({'promptFeedback': {'blockReason': 'SAFETY'}})
        self.assertIn('SAFETY', str(ctx.exception))

    def test_finish_reason_without_image_raises(self):
        data = {'candidates': [{'finishReason': 'NO_IMAGE', 'content': {'parts': [
            {'text': 'I cannot draw that'}]}}]}
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.parse_gemini_response(data)
        self.assertIn('NO_IMAGE', str(ctx.exception))

    def test_text_only_raises_with_text(self):
        data = {'candidates': [{'content': {'parts': [{'text': 'refused'}]}}]}
        with self.assertRaisesRegex(providers.ProviderError, 'refused'):
            providers.parse_gemini_response(data)

    def test_missing_key_is_not_configured(self):
        with override_settings(IMAGINE_PROVIDER='gemini', GEMINI_API_KEY=''):
            with self.assertRaises(providers.ProviderNotConfigured):
                providers.generate('p', b'g', [], '1:1')


class RunnerHelperTests(TestCase):
    def test_photo_cap_keeps_largest(self):
        items = [
            {'number': n, 'imageUrl': 'https://x', 'w': n, 'h': 1} for n in range(1, 13)
        ]
        kept = {i['number'] for i in runner._select_photo_items(items)}
        self.assertEqual(kept, set(range(3, 13)))

    def test_photo_failure_returns_none(self):
        self.assertIsNone(photos.fetch_photo('ftp://nope'))
