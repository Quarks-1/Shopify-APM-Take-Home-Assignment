import json

from unittest import mock

from django.test import TestCase

from .models import Placement, Space, SpaceObject


class Base(TestCase):
    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body) if body is not None else '')
        return getattr(self.client, method)(path, data=data, content_type='application/json')

    def make_space(self, **extra):
        body = {'name': 'Living room', 'type': 'floor', 'unit': 'ft', 'dims': {'x': 120, 'y': 96}}
        body.update(extra)
        r = self.call('post', '/api/spaces/', body)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def make_object(self, **extra):
        body = {'name': 'Sofa', 'type': 'furniture', 'dims': {'x': 80, 'y': 36}}
        body.update(extra)
        r = self.call('post', '/api/objects/', body)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()


class SpaceTests(Base):
    def test_crud_and_shape(self):
        s = self.make_space()
        self.assertTrue(s['id'].startswith('space_'))
        self.assertEqual(
            set(s), {'id', 'name', 'type', 'unit', 'dims', 'placements', 'createdAt', 'updatedAt'}
        )
        self.assertEqual(s['dims'], {'x': 120, 'y': 96})
        self.assertEqual(s['placements'], [])
        self.assertEqual(self.call('get', f'/api/spaces/{s["id"]}/').json(), s)
        self.assertEqual(len(self.call('get', '/api/spaces/').json()), 1)

        r = self.call('patch', f'/api/spaces/{s["id"]}/',
                      {'name': ' Den ', 'weightLimit': 50, 'dims': {'x': 10, 'y': 5, 'z': 3}})
        self.assertEqual(r.status_code, 200)
        p = r.json()
        self.assertEqual(p['name'], 'Den')
        self.assertEqual(p['weightLimit'], 50)
        self.assertEqual(p['dims']['z'], 3)
        self.assertGreater(p['updatedAt'], s['updatedAt'])
        self.assertEqual(p['createdAt'], s['createdAt'])

        p = self.call('patch', f'/api/spaces/{s["id"]}/', {'weightLimit': None}).json()
        self.assertNotIn('weightLimit', p)

        self.assertEqual(self.call('delete', f'/api/spaces/{s["id"]}/').status_code, 204)
        self.assertEqual(self.call('get', f'/api/spaces/{s["id"]}/').status_code, 404)

    def test_custom_id_conflict(self):
        self.make_space(id='space_custom')
        r = self.call('post', '/api/spaces/',
                      {'id': 'space_custom', 'name': 'x', 'type': 'wall', 'unit': 'in',
                       'dims': {'x': 1, 'y': 1}})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json(), {'error': 'conflict'})

    def test_validation_errors(self):
        base = {'name': 'a', 'type': 'floor', 'unit': 'in', 'dims': {'x': 1, 'y': 1}}

        def details(**over):
            body = {**base, **over}
            r = self.call('post', '/api/spaces/', body)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json()['error'], 'validation_failed')
            return r.json()['details']

        self.assertIn('type', details(type='bogus'))
        self.assertIn('unit', details(unit='m'))
        self.assertIn('dims.x', details(dims={'x': 0, 'y': 1}))
        self.assertIn('dims.y', details(dims={'x': 1, 'y': -2}))
        self.assertIn('dims.x', details(dims={'x': True, 'y': 1}))
        self.assertIn('dims.w', details(dims={'x': 1, 'y': 1, 'w': 2}))
        self.assertIn('name', details(name='   '))
        self.assertIn('name', details(name='a' * 121))
        self.assertIn('weightLimit', details(weightLimit=True))
        self.assertIn('weightLimit', details(weightLimit=-1))
        self.assertIn('bogus', details(bogus=1))
        self.assertIn('placements', details(placements=[]))
        self.assertIn('id', details(id='bad id!'))
        self.assertEqual(Space.objects.count(), 0)

    def test_patch_id_rejected_and_invalid_json(self):
        s = self.make_space()
        r = self.call('patch', f'/api/spaces/{s["id"]}/', {'id': 'other'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('id', r.json()['details'])
        r = self.call('patch', f'/api/spaces/{s["id"]}/', {'placements': []})
        self.assertEqual(r.status_code, 400)
        r = self.call('post', '/api/spaces/', raw='{nope')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'error': 'invalid_json'})

    def test_404_405(self):
        self.assertEqual(self.call('get', '/api/spaces/nope/').status_code, 404)
        self.assertEqual(self.call('patch', '/api/spaces/nope/', {}).status_code, 404)
        self.assertEqual(self.call('delete', '/api/spaces/nope/').json(), {'error': 'not_found'})
        self.assertEqual(self.call('put', '/api/spaces/').status_code, 405)
        self.assertEqual(self.call('post', '/api/state/').status_code, 405)
        self.assertEqual(self.call('get', '/api/state/import/').status_code, 405)
        self.assertEqual(self.call('post', '/api/spaces/x/placements/y/', {}).status_code, 405)

    def test_list_order_follows_position(self):
        a = self.make_space(name='A')
        b = self.make_space(name='B')
        c = self.make_space(name='C')
        Space.objects.filter(pk=a['id']).update(position=10)
        names = [s['name'] for s in self.call('get', '/api/spaces/').json()]
        self.assertEqual(names, ['B', 'C', 'A'])
        self.assertEqual([b['id'], c['id']][0], Space.objects.first().id)


