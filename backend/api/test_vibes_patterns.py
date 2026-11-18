"""Pattern lexicon, board themes, label parsing of patterns and item boxes, the
migration's backfill and the relabel_vibes command. No network: the claude CLI
is the fake one."""
import importlib
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .test_fixtures.vibes_helpers import TempVibes, jpeg_bytes, make_board, make_space
from .models import SpaceInspiration
from .test_vibes_categorize import entry
from .vibes import categorize, paths, patterns


def rows(*pattern_lists):
    return [{'patterns': list(p)} for p in pattern_lists]


class LexiconTests(SimpleTestCase):
    def test_canonical_names(self):
        for text, want in [
            ('leopard', 'leopard print'), ('Leopard-Spot', 'leopard print'),
            ('leopard-print', 'leopard print'), ('zebra stripe', 'zebra print'),
            ('zebra-print', 'zebra print'), ('cheetah', 'cheetah print'),
            ('tiger-stripe', 'tiger print'), ('cow-print', 'cowhide'), ('cow print', 'cowhide'),
            ('cowhide', 'cowhide'), ('giraffe', 'giraffe print'), ('python print', 'snakeskin'),
            ('snake pattern', 'snakeskin'), ('tortoise', 'tortoiseshell'), ('striped', 'stripe'),
            ('gingham', 'check'), ('plaid', 'check'), ('checkered', 'check'),
            ('floral', 'floral'), ('polka dot', 'polka dot'), ('polka-dot', 'polka dot'),
            ('Herringbone', 'herringbone'), ('', ''),
        ]:
            self.assertEqual(patterns.canonical(text), want, text)

    def test_longer_phrase_wins_over_its_words(self):
        self.assertEqual(patterns.find_patterns('a zebra-stripe pouf'), ['zebra print'])
        self.assertEqual(patterns.find_patterns('striped zebra print rug'),
                         ['stripe', 'zebra print'])

    def test_no_partial_word_hits(self):
        self.assertEqual(patterns.find_patterns('pythonic checkout tigerlily'), [])

    def test_bare_snake_and_python_are_not_snakeskin(self):
        self.assertEqual(patterns.find_patterns('a snake plant in a green pot'), [])
        self.assertEqual(patterns.find_patterns('a snake print clutch'), ['snakeskin'])
        self.assertEqual(patterns.find_patterns('python print boots'), ['snakeskin'])

    def test_canonical_list_dedupes_and_caps(self):
        self.assertEqual(patterns.canonical_list(['Leopard', 'leopard print', 'zebra', 7]),
                         ['leopard print', 'zebra print'])

    def test_families(self):
        for name in ('leopard print', 'zebra print', 'cheetah print', 'tiger print', 'cowhide',
                     'giraffe print', 'snakeskin', 'tortoiseshell', 'animal print'):
            self.assertEqual(patterns.family(name), 'animal print')
        self.assertEqual(patterns.family('stripe'), 'stripe')

    def test_derive_patterns_unions_model_and_text(self):
        got = patterns.derive_patterns({
            'patterns': ['Cowhide'], 'description': 'a leopard scalloped sofa',
            'styles': ['glam'], 'materials': ['zebra-print velvet'], 'item': 'striped chair'})
        self.assertEqual(got, ['cowhide', 'leopard print', 'stripe', 'zebra print'])
        self.assertEqual(patterns.derive_patterns({'description': 'a plain oak table'}), [])


