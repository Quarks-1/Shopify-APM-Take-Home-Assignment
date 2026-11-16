import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    DimensionUnit,
    Material,
    Object,
    ObjectType,
    SimilarItem,
    Space,
    SpaceType,
    Style,
)


def make_space(**overrides):
    values = {
        'x': Decimal('10'),
        'y': Decimal('8'),
        'space_type': SpaceType.FLOOR,
        'dimension_unit': DimensionUnit.FT,
    }
    values.update(overrides)
    return Space.objects.create(**values)


def make_object(**overrides):
    values = {
        'x': Decimal('1'),
        'y': Decimal('2'),
        'weight': Decimal('5'),
        'object_type': ObjectType.FURNITURE,
        'material': Material.WOOD,
        'style': Style.MODERN,
        'price_limit': Decimal('20'),
    }
    values.update(overrides)
    return Object.objects.create(**values)


class ObjectApiTests(TestCase):
    def patch_object(self, object_id, payload, client=None):
        http = client or self.client
        return http.patch(
            reverse('api:object-update', args=[object_id]),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_space_list_includes_default_one_inch_grid(self):
        space = make_space()
        response = self.client.get(reverse('api:space-list'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload['spaces']), 1)
        row = payload['spaces'][0]
        self.assertEqual(row['id'], space.id)
        self.assertEqual(row['editing_grid_increment_inches'], '1.0000')
        self.assertEqual(row['dimension_unit'], 'ft')
        self.assertEqual(row['object_count'], 0)

    def test_list_space_objects_returns_every_attribute(self):
        floor = make_space()
        shelf = make_space(space_type=SpaceType.SHELF, x=Decimal('4'), y=Decimal('1'))
        placed = make_object(z=Decimal('3'))
        placed.spaces.add(floor)
        other = make_object(object_type=ObjectType.POSTER, material=Material.METAL)
        other.spaces.add(shelf)
        SimilarItem.objects.create(
            parent_object=placed,
            name='Oak bench',
            image='https://example.com/bench.jpg',
        )

        response = self.client.get(reverse('api:space-objects', args=[floor.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['space']['id'], floor.id)
        self.assertEqual(payload['space']['editing_grid_increment_inches'], '1.0000')
        self.assertEqual(len(payload['objects']), 1)
        obj = payload['objects'][0]
        self.assertEqual(
            set(obj),
            {
                'id',
                'x',
                'y',
                'z',
                'weight',
                'object_type',
                'material',
                'style',
                'price_limit',
                'similar_items',
                'space_ids',
            },
        )
        self.assertEqual(obj['id'], placed.id)
        self.assertEqual(obj['z'], '3.0000')
        self.assertEqual(obj['space_ids'], [floor.id])
        self.assertEqual(obj['similar_items'][0]['name'], 'Oak bench')
        self.assertEqual(obj['similar_items'][0]['image'], 'https://example.com/bench.jpg')
        self.assertEqual({item['id'] for item in payload['spaces']}, {floor.id, shelf.id})

    def test_missing_space_returns_json_404(self):
        response = self.client.get(reverse('api:space-objects', args=[99999]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response.json()['error'], 'Space not found.')

    def test_patch_snaps_coordinates_to_one_inch_grid(self):
        space = make_space()
        obj = make_object(x=Decimal('1'), y=Decimal('1'))
        obj.spaces.add(space)
        response = self.patch_object(
            obj.id,
            {'x': '1.04', 'y': '2.10', 'z': '0.04', 'editing_space_id': space.id},
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['x'], '1.0000')
        self.assertEqual(body['y'], '2.0833')
        self.assertEqual(body['z'], '0.0000')
        obj.refresh_from_db()
        self.assertEqual(obj.x, Decimal('1.0000'))
        self.assertEqual(obj.weight, Decimal('5.0000'))

    def test_patch_snaps_to_the_space_grid_increment(self):
        space = make_space(editing_grid_increment_inches=Decimal('6'))
        obj = make_object(x=Decimal('0.4'))
        obj.spaces.add(space)
        response = self.patch_object(obj.id, {'x': '0.4', 'editing_space_id': space.id})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['x'], '0.5000')

    def test_patch_snaps_meters_to_inches(self):
        space = make_space(dimension_unit=DimensionUnit.M, x=Decimal('4'), y=Decimal('3'))
        obj = make_object()
        obj.spaces.add(space)
        response = self.patch_object(obj.id, {'x': '0.03', 'editing_space_id': space.id})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['x'], '0.0254')

    def test_patch_can_clear_optional_z(self):
        space = make_space()
        obj = make_object(z=Decimal('2'))
        obj.spaces.add(space)
        response = self.patch_object(obj.id, {'z': None, 'editing_space_id': space.id})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()['z'])
        obj.refresh_from_db()
        self.assertIsNone(obj.z)

    def test_patch_updates_attributes_similar_items_and_parents(self):
        floor = make_space()
        shelf = make_space(space_type=SpaceType.SHELF)
        obj = make_object()
        obj.spaces.add(floor)
        kept = SimilarItem.objects.create(
            parent_object=obj,
            name='Old name',
            image='https://example.com/old.jpg',
        )
        SimilarItem.objects.create(
            parent_object=obj,
            name='Remove me',
            image='https://example.com/gone.jpg',
        )
        response = self.patch_object(
            obj.id,
            {
                'object_type': 'poster',
                'material': 'fabric',
                'style': 'brutalist',
                'weight': '1.5',
                'price_limit': '10.5',
                'similar_items': [
                    {
                        'id': kept.id,
                        'name': 'Linen print',
                        'image': 'https://example.com/linen.jpg',
                    },
                    {'name': 'New match', 'image': 'https://example.com/new.jpg'},
                ],
                'space_ids': [floor.id, shelf.id],
                'editing_space_id': floor.id,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['object_type'], 'poster')
        self.assertEqual(body['material'], 'fabric')
        self.assertEqual(body['style'], 'brutalist')
        self.assertEqual(body['weight'], '1.5000')
        self.assertEqual(body['price_limit'], '10.50')
        self.assertEqual(body['space_ids'], [floor.id, shelf.id])
        names = [item['name'] for item in body['similar_items']]
        self.assertEqual(names, ['Linen print', 'New match'])
        self.assertEqual(body['similar_items'][0]['image'], 'https://example.com/linen.jpg')

        shelf_objects = self.client.get(reverse('api:space-objects', args=[shelf.id])).json()
        self.assertEqual([item['id'] for item in shelf_objects['objects']], [obj.id])

    def test_patch_rejects_invalid_values_without_saving(self):
        space = make_space()
        obj = make_object()
        obj.spaces.add(space)
        response = self.patch_object(
            obj.id,
            {'material': 'glass', 'weight': '-1', 'editing_space_id': space.id},
        )
        self.assertEqual(response.status_code, 400)
        errors = response.json()['errors']
        self.assertIn('material', errors)
        self.assertIn('weight', errors)
        obj.refresh_from_db()
        self.assertEqual(obj.material, Material.WOOD)
        self.assertEqual(obj.weight, Decimal('5.0000'))

    def test_patch_missing_object_and_bad_json(self):
        missing = self.patch_object(99999, {'x': '1'})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['error'], 'Object not found.')

        obj = make_object()
        bad = self.client.patch(
            reverse('api:object-update', args=[obj.id]),
            data='{',
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()['error'], 'Invalid JSON.')

        wrong = self.client.get(reverse('api:object-update', args=[1]))
        self.assertEqual(wrong.status_code, 405)

    def test_patch_does_not_require_a_csrf_token(self):
        space = make_space()
        obj = make_object()
        obj.spaces.add(space)
        client = Client(enforce_csrf_checks=True)
        response = self.patch_object(
            obj.id,
            {'material': 'metal', 'editing_space_id': space.id},
            client=client,
        )
        self.assertEqual(response.status_code, 200, response.content)
        obj.refresh_from_db()
        self.assertEqual(obj.material, Material.METAL)

    def test_health_and_overview_still_respond(self):
        health = self.client.get(reverse('api:health'))
        overview = self.client.get(reverse('api:overview'))
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()['status'], 'ok')
        self.assertEqual(overview.status_code, 200)
        paths = [item['path'] for item in overview.json()['endpoints']]
        self.assertIn('/api/health/', paths)
        self.assertIn('/api/overview/', paths)
        self.assertIn('/api/spaces/', paths)
        self.assertIn('/api/spaces/<id>/objects/', paths)
        self.assertIn('/api/objects/<id>/', paths)