class ObjectTests(Base):
    def test_crud_and_shape(self):
        o = self.make_object()
        self.assertTrue(o['id'].startswith('obj_'))
        self.assertEqual(
            set(o),
            {'id', 'name', 'dims', 'type', 'similarItems', 'source', 'candidateIds', 'search',
             'votes', 'createdAt'},
        )
        self.assertEqual(o['source'], 'search')
        self.assertEqual(o['search'], {'status': 'idle'})

        r = self.call('patch', f'/api/objects/{o["id"]}/', {
            'weight': 40, 'material': 'wood', 'style': 'modern', 'priceLimit': 900,
            'similarItems': [' lamp ', '', 'chair'],
            'owned': {'productId': 'p1', 'orderName': '#1001', 'purchasedAt': '2024-01-01'},
            'selectedProductId': 'p9', 'candidateIds': ['p9'],
            'search': {'status': 'done', 'step': 'ranking', 'lastRunAt': '2024-01-01T00:00:00Z'},
        })
        self.assertEqual(r.status_code, 200, r.content)
        p = r.json()
        self.assertEqual(p['similarItems'], ['lamp', 'chair'])
        self.assertEqual(p['material'], 'wood')
        self.assertEqual(p['selectedProductId'], 'p9')

        p = self.call('patch', f'/api/objects/{o["id"]}/', {
            'material': None, 'style': None, 'weight': None, 'priceLimit': None,
            'owned': None, 'selectedProductId': None,
        }).json()
        for k in ('material', 'style', 'weight', 'priceLimit', 'owned', 'selectedProductId'):
            self.assertNotIn(k, p)

        self.assertEqual(self.call('delete', f'/api/objects/{o["id"]}/').status_code, 204)
        self.assertEqual(self.call('get', f'/api/objects/{o["id"]}/').status_code, 404)

    def test_price_range(self):
        o = self.make_object(priceMin=100, priceLimit=400)
        self.assertEqual((o['priceMin'], o['priceLimit']), (100, 400))
        path = f'/api/objects/{o["id"]}/'

        # A PATCH sending only one end is checked against the stored other end.
        r = self.call('patch', path, {'priceMin': 500})
        self.assertEqual(r.status_code, 400)
        self.assertIn('priceMin', r.json()['details'])
        r = self.call('patch', path, {'priceLimit': 50})
        self.assertEqual(r.status_code, 400)
        self.assertIn('priceMin', r.json()['details'])
        self.assertEqual(self.call('get', path).json()['priceLimit'], 400)

        # Moving both ends together is fine; equal ends are allowed.
        r = self.call('patch', path, {'priceMin': 600, 'priceLimit': 600})
        self.assertEqual(r.status_code, 200, r.content)
        p = self.call('patch', path, {'priceLimit': None}).json()
        self.assertEqual((p['priceMin'], p.get('priceLimit')), (600, None))
        p = self.call('patch', path, {'priceMin': None}).json()
        self.assertNotIn('priceMin', p)

        base = {'name': 'a', 'type': 'rug', 'dims': {'x': 1, 'y': 1}}
        r = self.call('post', '/api/objects/', {**base, 'priceMin': -1})
        self.assertIn('priceMin', r.json()['details'])
        r = self.call('post', '/api/objects/', {**base, 'priceMin': 200, 'priceLimit': 100})
        self.assertEqual(r.status_code, 400)
        self.assertIn('priceMin', r.json()['details'])

    def test_min_rating_round_trip(self):
        o = self.make_object(minRating=4.5)
        self.assertEqual(o['minRating'], 4.5)
        for v in (0, 5):
            p = self.call('patch', f'/api/objects/{o["id"]}/', {'minRating': v}).json()
            self.assertEqual(p['minRating'], v)
        p = self.call('patch', f'/api/objects/{o["id"]}/', {'minRating': None}).json()
        self.assertNotIn('minRating', p)
        self.assertNotIn('minRating', self.call('get', f'/api/objects/{o["id"]}/').json())

    def test_client_created_at_and_conflict(self):
        o = self.make_object(id='obj_x', createdAt='2024-03-01T10:00:00Z')
        self.assertEqual(o['createdAt'], '2024-03-01T10:00:00Z')
        r = self.call('post', '/api/objects/',
                      {'id': 'obj_x', 'name': 'a', 'type': 'rug', 'dims': {'x': 1, 'y': 1}})
        self.assertEqual(r.status_code, 409)

    def test_validation_errors(self):
        base = {'name': 'a', 'type': 'rug', 'dims': {'x': 1, 'y': 1}}

        def details(**over):
            r = self.call('post', '/api/objects/', {**base, **over})
            self.assertEqual(r.status_code, 400)
            return r.json()['details']

        self.assertIn('type', details(type='sofa'))
        self.assertIn('material', details(material='plastic'))
        self.assertIn('style', details(style=3))
        self.assertIn('weight', details(weight=True))
        self.assertIn('priceLimit', details(priceLimit=-5))
        self.assertIn('minRating', details(minRating=-1))
        self.assertIn('minRating', details(minRating=5.1))
        self.assertIn('minRating', details(minRating='4'))
        self.assertIn('minRating', details(minRating=True))
        self.assertIn('dims.x', details(dims={'x': 0, 'y': 1}))
        self.assertIn('similarItems[2]', details(similarItems=['a', 'b', 3]))
        self.assertIn('similarItems[0]', details(similarItems=['a' * 81]))
        self.assertIn('similarItems', details(similarItems=['x'] * 21))
        self.assertIn('owned.productId', details(owned={'orderName': 'a', 'purchasedAt': 'b'}))
        self.assertIn('search.status', details(search={'status': 'weird'}))
        self.assertIn('search.step', details(search={'status': 'idle', 'step': 'zzz'}))
        self.assertIn('createdAt', details(createdAt='yesterday'))
        self.assertIn('nope', details(nope=1))
        self.assertIn('selectedProductId', details(selectedProductId=4))
        self.assertEqual(SpaceObject.objects.count(), 0)

    def test_patch_unknown_and_id(self):
        o = self.make_object()
        r = self.call('patch', f'/api/objects/{o["id"]}/', {'id': 'zzz', 'foo': 1})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()['details']), {'id', 'foo'})

    def test_list_order(self):
        self.make_object(name='A')
        b = self.make_object(name='B')
        SpaceObject.objects.filter(pk=b['id']).update(position=-1)
        self.assertEqual([o['name'] for o in self.call('get', '/api/objects/').json()], ['B', 'A'])