class BoardThemeTests(SimpleTestCase):
    def test_animal_family_theme(self):
        pins = rows(*([['leopard print']] * 4 + [['zebra print']] * 3 + [['cowhide', 'stripe']]
                      + [[]] * 6))
        theme = patterns.board_theme(pins)
        self.assertEqual(theme, {'family': 'animal print',
                                 'patterns': ['leopard print', 'zebra print', 'cowhide'],
                                 'pins': 8, 'boardPins': 14})

    def test_thirty_percent_and_three_pins_needed(self):
        self.assertIsNone(patterns.board_theme(rows(*([['zebra print']] * 2 + [[]] * 2))))
        self.assertIsNone(patterns.board_theme(rows(*([['zebra print']] * 3 + [[]] * 8))))  # 27%
        self.assertIsNotNone(patterns.board_theme(rows(*([['zebra print']] * 3 + [[]] * 7))))
        self.assertIsNone(patterns.board_theme([]))

    def test_single_non_animal_pattern_is_its_own_family(self):
        theme = patterns.board_theme(rows(*([['stripe']] * 4 + [[]] * 6)))
        self.assertEqual(theme['family'], 'stripe')
        self.assertEqual(theme['patterns'], ['stripe'])

    def test_generic_animal_print_only_when_nothing_specific(self):
        pins = rows(*([['animal print']] * 3 + [['leopard print']] * 2))
        self.assertEqual(patterns.board_theme(pins)['patterns'], ['leopard print'])
        self.assertEqual(patterns.board_theme(rows(*([['animal print']] * 3)))['patterns'],
                         ['animal print'])

    def test_top_four_patterns(self):
        names = ['leopard print', 'zebra print', 'cowhide', 'tiger print', 'giraffe print']
        theme = patterns.board_theme(rows(*[[n] for n in names for _ in range(3)]))
        self.assertEqual(len(theme['patterns']), 4)

    def test_carries_theme(self):
        theme = {'family': 'animal print'}
        self.assertTrue(patterns.carries_theme(['cowhide'], theme))
        self.assertFalse(patterns.carries_theme(['stripe'], theme))
        self.assertFalse(patterns.carries_theme(['cowhide'], None))


