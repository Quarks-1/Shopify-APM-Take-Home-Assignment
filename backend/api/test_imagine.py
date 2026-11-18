import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

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

    def test_layout_edit_mid_render_keeps_snapshot_consistent_with_fingerprint(self):
        space_obj = Space.objects.get(pk=self.space['id'])
        initial_fp = layout.fingerprint(space_obj, layout.render_items(space_obj))
        real_generate = providers.generate

        def mutate_then_generate(text, guide, photo_list, aspect):
            # Simulate the user moving an object and deleting another mid-render.
            Placement.objects.filter(object_id=self.sofa['id']).update(x=55, y=60)
            Placement.objects.filter(object_id=self.lamp['id']).delete()
            return real_generate(text, guide, photo_list, aspect)

        with mock.patch('api.imagine.providers.generate', side_effect=mutate_then_generate):
            res = self.post()
        self.assertEqual(res.status_code, 202, res.content)
        render_json = res.json()['render']
        self.assertEqual(render_json['fingerprint'], initial_fp)
        self.assertEqual(render_json['itemCount'], 2)

        stored = SpaceRender.objects.get(pk=render_json['id'])
        self.assertIn('10 in from the left and 10 in from the top', stored.prompt)
        self.assertNotIn('55 in from the left', stored.prompt)

        # Because the live layout moved mid-render, /renders/latest/ reports current=False.
        latest = self.call('get', f'/api/spaces/{self.space["id"]}/renders/latest/').json()
        self.assertFalse(latest['current'])
        self.assertEqual(latest['render']['id'], render_json['id'])

    def test_direct_run_render_without_snapshot_updates_fingerprint(self):
        render = SpaceRender.objects.create(
            space_id=self.space['id'], fingerprint='stale_fp', status=RenderStatus.RUNNING
        )
        runner.run_render(render.pk)
        render.refresh_from_db()
        space_obj = Space.objects.get(pk=self.space['id'])
        expected_fp = layout.fingerprint(space_obj, layout.render_items(space_obj))
        self.assertEqual(render.status, RenderStatus.DONE)
        self.assertEqual(render.fingerprint, expected_fp)


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


def make_item(number, name, x, y, w, h, **extra):
    item = {
        'number': number, 'objectId': number, 'name': name, 'type': 'furniture',
        'material': None, 'style': None, 'x': x, 'y': y, 'w': w, 'h': h,
        'dims': {'x': w, 'y': h}, 'productTitle': None,
    }
    item.update(extra)
    return item


def make_space_stub(width, depth, type='floor', name='Living room'):
    return SimpleNamespace(type=type, name=name, dims={'x': width, 'y': depth})


def example_layout():
    space = make_space_stub(144, 180)
    items = [
        make_item(1, 'Shelving unit', 19, 10, 24, 24, dims={'x': 24, 'y': 24, 'z': 72}),
        make_item(2, 'Floor lamp', 125, 10, 14, 14),
        make_item(3, 'Sofa', 19, 96, 35, 78, dims={'x': 35, 'y': 78, 'z': 32}),
        make_item(4, 'Coffee table', 72, 108, 24, 48),
        make_item(5, 'TV stand', 118, 100, 25, 66),
    ]
    return space, items


