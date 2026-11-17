from django.test import SimpleTestCase
from types import SimpleNamespace

from .serializers import candidate_sort_key, seller_group_key, sort_accepted_brand_diverse


def cand(pk, domain='', name='', price=100, size='fits'):
    return SimpleNamespace(
        pk=pk,
        seller_domain=domain,
        seller_name=name,
        price_cents=price,
        size_status=size,
    )


class BrandDiverseSortTests(SimpleTestCase):
    def test_round_robin_interleaves_sellers(self):
        items = [
            cand(1, 'a.shop', price=200),
            cand(2, 'a.shop', price=150),
            cand(3, 'b.shop', price=180),
            cand(4, 'c.shop', price=170),
        ]
        ordered = sort_accepted_brand_diverse(items)
        # One per seller first, sellers ranked by their best candidate
        # (a: $150, c: $170, b: $180), then a's second candidate.
        self.assertEqual([c.pk for c in ordered], [2, 4, 3, 1])
        self.assertEqual(
            [seller_group_key(c) for c in ordered[:3]],
            ['a.shop', 'c.shop', 'b.shop'],
        )

    def test_within_brand_fits_and_price(self):
        a = cand(1, 'x.shop', price=300)
        b = cand(2, 'x.shop', price=100)
        ordered = sort_accepted_brand_diverse([a, b])
        self.assertEqual([c.pk for c in ordered], [2, 1])
        self.assertEqual(candidate_sort_key(b), (0, 100, 2))

def titled(pk, title, description='', **kw):
    c = cand(pk, **kw)
    c.title = title
    c.description = description
    return c


THEME = {'family': 'animal print', 'patterns': ['leopard print', 'zebra print'],
         'pins': 5, 'boardPins': 9}


class ThemeSortTests(SimpleTestCase):
    def items(self):
        return [
            titled(1, 'Plain Chair', domain='a.shop', price=9900),
            titled(2, 'Zebra Print Chair', domain='b.shop', price=57000),
            titled(3, 'Velvet Leopard Pattern Chair', domain='c.shop', price=69900),
        ]

    def test_no_theme_unchanged(self):
        self.assertEqual([c.pk for c in sort_accepted_brand_diverse(self.items())], [1, 2, 3])
        self.assertEqual([c.pk for c in sort_accepted_brand_diverse(self.items(), None)], [1, 2, 3])

    def test_theme_carriers_first(self):
        ordered = sort_accepted_brand_diverse(self.items(), THEME)
        self.assertEqual([c.pk for c in ordered], [2, 3, 1])

    def test_theme_in_description_only(self):
        items = self.items()
        items[2] = titled(3, 'Velvet Chair', 'Bold cheetah spots', domain='c.shop', price=69900)
        items[1] = titled(2, 'Chair', 'zebra stripes on the seat', domain='b.shop', price=57000)
        # cheetah print is in the animal family
        self.assertEqual([c.pk for c in sort_accepted_brand_diverse(items, THEME)], [2, 3, 1])

    def test_seller_diversity_within_groups(self):
        items = [
            titled(1, 'Zebra Chair', domain='a.shop', price=500),
            titled(2, 'Zebra Stool', domain='a.shop', price=400),
            titled(3, 'Leopard Chair', domain='b.shop', price=450),
            titled(4, 'Plain A', domain='a.shop', price=100),
            titled(5, 'Plain B', domain='b.shop', price=200),
        ]
        ordered = sort_accepted_brand_diverse(items, THEME)
        # Themed: a (best 400), b (450), then a's second; then plain: a, b.
        self.assertEqual([c.pk for c in ordered], [2, 3, 1, 4, 5])

    def test_key_has_flag_only_with_theme(self):
        c = titled(2, 'Zebra Chair', price=100)
        self.assertEqual(candidate_sort_key(c), (0, 100, 2))
        self.assertEqual(candidate_sort_key(c, THEME), (0, 0, 100, 2))
        self.assertEqual(candidate_sort_key(titled(3, 'Plain', price=100), THEME), (1, 0, 100, 3))

    def test_non_dict_theme_ignored(self):
        self.assertEqual([c.pk for c in sort_accepted_brand_diverse(self.items(), 'oops')], [1, 2, 3])
