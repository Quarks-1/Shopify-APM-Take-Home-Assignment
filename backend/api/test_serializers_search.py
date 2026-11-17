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