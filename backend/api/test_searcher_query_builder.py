from django.test import SimpleTestCase, override_settings

from .searcher import query_builder as qb

SNAP = {
    'id': 'o1', 'name': 'Coffee table', 'dims': {'x': 46, 'y': 24, 'z': 17}, 'weight': 40,
    'type': 'furniture', 'material': 'wood', 'style': 'mid-century', 'priceLimit': 400,
    'similarItems': ['cocktail table', 'ottoman'], 'space': {'type': 'floor', 'dims': {'x': 1, 'y': 1}},
}


def snap(**kw):
    s = dict(SNAP)
    s.update(kw)
    return {k: v for k, v in s.items() if v is not None}


def prev(strategy, query, passing=0, cursor=None, has_next=False, used_cursor=None):
    payload = {'query': query, 'pagination': {'limit': 20}}
    if used_cursor:
        payload['pagination']['cursor'] = used_cursor
    return {'strategy': strategy, 'query': query, 'passing_count': passing, 'payload': payload,
            'response_meta': {'cursor': cursor, 'has_next_page': has_next}}


class StrategyListTests(SimpleTestCase):
    def test_ladder_order(self):
        self.assertEqual(qb.strategies(SNAP, [], []),
                         ['exact', 'relax-style', 'name', 'similar:0', 'similar:1', 'broad'])

    def test_query_strings(self):
        q = lambda s, snapshot=SNAP: qb.build_payload(snapshot, s)['query']
        self.assertEqual(q('exact'), 'mid-century wood Coffee table')
        self.assertEqual(q('relax-style'), 'wood Coffee table')
        self.assertEqual(q('name'), 'Coffee table')
        self.assertEqual(q('similar:0'), 'mid-century cocktail table')
        self.assertEqual(q('similar:1'), 'mid-century ottoman')
        self.assertEqual(q('broad'), 'mid-century wood table')  # furniture -> last word of name

    def test_missing_parts_omitted(self):
        s = snap(style=None, material=None)
        self.assertEqual(qb.build_payload(s, 'exact')['query'], 'Coffee table')
        self.assertEqual(qb.build_payload(snap(material='mixed'), 'exact')['query'],
                         'mid-century Coffee table')
        # identical queries collapse to the first strategy
        self.assertEqual(qb.strategies(s, [], [])[0], 'exact')
        self.assertNotIn('name', qb.strategies(s, [], []))
        self.assertNotIn('relax-style', qb.strategies(s, [], []))

    def test_broad_type_nouns(self):
        for t, noun in [('lighting', 'lamp'), ('rug', 'area rug'), ('wall-art', 'wall art'),
                        ('storage', 'storage'), ('decor', 'home decor')]:
            with self.subTest(t=t):
                self.assertEqual(qb.build_payload(snap(type=t, style=None, material=None),
                                                  'broad')['query'], noun)

    def test_used_queries_dropped_case_and_space_insensitive(self):
        used = ['  MID-CENTURY  wood coffee TABLE ', 'cocktail']
        got = qb.strategies(SNAP, used, [])
        self.assertNotIn('exact', got)
        self.assertEqual(got[:2], ['relax-style', 'name'])
        self.assertEqual(qb.strategies(SNAP, [qb.build_payload(SNAP, s)['query']
                                              for s in qb.strategies(SNAP, [], [])], []), [])

    def test_more_offered_only_with_next_page(self):
        self.assertNotIn('more', qb.strategies(SNAP, [], [prev('exact', 'q', 3)]))
        self.assertNotIn('more', qb.strategies(SNAP, [], [prev('exact', 'q', 3, cursor='c', has_next=False)]))
        self.assertNotIn('more', qb.strategies(SNAP, [], [prev('exact', 'q', 3, cursor=None, has_next=True)]))
        got = qb.strategies(SNAP, ['q'], [prev('exact', 'q', 3, cursor='c', has_next=True)])
        self.assertEqual(got[-1], 'more')

    def test_more_survives_used_query(self):
        # the `more` query string equals an already-used query; it must still be offered
        used = [qb.build_payload(SNAP, s)['query'] for s in ['exact', 'relax-style', 'name', 'broad']]
        got = qb.strategies(SNAP, used, [prev('exact', used[0], 2, 'cur1', True)])
        self.assertIn('more', got)