class ParseLabelFieldsTests(SimpleTestCase):
    def parse(self, **kw):
        return categorize.parse_labels({'pins': [entry(0, **kw)]}, 1)[0]

    def test_patterns_are_canonical_and_capped(self):
        out = self.parse(patterns=['Leopard-Spot', 'zebra', 'Herringbone', 'cow print', 'floral'])
        self.assertEqual(out['patterns'], ['leopard print', 'zebra print', 'herringbone', 'cowhide'])

    def test_box_valid_clamped_and_rounded(self):
        self.assertEqual(self.parse(box=[0.1, 0.2, 0.8, 0.9])['box'], [0.1, 0.2, 0.8, 0.9])
        self.assertEqual(self.parse(box=[-0.5, 0, 2, 1.5])['box'], [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(self.parse(box=[0, 0, 1, 1])['box'], [0.0, 0.0, 1.0, 1.0])

    def test_box_invalid_is_none(self):
        for bad in ([0.1, 0.2, 0.8], [0.1, 0.2, 0.8, 0.9, 1], 'box', None, [0, 0, 'a', 1],
                    [True, 0, 1, 1], [0.5, 0.1, 0.5, 0.9], [0.8, 0.1, 0.2, 0.9],
                    [0.1, 0.1, 0.3, 0.3],  # area 0.04 minus float noise below the floor
                    [0.1, 0.1, 0.2, 0.2], [0, 0, 0, 0]):
            self.assertIsNone(self.parse(box=bad)['box'], bad)

    def test_box_area_floor_is_inclusive(self):
        self.assertIsNotNone(self.parse(box=[0, 0, 0.2, 0.2])['box'])

    def test_box_is_none_without_categories(self):
        self.assertIsNone(self.parse(categories=[], box=[0, 0, 1, 1])['box'])

    def test_row_fields_derive_patterns_from_text(self):
        label = self.parse(patterns=['cowhide'], description='a leopard sofa', box=[0, 0, 1, 1])
        fields = categorize.row_fields(label)
        self.assertEqual(fields['patterns'], ['cowhide', 'leopard print'])
        self.assertEqual(fields['item_box'], [0.0, 0.0, 1.0, 1.0])

    def test_row_fields_set_model_patterns_apart_from_patterns(self):
        label = self.parse(patterns=['Cowhide'], description='a leopard sofa')
        fields = categorize.row_fields(label)
        self.assertEqual(fields['model_patterns'], ['cowhide'])
        self.assertEqual(fields['patterns'], ['cowhide', 'leopard print'])

    def test_derive_patterns_model_row_ignores_stored_patterns(self):
        row = SpaceInspiration(patterns=['snakeskin'], model_patterns=['cowhide'],
                               description='beside a snake plant')
        self.assertEqual(patterns.derive_patterns(row), ['cowhide'])
        self.assertEqual(patterns.derive_patterns({'model_patterns': [], 'patterns': ['x']}), [])


class BackfillMigrationTests(TestCase):
    def test_backfill_from_text(self):
        migration = importlib.import_module('api.migrations.0014_inspiration_patterns')
        board = make_board()
        made = timezone.now()

        def row(pin, **kw):
            kw.setdefault('categorized_at', made)
            return SpaceInspiration.objects.create(
                board=board, pin_id=pin, pin_url='https://x/', **kw)

        a = row('a', description='A leopard print scalloped sofa', item='sofa')
        b = row('b', styles=['zebra-print'], materials=['cowhide'], item='bed')
        c = row('c', description='Plain oak table', item='table')
        d = row('d', description='leopard', categorized_at=None)
        from django.apps import apps
        migration.backfill_patterns(apps, None)
        for r in (a, b, c, d):
            r.refresh_from_db()
        self.assertEqual(a.patterns, ['leopard print'])
        self.assertEqual(b.patterns, ['zebra print', 'cowhide'])
        self.assertEqual(c.patterns, [])
        self.assertEqual(d.patterns, [])  # unsorted pins are left alone

    def test_frozen_lexicon_matches_the_module_today(self):
        migration = importlib.import_module('api.migrations.0014_inspiration_patterns')
        self.assertEqual(migration.LEXICON, patterns.LEXICON)


class RelabelCommandTests(TempVibes, TestCase):
    def setUp(self):
        super().setUp()
        self.space = make_space()
        self.board = make_board(self.space)

    def make_row(self, pin_id, title, image=True, **kw):
        rel = f'boards/{self.board.id}/{pin_id}.jpg'
        if image:
            target = paths.vibes_dir() / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(jpeg_bytes())
        return SpaceInspiration.objects.create(
            board=self.board, pin_id=pin_id, pin_url='https://x/', image_path=rel if image else '',
            pin_text={'title': title, 'description': '', 'altText': ''}, board_rank=len(title),
            categories=['decor'], item='vase', categorized_at=timezone.now(), **kw)

    def run_cmd(self, *args):
        out, err = StringIO(), StringIO()
        call_command('relabel_vibes', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_relabels_from_stored_images_with_fake_claude(self):
        sofa = self.make_row('p1', 'cat=furniture item=sofa pat=leopard-print,zebra-print '
                                   'box=0.1,0.2,0.6,0.9')
        blank = self.make_row('p2', 'cat= item=')
        gone = self.make_row('p3', 'cat=furniture item=bed', image=False)
        out, _ = self.run_cmd(self.space.id)
        self.assertIn('relabeled 2 pins', out)
        self.assertIn('1 skipped', out)
        for r in (sofa, blank, gone):
            r.refresh_from_db()
        self.assertEqual(sofa.categories, ['furniture'])
        self.assertEqual(sofa.item, 'sofa')
        self.assertEqual(sofa.patterns, ['leopard print', 'zebra print'])
        self.assertEqual(sofa.model_patterns, ['leopard print', 'zebra print'])
        self.assertEqual(sofa.item_box, [0.1, 0.2, 0.6, 0.9])
        self.assertIsNone(blank.item_box)
        self.assertEqual(gone.item, 'vase')  # no image file: untouched
        self.assertEqual(gone.categories, ['decor'])
        leftovers = [p for p in paths.imports_dir().glob('relabel-*')] if paths.imports_dir().is_dir() else []
        self.assertEqual(leftovers, [])

    def test_patterns_only_needs_no_claude(self):
        row = self.make_row('p1', 'x', description='a zebra-print pouf')
        plain = self.make_row('p2', 'xx', description='plain')
        with self.settings(SEARCHER_CLAUDE_BIN='/nonexistent/claude'):
            out, _ = self.run_cmd(self.space.id, '--patterns-only')
        self.assertIn('patterns updated on 1 pins', out)
        row.refresh_from_db()
        plain.refresh_from_db()
        self.assertEqual(row.patterns, ['zebra print'])
        self.assertEqual(plain.patterns, [])
        self.assertEqual(row.item, 'vase')  # labels untouched

    def test_patterns_only_drops_stale_text_derived_pattern(self):
        stale = self.make_row('p1', 'x', description='a leopard chair beside a snake plant',
                              patterns=['leopard print', 'snakeskin'],
                              model_patterns=['leopard print'])
        model_only = self.make_row('p2', 'xx', description='a plain chair',
                                   patterns=['herringbone'], model_patterns=['herringbone'])
        out, _ = self.run_cmd(self.space.id, '--patterns-only')
        self.assertIn('patterns updated on 1 pins', out)
        stale.refresh_from_db()
        model_only.refresh_from_db()
        self.assertEqual(stale.patterns, ['leopard print'])
        self.assertEqual(stale.model_patterns, ['leopard print'])
        self.assertEqual(model_only.patterns, ['herringbone'])

    def test_unknown_space(self):
        with self.assertRaises(CommandError):
            self.run_cmd('nope')