class PatchKeepsSearchStateTests(Base):
    def test_object_patch_does_not_overwrite_search(self):
        from . import state_views
        o = self.make_object()
        real = state_views.validate_object

        def validate_then_race(*a, **kw):
            out = real(*a, **kw)
            SpaceObject.objects.filter(pk=o['id']).update(
                search={'status': 'running', 'step': 'ranking'})
            return out

        with mock.patch.object(state_views, 'validate_object', side_effect=validate_then_race):
            r = self.call('patch', f'/api/objects/{o["id"]}/', {'name': 'Renamed'})
        self.assertEqual(r.status_code, 200, r.content)
        stored = SpaceObject.objects.get(pk=o['id'])
        self.assertEqual(stored.name, 'Renamed')
        self.assertEqual(stored.search, {'status': 'running', 'step': 'ranking'})

    def test_empty_object_patch_is_a_noop(self):
        o = self.make_object()
        r = self.call('patch', f'/api/objects/{o["id"]}/', {})
        self.assertEqual(r.status_code, 200, r.content)

    def test_space_patch_saves_only_given_fields(self):
        from . import state_views
        s = self.make_space()
        real = state_views.validate_space

        def validate_then_race(*a, **kw):
            out = real(*a, **kw)
            Space.objects.filter(pk=s['id']).update(unit='in')
            return out

        with mock.patch.object(state_views, 'validate_space', side_effect=validate_then_race):
            r = self.call('patch', f'/api/spaces/{s["id"]}/', {'name': 'Den'})
        self.assertEqual(r.status_code, 200, r.content)
        stored = Space.objects.get(pk=s['id'])
        self.assertEqual((stored.name, stored.unit), ('Den', 'in'))