class MoreTests(SimpleTestCase):
    def test_picks_most_passing_with_next_page(self):
        qs = [prev('exact', 'a', 2, 'c-a', True), prev('name', 'b', 7, 'c-b', True),
              prev('broad', 'c', 9, None, False), prev('similar:0', 'd', 8, 'c-d', False)]
        p = qb.build_payload(SNAP, 'more', qs)
        self.assertEqual(p['query'], 'b')
        self.assertEqual(p['pagination']['cursor'], 'c-b')
        self.assertEqual(p['filters']['price'], {'max': 40000})

    def test_cursor_already_consumed_is_skipped(self):
        qs = [prev('exact', 'a', 5, 'c-a', True), prev('name', 'b', 1, 'c-b', True),
              prev('more', 'a', 0, None, False, used_cursor='c-a')]
        p = qb.build_payload(SNAP, 'more', qs)
        self.assertEqual((p['query'], p['pagination']['cursor']), ('b', 'c-b'))

    def test_chain_of_pages(self):
        qs = [prev('exact', 'a', 5, 'c-a', True),
              prev('more', 'a', 3, 'c-a2', True, used_cursor='c-a')]
        self.assertEqual(qb.build_payload(SNAP, 'more', qs)['pagination']['cursor'], 'c-a2')

    def test_no_source_is_error(self):
        with self.assertRaises(ValueError):
            qb.build_payload(SNAP, 'more', [])

    def test_unknown_strategy(self):
        with self.assertRaises(ValueError):
            qb.build_payload(SNAP, 'similar:9')
        with self.assertRaises(ValueError):
            qb.build_payload(SNAP, 'bogus')


class PayloadTests(SimpleTestCase):
    @override_settings(SEARCHER_PAGE_LIMIT=12)
    def test_exact_payload_shape(self):
        p = qb.build_payload(SNAP, 'exact')
        self.assertEqual(set(p), {'query', 'context', 'filters', 'pagination'})
        self.assertEqual(p['context']['address_country'], 'US')
        self.assertEqual(p['context']['currency'], 'USD')
        self.assertEqual(p['context']['language'], 'en-US')
        self.assertEqual(p['filters'], {'available': True, 'ships_to': {'country': 'US'},
                                        'price': {'max': 40000}})
        self.assertEqual(p['pagination'], {'limit': 12})

    def test_price_filter_present_for_every_strategy(self):
        prevs = [prev('exact', 'a', 1, 'c', True)]
        for s in qb.strategies(SNAP, [], prevs):
            with self.subTest(strategy=s):
                self.assertEqual(qb.build_payload(SNAP, s, prevs)['filters']['price'], {'max': 40000})

    def test_no_price_filter_without_limit(self):
        p = qb.build_payload(snap(priceLimit=None), 'exact')
        self.assertNotIn('price', p['filters'])
        self.assertEqual(p['filters']['ships_to'], {'country': 'US'})
        self.assertTrue(p['filters']['available'])

    def test_price_minimum_filter(self):
        self.assertEqual(qb.build_payload(snap(priceMin=100), 'exact')['filters']['price'],
                         {'min': 10000, 'max': 40000})
        self.assertEqual(qb.build_payload(snap(priceMin=99.5, priceLimit=None), 'exact')['filters']['price'],
                         {'min': 9950})

    def test_rating_filter_only_when_set(self):
        self.assertNotIn('rating', qb.build_payload(SNAP, 'exact')['filters'])
        prevs = [prev('exact', 'a', 1, 'c', True)]
        for s in qb.strategies(SNAP, [], prevs):
            with self.subTest(strategy=s):
                self.assertEqual(
                    qb.build_payload(snap(minRating=4), s, prevs)['filters']['rating'],
                    {'variant': {'min': 4.0}})
        self.assertEqual(qb.build_payload(snap(minRating=0), 'exact')['filters']['rating'],
                         {'variant': {'min': 0.0}})

    def test_fractional_price_in_cents(self):
        self.assertEqual(qb.build_payload(snap(priceLimit=399.99), 'exact')['filters']['price'],
                         {'max': 39999})


