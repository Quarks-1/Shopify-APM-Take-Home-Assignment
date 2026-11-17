"""The whole vibes loop with no mocks beyond the network: fake Pinterest client ->
importer.execute -> real fake_claude categorize -> a search whose run snapshot
and agent payload only carry category-matched pins."""
import json
from unittest import mock

from django.test import TransactionTestCase, override_settings

from .models import (
    Placement, RunStatus, SpaceInspiration, VibeImport, VibeImportStatus,
)
from .searcher import engine, like_images, runner
from .test_fixtures.vibes_helpers import BOARD_URL, FakeClient, TempVibes, make_space, raw_pin
from .test_like_search import FAKE_UCP, fake_read, make_object
from .vibes import importer, matching

TITLES = [
    'cat=furniture item=sofa', 'cat=furniture item=sofa', 'cat=furniture item=sectional',
    'cat=wall-art item=framed print', 'cat=wall-art item=framed print',
    'cat=furniture,lighting,rug item=sofa', 'cat=furniture,lighting,rug item=armchair',
    'cat=lighting item=floor lamp',
    'cat= item=', 'cat= item=',
]


class VibesEndToEndTests(TempVibes, TransactionTestCase):
    def setUp(self):
        super().setUp()
        cm = override_settings(
            SEARCHER_UCP_BIN=FAKE_UCP, SEARCHER_AGENT='claude', SEARCHER_FALLBACK='deterministic',
            SEARCHER_RUN_TIMEOUT=60, SEARCHER_TARGET=2, SEARCHER_MODEL='haiku')
        cm.enable()
        self.addCleanup(cm.disable)
        for p in (mock.patch.object(runner, '_spawn_worker', side_effect=AssertionError('no workers')),
                  mock.patch.object(like_images, '_read', side_effect=fake_read)):
            p.start()
            self.addCleanup(p.stop)
        self.space = make_space()
        pins = [raw_pin(f'e{i:02d}', title) for i, title in enumerate(TITLES)]
        imp = VibeImport.objects.create(space=self.space, board_url=BOARD_URL)
        importer.execute(imp.pk, client_factory=lambda: FakeClient(pins))
        self.imp = VibeImport.objects.get(pk=imp.pk)

    def search(self, obj, ok=(RunStatus.DONE,)):
        Placement.objects.create(space=self.space, object=obj, x=1, y=1)
        run = engine.create_run(obj, status=RunStatus.QUEUED)
        runner.execute(run)
        run.refresh_from_db()
        self.assertIn(run.status, ok, run.error)
        return run

    def test_import_finished_with_eight_usable_pins(self):
        self.assertEqual(self.imp.status, VibeImportStatus.DONE)
        self.assertEqual(self.imp.pins_usable, 8)
        self.assertEqual(SpaceInspiration.objects.filter(space=self.space).count(), 10)

    def test_couch_search_only_sees_furniture_pins(self):
        run = self.search(make_object(name='Couch', type='furniture', similar_items=['loveseat']))
        pins = run.vibes['pins']
        self.assertTrue(pins)
        for pin in pins:
            self.assertIn('furniture', pin['categories'])
        self.assertEqual(len(pins), 5)  # 3 sofas + 2 room shots; no art, lamp or empty pins
        self.assertEqual(matching.normalize_item(pins[0]['item']), ('sofa',))
        self.assertEqual(run.queries.order_by('round').first().strategy, 'vibe')
        info = json.loads((self.var / 'agent-cwd' / f'fake-claude-{run.pk}.json').read_text())
        self.assertEqual(info['problems'], [])
        self.assertEqual(info['image_types'], ['image/jpeg'] * 3)

    def test_floor_lamp_search_only_sees_lighting_pins(self):
        run = self.search(make_object(oid='lamp1', name='Floor lamp', type='lighting',
                                      similar_items=[]),
                          ok=(RunStatus.DONE, RunStatus.EMPTY))  # fake ucp has no lamps
        pins = run.vibes['pins']
        self.assertEqual(len(pins), 3)  # the lamp and the two room shots
        for pin in pins:
            self.assertIn('lighting', pin['categories'])
            self.assertNotEqual(pin['item'], 'sectional')
        self.assertEqual(matching.normalize_item(pins[0]['item']), ('floor', 'lamp'))
