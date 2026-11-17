"""Vibe import data model and on-disk path guards."""
import tempfile
from pathlib import Path

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Space, SearchRun, SpaceInspiration, SpaceObject, VibeImport
from .searcher import engine
from .vibes import paths


def make_space(sid='space1'):
    now = timezone.now()
    return Space.objects.create(
        id=sid, name='Den', type='room', unit='in', dims={'x': 120, 'y': 96, 'z': 96},
        created_at=now, updated_at=now,
    )


class VibeModelTests(TestCase):
    def test_space_pin_is_unique(self):
        space = make_space()
        SpaceInspiration.objects.create(space=space, pin_id='p1', pin_url='https://pinterest.com/pin/p1/')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpaceInspiration.objects.create(space=space, pin_id='p1', pin_url='https://pinterest.com/pin/p1/')

    def test_deleting_space_cascades(self):
        space = make_space()
        imp = VibeImport.objects.create(space=space, board_url='https://pin.it/x')
        SpaceInspiration.objects.create(
            space=space, vibe_import=imp, pin_id='p1', pin_url='https://pinterest.com/pin/p1/'
        )
        space.delete()
        self.assertFalse(VibeImport.objects.filter(pk=imp.pk).exists())
        self.assertEqual(SpaceInspiration.objects.count(), 0)

    def test_search_run_vibes_defaults_to_none(self):
        make_space()
        obj = SpaceObject.objects.create(
            id='obj1', name='Sofa', dims={'x': 60, 'y': 33, 'z': 34}, type='furniture',
            created_at=timezone.now(),
        )
        run = engine.create_run(obj)
        self.assertIsNone(SearchRun.objects.get(pk=run.pk).vibes)


class VibePathTests(TestCase):
    def test_space_dir_rejects_traversal(self):
        with self.assertRaises(ValueError):
            paths.space_dir('../x')

    def test_space_dir_accepts_valid_id(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(VIBES_DIR=tmp):
            self.assertEqual(paths.space_dir('abc-1'), Path(tmp).resolve() / 'abc-1')

    def test_resolve_inside_rejects_escape_and_accepts_file(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(VIBES_DIR=tmp):
            root = Path(tmp).resolve()
            (root / 'space1').mkdir()
            (root / 'space1' / 'p1.jpg').write_bytes(b'\xff\xd8\xff')
            self.assertIsNone(paths.resolve_inside('../../etc/passwd'))
            self.assertIsNone(paths.resolve_inside('space1'))  # a directory
            self.assertIsNone(paths.resolve_inside('space1/missing.jpg'))
            self.assertEqual(paths.resolve_inside('space1/p1.jpg'), root / 'space1' / 'p1.jpg')