class PlacementTests(Base):
    def test_put_move_delete(self):
        s = self.make_space()
        o = self.make_object()
        url = f'/api/spaces/{s["id"]}/placements/{o["id"]}/'
        r = self.call('put', url, {'x': 5, 'y': 6})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual(j['placements'], [{'objectId': o['id'], 'x': 5, 'y': 6}])
        self.assertGreater(j['updatedAt'], s['updatedAt'])

        r = self.call('put', url, {'x': 7.5, 'y': 0})
        j2 = r.json()
        self.assertEqual(j2['placements'], [{'objectId': o['id'], 'x': 7.5, 'y': 0}])
        self.assertGreater(j2['updatedAt'], j['updatedAt'])
        self.assertEqual(Placement.objects.count(), 1)

        r = self.call('delete', url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['placements'], [])
        self.assertGreater(r.json()['updatedAt'], j2['updatedAt'])
        self.assertEqual(self.call('delete', url).status_code, 404)

    def test_errors(self):
        s = self.make_space()
        o = self.make_object()
        url = f'/api/spaces/{s["id"]}/placements/{o["id"]}/'
        self.assertEqual(self.call('put', url, {'x': True, 'y': 1}).status_code, 400)
        r = self.call('put', url, {'x': 1})
        self.assertIn('y', r.json()['details'])
        self.assertEqual(self.call('put', f'/api/spaces/{s["id"]}/placements/nope/', {'x': 1, 'y': 1}).status_code, 404)
        self.assertEqual(self.call('put', f'/api/spaces/nope/placements/{o["id"]}/', {'x': 1, 'y': 1}).status_code, 404)
        self.assertEqual(self.call('get', url).status_code, 405)

    def test_cascades(self):
        s = self.make_space()
        o = self.make_object()
        self.call('put', f'/api/spaces/{s["id"]}/placements/{o["id"]}/', {'x': 1, 'y': 1})
        self.call('delete', f'/api/objects/{o["id"]}/')
        self.assertEqual(Placement.objects.count(), 0)
        self.assertEqual(self.call('get', f'/api/spaces/{s["id"]}/').json()['placements'], [])

        o2 = self.make_object(name='Lamp')
        self.call('put', f'/api/spaces/{s["id"]}/placements/{o2["id"]}/', {'x': 1, 'y': 1})
        self.call('delete', f'/api/spaces/{s["id"]}/')
        self.assertEqual(Placement.objects.count(), 0)
        self.assertTrue(SpaceObject.objects.filter(pk=o2['id']).exists())


def app_state():
    return {
        'version': 1,
        'spaces': [
            {'id': 'space_b', 'name': 'Bedroom', 'type': 'floor', 'unit': 'ft',
             'dims': {'x': 144, 'y': 120, 'z': 96}, 'weightLimit': 500,
             'placements': [{'objectId': 'obj_sofa', 'x': 10, 'y': 12.5},
                            {'objectId': 'obj_lamp', 'x': 0, 'y': 0}],
             'createdAt': '2024-05-01T10:00:00.000Z', 'updatedAt': '2024-05-02T11:30:00.000Z'},
            {'id': 'space_a', 'name': 'Shelf', 'type': 'shelf', 'unit': 'cm',
             'dims': {'x': 30, 'y': 10}, 'placements': [],
             'createdAt': '2024-04-01T10:00:00.000Z', 'updatedAt': '2024-04-01T10:00:00.000Z'},
        ],
        'objects': [
            {'id': 'obj_sofa', 'name': 'Sofa', 'dims': {'x': 80, 'y': 36, 'z': 30}, 'weight': 120,
             'type': 'furniture', 'material': 'fabric', 'style': 'modern', 'priceLimit': 1500, 'minRating': 4,
             'similarItems': ['loveseat'], 'source': 'search', 'candidateIds': ['p1', 'p2'],
             'selectedProductId': 'p1',
             'search': {'status': 'done', 'step': 'ranking', 'lastRunAt': '2024-05-01T12:00:00.000Z'},
             'createdAt': '2024-05-01T09:00:00.000Z'},
            {'id': 'obj_lamp', 'name': 'Lamp', 'dims': {'x': 10, 'y': 10}, 'type': 'lighting',
             'similarItems': [], 'source': 'search', 'candidateIds': [],
             'search': {'status': 'idle'}, 'createdAt': '2024-05-01T09:30:00.000Z'},
            {'id': 'obj_vase', 'name': 'Vase', 'dims': {'x': 4, 'y': 4, 'z': 9}, 'type': 'decor',
             'material': 'ceramic', 'similarItems': [], 'source': 'imported',
             'owned': {'productId': 'p77', 'orderName': '#1042', 'purchasedAt': '2024-02-02'},
             'candidateIds': [], 'search': {'status': 'idle'},
             'createdAt': '2024-02-03T00:00:00.000Z'},
        ],
        'cart': [{'id': 'c1', 'productId': 'p1', 'objectId': 'obj_sofa', 'qty': 1}],
        'checkoutOpened': {'obj_sofa': '2024-05-03T00:00:00Z'},
        'importedAt': '2024-05-04T00:00:00Z',
    }