class SpatialHelperTests(TestCase):
    def test_item_facts(self):
        facts = layout.item_facts(make_item(1, 'A', 36, 45, 72, 90), 144, 180)
        self.assertEqual(facts['left_pct'], 25.0)
        self.assertEqual(facts['right_pct'], 75.0)
        self.assertEqual(facts['top_pct'], 25.0)
        self.assertEqual(facts['bottom_pct'], 75.0)
        self.assertEqual((facts['center_x_pct'], facts['center_y_pct']), (50.0, 50.0))
        self.assertEqual((facts['width_pct'], facts['depth_pct']), (50.0, 50.0))
        self.assertEqual(facts['wall_in'], {'left': 36, 'right': 36, 'top': 45, 'bottom': 45})

    def test_clear_gaps_neighbours_and_walls(self):
        _space, items = example_layout()
        sofa = layout.clear_gaps(items[2], items, 144, 180)
        # The shelving unit overlaps the sofa horizontally: 96 - 34 = 62 in above it.
        self.assertEqual(sofa['top'], {'inches': 62, 'pct': 34.4, 'to': 1})
        # Coffee table starts at x 72, sofa ends at x 54.
        self.assertEqual(sofa['right'], {'inches': 18, 'pct': 12.5, 'to': 4})
        self.assertEqual(sofa['left'], {'inches': 19, 'pct': 13.2, 'to': 'wall'})
        self.assertEqual(sofa['bottom'], {'inches': 6, 'pct': 3.3, 'to': 'wall'})

    def test_clear_gaps_ignore_non_overlapping_neighbours(self):
        a = make_item(1, 'A', 0, 0, 10, 10)
        b = make_item(2, 'B', 50, 50, 10, 10)
        gaps = layout.clear_gaps(a, [a, b], 100, 100)
        self.assertEqual(gaps['right'], {'inches': 90, 'pct': 90.0, 'to': 'wall'})
        self.assertEqual(gaps['bottom'], {'inches': 90, 'pct': 90.0, 'to': 'wall'})

    def test_clear_gaps_single_item_and_touching_wall(self):
        a = make_item(1, 'A', 0, 20, 100, 10)
        gaps = layout.clear_gaps(a, [a], 100, 100)
        self.assertEqual(gaps['left']['inches'], 0)
        self.assertEqual(gaps['right']['inches'], 0)
        self.assertEqual(gaps['top'], {'inches': 20, 'pct': 20.0, 'to': 'wall'})
        self.assertEqual(gaps['bottom'], {'inches': 70, 'pct': 70.0, 'to': 'wall'})

    def test_clear_gaps_overlapping_items_have_no_negative_gap(self):
        a = make_item(1, 'A', 10, 10, 30, 30)
        b = make_item(2, 'B', 30, 10, 30, 30)
        gaps = layout.clear_gaps(a, [a, b], 100, 100)
        self.assertEqual(gaps['right'], {'inches': 60, 'pct': 60.0, 'to': 'wall'})
        self.assertEqual(gaps['left'], {'inches': 10, 'pct': 10.0, 'to': 'wall'})

    def test_empty_bands_example(self):
        _space, items = example_layout()
        self.assertEqual(
            layout.empty_bands(items, 144, 180, 'y'), [(0, 10), (34, 96), (174, 180)])
        self.assertEqual(
            layout.empty_bands(items, 144, 180, 'x'),
            [(0, 19), (54, 72), (96, 118), (143, 144)])

    def test_empty_bands_no_items(self):
        self.assertEqual(layout.empty_bands([], 100, 80, 'y'), [(0, 80)])
        self.assertEqual(layout.empty_bands([], 100, 80, 'x'), [(0, 100)])

    def test_empty_bands_one_item_touching_wall(self):
        a = make_item(1, 'A', 0, 0, 40, 20)
        self.assertEqual(layout.empty_bands([a], 100, 80, 'y'), [(20, 80)])
        self.assertEqual(layout.empty_bands([a], 100, 80, 'x'), [(40, 100)])

    def test_empty_bands_overlapping_and_adjacent_items_merge(self):
        items = [
            make_item(1, 'A', 0, 10, 10, 30),
            make_item(2, 'B', 0, 30, 10, 30),
            make_item(3, 'C', 0, 60, 10, 10),
        ]
        self.assertEqual(layout.empty_bands(items, 100, 100, 'y'), [(0, 10), (70, 100)])

    def test_empty_bands_clamp_items_outside_room(self):
        a = make_item(1, 'A', -20, 90, 40, 40)
        self.assertEqual(layout.empty_bands([a], 100, 100, 'y'), [(0, 90)])

    def test_covered_pct_counts_overlap_once(self):
        a = make_item(1, 'A', 0, 0, 50, 50)
        b = make_item(2, 'B', 25, 0, 50, 50)
        self.assertEqual(layout.covered_pct([a, b], 100, 100), 37.5)
        self.assertEqual(layout.covered_pct([], 100, 100), 0.0)