class IntentTests(SimpleTestCase):
    def test_full_intent(self):
        self.assertEqual(
            qb.build_intent(SNAP),
            'Looking for a coffee table (furniture), in mid-century style, made of wood, '
            'fitting within 46 W x 24 D x 17 H inches, weighing at most 40 lb, priced under $400, '
            'similar to: cocktail table, ottoman, for a floor space.')

    def test_every_field_present(self):
        text = qb.build_intent(SNAP)
        for needle in ['coffee table', 'furniture', 'mid-century', 'wood', '46 W', '24 D', '17 H',
                       '40 lb', '$400', 'cocktail table', 'ottoman', 'floor space']:
            self.assertIn(needle, text)

    def test_missing_parts_omitted(self):
        text = qb.build_intent(snap(style=None, material=None, weight=None, priceLimit=None,
                                    similarItems=[], space=None, dims={'x': 30, 'y': 20}))
        self.assertEqual(text, 'Looking for a coffee table (furniture), fitting within 30 W x 20 D inches.')
        for word in ['style', 'made of', 'weighing', 'priced', 'similar', 'space', ' H ']:
            self.assertNotIn(word, text)

    def test_rating_phrase(self):
        text = qb.build_intent(snap(minRating=4))
        self.assertIn('priced under $400, rated at least 4 stars', text)
        self.assertIn('rated at least 4.5 stars', qb.build_intent(snap(minRating=4.5)))
        self.assertNotIn('rated', qb.build_intent(SNAP))

    def test_article_and_mixed_and_decimals(self):
        text = qb.build_intent(snap(name='Area rug', type='rug', material='mixed', priceLimit=99.5))
        self.assertTrue(text.startswith('Looking for an area rug (rug)'))
        self.assertIn('made of mixed materials', text)
        self.assertIn('priced under $99.50', text)


class PriceIntentTests(SimpleTestCase):
    def test_range_and_minimum_only(self):
        self.assertIn('priced between $100 and $400', qb.build_intent(snap(priceMin=100)))
        text = qb.build_intent(snap(priceMin=99.5, priceLimit=None))
        self.assertIn('priced over $99.50', text)
        self.assertNotIn('under', text)


class FuzzyPriceQueryTests(SimpleTestCase):
    def test_widened_filter(self):
        p = qb.build_payload(snap(priceMin=100, priceMinFuzzy=True, priceLimitFuzzy=True), 'exact')
        self.assertEqual(p['filters']['price'], {'min': 8500, 'max': 46000})
        p = qb.build_payload(snap(priceMin=100, priceLimitFuzzy=True), 'exact')
        self.assertEqual(p['filters']['price'], {'min': 10000, 'max': 46000})

    def test_intent(self):
        self.assertIn('priced around $400', qb.build_intent(snap(priceLimitFuzzy=True)))
        self.assertIn('priced between $100 and around $400',
                      qb.build_intent(snap(priceMin=100, priceLimitFuzzy=True)))
        self.assertIn('priced between around $100 and $400',
                      qb.build_intent(snap(priceMin=100, priceMinFuzzy=True)))
        self.assertIn('priced around $100 or more',
                      qb.build_intent(snap(priceMin=100, priceLimit=None, priceMinFuzzy=True)))


class WallIntentTests(SimpleTestCase):
    def test_wall_mode_size_is_w_x_h(self):
        s = {'name': 'Gallery poster', 'type': 'wall-art', 'dims': {'x': 24, 'y': 1, 'z': 36}}
        text = qb.build_intent(s)
        self.assertIn('fitting within 24 W x 36 H inches', text)
        self.assertNotIn(' D', text)
        s = {'name': 'Mirror', 'type': 'decor', 'dims': {'x': 20, 'y': 2, 'z': 30},
             'space': {'type': 'wall', 'dims': {'x': 1, 'y': 1}}}
        self.assertIn('20 W x 30 H inches', qb.build_intent(s))