class ImportTests(Base):
    def snapshot(self):
        return self.call('get', '/api/state/').json()

    def test_import_roundtrip_idempotent(self):
        payload = app_state()
        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['imported'], {'spaces': 2, 'objects': 3, 'placements': 2})
        self.assertEqual(sorted(r.json()['ignored']), ['cart', 'checkoutOpened', 'importedAt'])
        first = self.snapshot()
        self.assertEqual([s['id'] for s in first['spaces']], ['space_b', 'space_a'])
        self.assertEqual([o['id'] for o in first['objects']], ['obj_sofa', 'obj_lamp', 'obj_vase'])
        sp = first['spaces'][0]
        self.assertEqual(sp['createdAt'], '2024-05-01T10:00:00Z')
        self.assertEqual(sp['updatedAt'], '2024-05-02T11:30:00Z')
        self.assertEqual(sp['dims'], {'x': 144, 'y': 120, 'z': 96})
        self.assertEqual(sp['weightLimit'], 500)
        self.assertEqual([p['objectId'] for p in sp['placements']], ['obj_sofa', 'obj_lamp'])
        vase = first['objects'][2]
        self.assertEqual(vase['source'], 'imported')
        self.assertEqual(vase['owned']['orderName'], '#1042')
        self.assertEqual(vase['createdAt'], '2024-02-03T00:00:00Z')
        sofa = first['objects'][0]
        self.assertEqual(sofa['selectedProductId'], 'p1')
        self.assertEqual(sofa['search']['step'], 'ranking')
        self.assertNotIn('weight', first['objects'][1])

        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(Placement.objects.count(), 2)

    def test_reimport_replaces_placements_and_keeps_others(self):
        self.call('post', '/api/state/import/', app_state())
        extra = self.make_object(name='Extra')
        payload = app_state()
        payload['spaces'][0]['placements'] = [{'objectId': extra['id'], 'x': 1, 'y': 2}]
        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 200, r.content)
        sp = self.call('get', '/api/spaces/space_b/').json()
        self.assertEqual(sp['placements'], [{'objectId': extra['id'], 'x': 1, 'y': 2}])
        self.assertTrue(SpaceObject.objects.filter(pk=extra['id']).exists())

    def test_bad_object_writes_nothing(self):
        payload = app_state()
        payload['objects'][1]['dims'] = {'x': 0, 'y': 1}
        payload['objects'].append({**payload['objects'][0], 'id': 'obj_z', 'type': 'nope'})
        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 400)
        d = r.json()['details']
        self.assertIn('objects[1].dims.x', d)
        self.assertIn('objects[3].type', d)
        self.assertEqual(Space.objects.count(), 0)
        self.assertEqual(SpaceObject.objects.count(), 0)

    def test_other_errors(self):
        payload = app_state()
        payload['version'] = 2
        del payload['objects']
        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()['details']), {'version', 'objects'})

        payload = app_state()
        payload['spaces'][1]['placements'] = [{'objectId': 'ghost', 'x': 1, 'y': 1}]
        r = self.call('post', '/api/state/import/', payload)
        self.assertEqual(r.status_code, 400)
        self.assertIn('spaces[1].placements[0].objectId', r.json()['details'])
        self.assertEqual(Space.objects.count(), 0)

        payload = app_state()
        payload['spaces'][0]['placements'][0]['x'] = True
        r = self.call('post', '/api/state/import/', payload)
        self.assertIn('spaces[0].placements[0].x', r.json()['details'])

        r = self.call('post', '/api/state/import/', raw='[')
        self.assertEqual(r.json(), {'error': 'invalid_json'})

    def test_overview_lists_new_routes(self):
        paths = [e['path'] for e in self.client.get('/api/overview/').json()['endpoints']]
        for p in ('/api/state/', '/api/state/import/', '/api/spaces/', '/api/objects/<id>/',
                  '/api/spaces/<id>/placements/<object_id>/'):
            self.assertIn(p, paths)

    def test_reimport_resets_omitted_optional_fields(self):
        payload = app_state()
        self.call('post', '/api/state/import/', payload)
        first = self.snapshot()
        self.assertEqual(first['objects'][0]['material'], 'fabric')
        for k in ('weight', 'material', 'style', 'priceLimit', 'minRating', 'selectedProductId'):
            del payload['objects'][0][k]
        payload['objects'][0].update(candidateIds=[], similarItems=[], search={'status': 'idle'})
        del payload['spaces'][0]['weightLimit']
        self.assertEqual(self.call('post', '/api/state/import/', payload).status_code, 200)
        snap = self.snapshot()
        for k in ('weight', 'material', 'style', 'priceLimit', 'minRating', 'selectedProductId'):
            self.assertNotIn(k, snap['objects'][0])
        self.assertNotIn('weightLimit', snap['spaces'][0])

        # Fields omitted entirely (including defaulted ones) also reset.
        self.call('post', '/api/state/import/', app_state())
        minimal = {'id': 'obj_sofa', 'name': 'Sofa', 'dims': {'x': 1, 'y': 1}, 'type': 'rug',
                   'createdAt': '2024-05-01T09:00:00Z'}
        self.call('post', '/api/state/import/',
                  {'version': 1, 'spaces': [], 'objects': [minimal]})
        o = self.call('get', '/api/objects/obj_sofa/').json()
        self.assertEqual(o['candidateIds'], [])
        self.assertEqual(o['similarItems'], [])
        self.assertEqual(o['search'], {'status': 'idle'})
        self.assertEqual(o['source'], 'search')


