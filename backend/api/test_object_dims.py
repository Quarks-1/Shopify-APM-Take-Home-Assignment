"""Object dims with an optional y (depth): validation, filters and intent."""
import json

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from pathlib import Path

from .models import SpaceObject
from .searcher import engine
from .searcher import query_builder as qb
from .searcher.filters import evaluate

TOL = 1.15
FAKE_UCP = str(Path(__file__).parent / 'test_fixtures' / 'fake_ucp.py')


class DimsValidationTests(TestCase):
    def post(self, path, body):
        return self.client.post(path, data=json.dumps(body), content_type='application/json')

    def obj(self, dims, **extra):
        return self.post('/api/objects/', {'name': 'Mirror', 'type': 'decor', 'dims': dims, **extra})

    def test_object_y_optional(self):
        r = self.obj({'x': 24})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['dims'], {'x': 24})
        self.assertEqual(SpaceObject.objects.get(pk=r.json()['id']).dims, {'x': 24})
        self.assertEqual(self.obj({'x': 24, 'y': None}).json()['dims'], {'x': 24})
        self.assertEqual(self.obj({'x': 24, 'y': None, 'z': 36}).json()['dims'], {'x': 24, 'z': 36})
        self.assertEqual(self.obj({'x': 24, 'y': 2, 'z': 36}).json()['dims'], {'x': 24, 'y': 2, 'z': 36})

    def test_object_dims_still_validated(self):
        for dims, key in [({'y': 3}, 'dims.x'), ({'x': 24, 'y': 0}, 'dims.y'),
                          ({'x': 24, 'y': 'deep'}, 'dims.y'), ({'x': 24, 'w': 1}, 'dims.w')]:
            r = self.obj(dims)
            self.assertEqual(r.status_code, 400)
            self.assertIn(key, r.json()['details'])

    def test_patch_drops_y(self):
        oid = self.obj({'x': 24, 'y': 2}).json()['id']
        r = self.client.patch(f'/api/objects/{oid}/', data=json.dumps({'dims': {'x': 30, 'z': 40}}),
                              content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['dims'], {'x': 30, 'z': 40})

    def test_space_still_requires_y(self):
        base = {'name': 'Wall', 'type': 'wall', 'unit': 'in'}
        for dims in ({'x': 100}, {'x': 100, 'y': None}):
            r = self.post('/api/spaces/', {**base, 'dims': dims})
            self.assertEqual(r.status_code, 400)
            self.assertIn('dims.y', r.json()['details'])

    def test_import_follows_the_same_rules(self):
        state = {
            'version': 1,
            'spaces': [{'id': 's1', 'name': 'Wall', 'type': 'wall', 'unit': 'in',
                        'dims': {'x': 100, 'y': 90}, 'placements': [],
                        'createdAt': '2024-01-01T00:00:00Z', 'updatedAt': '2024-01-01T00:00:00Z'}],
            'objects': [{'id': 'o1', 'name': 'Poster', 'type': 'wall-art', 'dims': {'x': 24, 'z': 36},
                         'similarItems': [], 'createdAt': '2024-01-01T00:00:00Z'}],
        }
        r = self.post('/api/state/import/', state)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.client.get('/api/objects/o1/').json()['dims'], {'x': 24, 'z': 36})
        state['spaces'][0]['dims'] = {'x': 100}
        r = self.post('/api/state/import/', state)
        self.assertEqual(r.status_code, 400)
        self.assertIn('spaces[0].dims.y', r.json()['details'])


def prod(**kw):
    base = {'price_cents': 5000, 'currency': 'USD', 'dims': None, 'dims_longest': None,
            'weight_lb': None}
    base.update(kw)
    return base


