from django.test import SimpleTestCase

from .searcher.filters import evaluate

TOL = 1.15
OBJ = {'dims': {'x': 46, 'y': 24, 'z': 17}, 'priceLimit': 400, 'weight': 40}


def prod(**kw):
    base = {'price_cents': 15000, 'currency': 'USD', 'dims': None, 'dims_longest': None,
            'weight_lb': None}
    base.update(kw)
    return base


class FilterTests(SimpleTestCase):
    def ev(self, fields, snapshot=OBJ):
        return evaluate(fields, snapshot, TOL)

    def test_price(self):
        self.assertTrue(self.ev(prod(price_cents=40000))[0])
        passes, reasons, *_ = self.ev(prod(price_cents=45000))
        self.assertFalse(passes)
        self.assertEqual(reasons, ['over price limit: $450.00 > $400.00'])
        # no limit -> any USD price passes
        self.assertTrue(evaluate(prod(price_cents=10**7), {'dims': {'x': 1, 'y': 1}}, TOL)[0])

    def test_currency(self):
        passes, reasons, *_ = self.ev(prod(currency='EUR'))
        self.assertFalse(passes)
        self.assertIn('not USD', reasons[0])

    def test_fits_and_rotation(self):
        passes, reasons, size, _ = self.ev(prod(dims={'x': 24, 'y': 46, 'z': 17}))
        self.assertEqual((passes, reasons, size), (True, [], 'fits'))
        self.assertEqual(self.ev(prod(dims={'x': 52, 'y': 27, 'z': 19}))[2], 'fits')  # within +15%

    def test_too_wide(self):
        passes, reasons, size, _ = self.ev(prod(dims={'x': 52, 'y': 28, 'z': 10}))
        self.assertEqual((passes, size), (False, 'too_big'))
        self.assertEqual(
            reasons, ['too large: 52 x 28 in footprint exceeds 46 x 24 in (+15%)'])
        # a square 31.5 footprint is too deep once sorted
        self.assertEqual(self.ev(prod(dims={'x': 31.5, 'y': 31.5}))[2], 'too_big')

    def test_too_tall(self):
        passes, reasons, size, _ = self.ev(prod(dims={'x': 40, 'y': 20, 'z': 25}))
        self.assertEqual((passes, size), (False, 'too_big'))
        self.assertTrue(reasons[0].startswith('too tall: 25 in height exceeds 17 in'))

    def test_single_axis(self):
        self.assertEqual(self.ev(prod(dims={'x': 47}))[2], 'unverified')
        passes, reasons, size, _ = self.ev(prod(dims={'x': 47}))
        self.assertFalse(passes)
        self.assertIn('size unverified', reasons)
        passes, _, size, _ = self.ev(prod(dims={'y': 60}))
        self.assertEqual((passes, size), (False, 'too_big'))

    def test_longest(self):
        passes, reasons, size, _ = self.ev(prod(dims_longest=48))
        self.assertEqual((passes, size), (False, 'unverified'))
        self.assertIn('size unverified', reasons)
        self.assertTrue(self.ev(prod(dims={'x': 40, 'y': 20, 'z': 15}))[0])
        passes, reasons, size, _ = self.ev(prod(dims_longest=60))
        self.assertEqual((passes, size), (False, 'too_big'))
        self.assertIn('longest side', reasons[0])

    def test_unverified_fails(self):
        passes, reasons, size, weight = self.ev(prod())
        self.assertEqual((passes, size, weight), (False, 'unverified', 'unverified'))
        self.assertIn('size unverified', reasons)

    def test_fits_requires_z_when_object_has_z(self):
        passes, reasons, size, _ = self.ev(prod(dims={'x': 40, 'y': 20}))
        self.assertEqual((passes, size), (False, 'unverified'))
        self.assertIn('size unverified', reasons)
        flat = {'dims': {'x': 46, 'y': 24}}
        self.assertEqual(evaluate(prod(dims={'x': 40, 'y': 20}), flat, TOL)[2], 'fits')
        # z only compared when both sides have it
        self.assertTrue(evaluate(prod(dims={'x': 40, 'y': 20, 'z': 99}), flat, TOL)[0])

    def test_weight(self):
        self.assertEqual(self.ev(prod(weight_lb=30))[3], 'fits')
        self.assertEqual(self.ev(prod(weight_lb=45))[3], 'fits')  # within tolerance
        passes, reasons, _, weight = self.ev(prod(weight_lb=56))
        self.assertEqual((passes, weight), (False, 'too_heavy'))
        self.assertEqual(reasons, ['too heavy: 56 lb exceeds 40 lb (+15%)'])
        self.assertEqual(self.ev(prod())[3], 'unverified')
        no_weight = {'dims': {'x': 46, 'y': 24}}
        self.assertEqual(evaluate(prod(weight_lb=500), no_weight, TOL)[3], 'unverified')

    def test_multiple_reasons(self):
        passes, reasons, *_ = self.ev(prod(price_cents=90000, weight_lb=99,
                                           dims={'x': 90, 'y': 40}))
        self.assertFalse(passes)
        self.assertEqual(len(reasons), 3)