class GuideImageTests(TestCase):
    def open(self, space, items):
        return Image.open(io.BytesIO(layout.layout_guide_png(space, items))).convert('RGB')

    def test_size_matches_aspect_ratio(self):
        space, items = example_layout()
        self.assertEqual(layout.aspect_ratio(space), '4:5')
        self.assertEqual(self.open(space, items).size, (819, 1024))
        wide = make_space_stub(160, 90)
        self.assertEqual(self.open(wide, []).size, (1024, 576))

    def test_room_fills_image_with_no_margin(self):
        space, items = example_layout()
        img = self.open(space, items)
        w, h = img.size
        # The outline sits on the image edges, no white margin.
        for point in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (0, h // 2)]:
            self.assertEqual(img.getpixel(point), (40, 40, 40), point)

    def test_box_bounds_have_no_offset(self):
        space, items = example_layout()
        img = self.open(space, items)
        w, h = img.size
        sx, sy = w / 144, h / 180
        sofa = items[2]
        left, top = round(sofa['x'] * sx), round(sofa['y'] * sy)
        right = round((sofa['x'] + sofa['w']) * sx)
        bottom = round((sofa['y'] + sofa['h']) * sy)
        self.assertEqual(img.getpixel((left, top + 40)), (30, 30, 30))
        self.assertEqual(img.getpixel((right, bottom - 40)), (30, 30, 30))
        self.assertNotEqual(img.getpixel((left - 4, top + 40)), (30, 30, 30))
        # Inside the box is its palette color (box 3), outside is the white/grid ground.
        self.assertEqual(img.getpixel((left + 6, top + 6)), layout.PALETTE[2])

    def test_no_text_labels_on_wall_guide(self):
        space = make_space_stub(96, 96, type='wall')
        img = self.open(space, [])
        self.assertEqual(img.size, (1024, 1024))
        self.assertEqual(img.getpixel((500, 6)), (255, 255, 255))
        self.assertEqual(img.getpixel((500, 1017)), (255, 255, 255))


class SpatialPromptTests(TestCase):
    def test_example_prompt(self):
        space, items = example_layout()
        text, index = prompt.build_prompt(space, items, {3})
        self.assertEqual(index, {3: 2})
        self.assertIn('Scale and spacing (must be respected):', text)
        self.assertLess(text.index('Scale and spacing'), text.index('Item 1:'))
        self.assertIn('IS the frame', text)
        self.assertIn('do NOT enlarge objects', text)
        # The large open band between the top objects and the sofa/TV.
        self.assertIn(
            'Open floor: no objects between 34 in and 96 in from the top '
            '(34.4% of the room depth)', text)
        # Percentage positions, wall distances and gaps for the sofa.
        self.assertIn('left edge 13.2%, right edge 37.5%, top edge 53.3%, bottom edge 96.7%', text)
        self.assertIn('left 19 in, right 90 in, top 96 in, bottom 6 in', text)
        self.assertIn('top 62 in (34.4%) to item 1', text)
        self.assertIn('covers 24.3% of the image width x 43.3% of the image height', text)
        # Scale line and footprint dims; heights are not passed to the model.
        self.assertIn('1 inch = 0.69% of the image width, 0.56% of the image height', text)
        self.assertIn('a line every 12 in', text)
        self.assertIn('35 W x 78 D in', text)
        self.assertNotIn('32 H in', text)
        self.assertNotIn(' H in', text)
        self.assertNotIn('Object heights', text)
        self.assertIn(
            'Create a photorealistic, perfectly flat, true-orthographic (zero perspective: a '
            'tall object must look exactly as wide as its footprint, like an architectural '
            'plan rendering) top-down view of a floor called', text)
        self.assertTrue(text.endswith(
            "The image shows ONLY the floor surface, edge to edge: no walls, no baseboards, "
            "no windows, no ceiling, no wall shadows visible. The frame is a tight crop of the "
            "floor, so every object's size in the image is exactly its footprint percentage "
            "given above and no larger."))
        self.assertGreater(
            text.index('The image shows ONLY'), text.index('Leave every area outside the boxes'))
        self.assertIn('product photo is image 2', text)
        self.assertIn('No photo: render a plausible furniture', text)

    def test_prompt_is_deterministic(self):
        space, items = example_layout()
        self.assertEqual(
            prompt.build_prompt(space, items, set()), prompt.build_prompt(space, items, set()))

    def test_wall_prompt_orientation_and_no_heights_note(self):
        space = make_space_stub(96, 84, type='wall', name='Wall')
        items = [make_item(1, 'Art', 10, 20, 40, 30, dims={'x': 40, 'y': 2, 'z': 30})]
        text, _ = prompt.build_prompt(space, items, set())
        self.assertIn('top edge of the frame is the ceiling', text)
        self.assertIn('ceiling 20 in, floor 34 in', text)
        self.assertIn('40 W x 30 H in, projecting 2 in from the wall', text)
        self.assertNotIn('Object heights', text)
        self.assertTrue(text.endswith(
            "The image shows ONLY the wall surface, edge to edge: no floor, no ceiling, no "
            "adjoining walls visible. The frame is a tight crop of the wall, so every object's "
            "size in the image is exactly its footprint percentage given above and no larger."))

    def test_empty_space_prompt(self):
        space = make_space_stub(100, 80)
        text, index = prompt.build_prompt(space, [], set())
        self.assertEqual(index, {})
        self.assertIn('Open floor: no objects between 0 in and 80 in from the top (100% ', text)
        self.assertIn('Objects cover 0% of the floor', text)


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