class QueryCountTests(Base):
    def test_list_endpoints_constant_queries(self):
        for n in (1, 6):
            for _ in range(n if n == 1 else 5):
                s = self.make_space()
                o = self.make_object()
                self.call('put', f'/api/spaces/{s["id"]}/placements/{o["id"]}/', {'x': 1, 'y': 1})
            # spaces, placements, objects, runs with accepted candidates
            # (the candidate query is skipped when there are none), cart
            # lines, merchant carts
            with self.assertNumQueries(7):
                r = self.client.get('/api/state/')
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()['candidates'], [])
            with self.assertNumQueries(2):
                self.assertEqual(self.client.get('/api/spaces/').status_code, 200)


class FuzzyPriceTests(Base):
    def test_fuzzy_flags(self):
        o = self.make_object(priceMin=100, priceLimit=400, priceLimitFuzzy=True)
        self.assertTrue(o['priceLimitFuzzy'])
        self.assertNotIn('priceMinFuzzy', o)
        path = f'/api/objects/{o["id"]}/'
        p = self.call('patch', path, {'priceMinFuzzy': True}).json()
        self.assertTrue(p['priceMinFuzzy'] and p['priceLimitFuzzy'])
        p = self.call('patch', path, {'priceLimitFuzzy': False}).json()
        self.assertNotIn('priceLimitFuzzy', p)
        self.assertTrue(p['priceMinFuzzy'])

    def test_non_bool_rejected(self):
        o = self.make_object(priceLimit=400)
        r = self.call('patch', f'/api/objects/{o["id"]}/', {'priceLimitFuzzy': 'yes'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('priceLimitFuzzy', r.json()['details'])

    def test_coerced_when_price_cleared(self):
        o = self.make_object(priceLimit=400, priceLimitFuzzy=True)
        path = f'/api/objects/{o["id"]}/'
        p = self.call('patch', path, {'priceLimit': None}).json()
        self.assertNotIn('priceLimit', p)
        self.assertNotIn('priceLimitFuzzy', p)
        # fuzzy without any price on create is coerced too
        o2 = self.make_object(priceMinFuzzy=True)
        self.assertNotIn('priceMinFuzzy', o2)