class OptionalDepthFilterTests(SimpleTestCase):
    def test_floor_object_without_y_uses_square_footprint(self):
        snap = {'type': 'furniture', 'dims': {'x': 24}}
        self.assertEqual(evaluate(prod(dims={'x': 24, 'y': 24}), snap, TOL)[2], 'fits')
        passes, reasons, size, _ = evaluate(prod(dims={'x': 24, 'y': 30}), snap, TOL)
        self.assertEqual((passes, size), (False, 'too_big'))
        self.assertEqual(reasons, ['too large: 24 x 30 in footprint exceeds 24 x 24 in (+15%)'])
        # height still checked when the object has z
        snap = {'type': 'furniture', 'dims': {'x': 24, 'z': 30}}
        self.assertEqual(evaluate(prod(dims={'x': 20, 'y': 20, 'z': 30}), snap, TOL)[2], 'fits')
        self.assertFalse(evaluate(prod(dims={'x': 20, 'y': 20, 'z': 40}), snap, TOL)[0])

    def test_wall_object_height_is_z_then_y_then_x(self):
        with_y = {'type': 'wall-art', 'dims': {'x': 24, 'y': 36}}
        self.assertEqual(evaluate(prod(dims={'x': 24, 'y': 36}), with_y, TOL)[2], 'fits')
        passes, reasons, size, _ = evaluate(prod(dims={'x': 30, 'y': 45}), with_y, TOL)
        self.assertEqual((passes, size), (False, 'too_big'))
        self.assertIn('face exceeds 24 x 36 in', reasons[0])

        x_only = {'type': 'wall-art', 'dims': {'x': 24}}
        self.assertEqual(evaluate(prod(dims={'x': 24, 'z': 24}), x_only, TOL)[2], 'fits')
        self.assertFalse(evaluate(prod(dims={'x': 24, 'y': 36}), x_only, TOL)[0])
        self.assertFalse(evaluate(prod(dims_longest=40), x_only, TOL)[0])

        z_wins = {'type': 'decor', 'dims': {'x': 20, 'y': 2, 'z': 30},
                  'space': {'type': 'wall', 'dims': {'x': 100, 'y': 100}}}
        self.assertEqual(evaluate(prod(dims={'x': 20, 'y': 30}), z_wins, TOL)[2], 'fits')


class OptionalDepthIntentTests(SimpleTestCase):
    def test_floor_object_without_depth(self):
        self.assertEqual(
            qb.build_intent({'name': 'Floor lamp', 'type': 'lighting', 'dims': {'x': 24, 'z': 36}}),
            'Looking for a floor lamp (lighting), fitting within 24 W x 36 H inches.')
        self.assertEqual(
            qb.build_intent({'name': 'Plant', 'type': 'decor', 'dims': {'x': 24}}),
            'Looking for a plant (decor), fitting within 24 W inches.')

    def test_wall_object_without_z_uses_y_as_height(self):
        s = {'name': 'Poster', 'type': 'wall-art', 'dims': {'x': 24, 'y': 30}}
        self.assertIn('fitting within 24 W x 30 H inches', qb.build_intent(s))
        s = {'name': 'Poster', 'type': 'wall-art', 'dims': {'x': 24}}
        self.assertIn('fitting within 24 W inches', qb.build_intent(s))

    def test_unchanged_with_full_dims(self):
        s = {'name': 'Desk', 'type': 'furniture', 'dims': {'x': 46, 'y': 24, 'z': 30}}
        self.assertIn('fitting within 46 W x 24 D x 30 H inches', qb.build_intent(s))


@override_settings(SEARCHER_UCP_BIN=FAKE_UCP)
class OptionalDepthEngineTests(TestCase):
    def test_round_for_object_without_depth(self):
        obj = SpaceObject.objects.create(
            id='o', name='Coffee table', dims={'x': 46, 'z': 17}, type='furniture',
            price_limit=400, created_at=timezone.now())
        run = engine.create_run(obj)
        self.assertEqual(run.object_snapshot['dims'], {'x': 46, 'z': 17})
        out = engine.next_round(run)
        self.assertEqual(out['returned'], 5)
        self.assertEqual(engine.status(run)['object']['dims'], {'x': 46, 'z': 17})