class WallModeTests(SimpleTestCase):
    POSTER = {'type': 'wall-art', 'dims': {'x': 24, 'y': 1, 'z': 36}, 'priceLimit': 120}

    def ev(self, dims=None, snapshot=None, **kw):
        return evaluate(prod(price_cents=5000, dims=dims, **kw), snapshot or self.POSTER, TOL)

    def test_poster_fits(self):
        passes, reasons, size, _ = self.ev({'x': 24, 'y': 36})
        self.assertEqual((passes, reasons, size), (True, [], 'fits'))

    def test_too_big_poster(self):
        passes, reasons, size, _ = self.ev({'x': 30, 'y': 40})
        self.assertEqual((passes, size), (False, 'too_big'))
        self.assertEqual(reasons, ['too large: 30 x 40 in face exceeds 24 x 36 in (+15%)'])

    def test_small_poster_and_labeled_wh(self):
        self.assertEqual(self.ev({'x': 11, 'y': 17})[2], 'fits')
        self.assertEqual(self.ev({'x': 24, 'z': 36})[2], 'fits')
        self.assertEqual(self.ev({'x': 36, 'y': 24})[2], 'fits')  # rotated

    def test_depth_not_checked(self):
        passes, _, size, _ = self.ev({'x': 24, 'y': 1.5, 'z': 36})
        self.assertEqual((passes, size), (True, 'fits'))
        self.assertEqual(self.ev({'x': 24, 'y': 6, 'z': 36})[2], 'fits')

    def test_single_and_longest(self):
        passes, reasons, size, _ = self.ev({'x': 30})
        self.assertEqual((passes, size), (False, 'unverified'))
        self.assertIn('size unverified', reasons)
        self.assertFalse(self.ev({'x': 50})[0])
        passes, reasons, size, _ = self.ev(None, dims_longest=40)
        self.assertEqual((passes, size), (False, 'unverified'))
        self.assertIn('size unverified', reasons)
        self.assertFalse(self.ev(None, dims_longest=50)[0])
        passes, reasons, size, _ = self.ev(None)
        self.assertEqual((passes, size), (False, 'unverified'))
        self.assertIn('size unverified', reasons)

    def test_wall_art_in_floor_space_uses_wall_mode(self):
        snap = dict(self.POSTER, space={'type': 'floor', 'dims': {'x': 100, 'y': 100}})
        self.assertEqual(self.ev({'x': 24, 'y': 36}, snap)[2], 'fits')

    def test_non_wall_art_in_wall_space_uses_wall_mode(self):
        snap = {'type': 'decor', 'dims': {'x': 24, 'y': 1, 'z': 36},
                'space': {'type': 'wall', 'dims': {'x': 100, 'y': 100}}}
        self.assertEqual(self.ev({'x': 24, 'y': 36}, snap)[2], 'fits')

    def test_floor_behavior_unchanged(self):
        snap = {'type': 'decor', 'dims': {'x': 24, 'y': 1, 'z': 36}}
        self.assertEqual(self.ev({'x': 24, 'y': 36}, snap)[2], 'too_big')


class PriceUnknownTests(SimpleTestCase):
    def test_zero_price_fails(self):
        passes, reasons, *_ = evaluate(prod(price_cents=0), OBJ, TOL)
        self.assertFalse(passes)
        self.assertIn('price unknown', reasons)
        self.assertFalse(evaluate(prod(price_cents=0), {'dims': {'x': 1, 'y': 1}}, TOL)[0])
