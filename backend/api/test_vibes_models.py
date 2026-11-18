"""Vibe board / import data model and on-disk path guards."""
import tempfile
import uuid
from pathlib import Path

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import SearchRun, Space, SpaceInspiration, SpaceObject, VibeBoard, VibeImport
from .searcher import engine
from .vibes import paths


def make_space(sid='space1'):
    now = timezone.now()
    return Space.objects.create(
        id=sid, name='Den', type='room', unit='in', dims={'x': 120, 'y': 96, 'z': 96},
        created_at=now, updated_at=now,
    )


class VibeModelTests(TestCase):
    def test_board_pin_is_unique(self):
        board = VibeBoard.objects.create(board_url='https://pin.it/x', title='Cozy')
        SpaceInspiration.objects.create(
            board=board, pin_id='p1', pin_url='https://pinterest.com/pin/p1/'
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SpaceInspiration.objects.create(
                board=board, pin_id='p1', pin_url='https://pinterest.com/pin/p1/'
            )

    def test_deleting_space_keeps_shared_board_and_pins(self):
        space = make_space()
        board = VibeBoard.objects.create(board_url='https://pin.it/x', title='Cozy')
        space.vibe_board = board
        space.save(update_fields=['vibe_board'])
        imp = VibeImport.objects.create(space=space, board=board, board_url='https://pin.it/x')
        SpaceInspiration.objects.create(
            board=board, vibe_import=imp, pin_id='p1', pin_url='https://pinterest.com/pin/p1/'
        )
        space.delete()
        self.assertTrue(VibeBoard.objects.filter(pk=board.pk).exists())
        self.assertEqual(SpaceInspiration.objects.count(), 1)
        imp.refresh_from_db()
        self.assertIsNone(imp.space_id)

    def test_deleting_board_cascades_pins_and_clears_space_fk(self):
        space = make_space()
        board = VibeBoard.objects.create(board_url='https://pin.it/x', title='Cozy')
        space.vibe_board = board
        space.save(update_fields=['vibe_board'])
        SpaceInspiration.objects.create(
            board=board, pin_id='p1', pin_url='https://pinterest.com/pin/p1/'
        )
        board.delete()
        space.refresh_from_db()
        self.assertIsNone(space.vibe_board_id)
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
    def test_space_and_board_dir_reject_traversal(self):
        with self.assertRaises(ValueError):
            paths.space_dir('../x')
        with self.assertRaises(ValueError):
            paths.board_dir('../x')

    def test_space_and_board_dir_accept_valid_id(self):
        bid = uuid.uuid4()
        with tempfile.TemporaryDirectory() as tmp, override_settings(VIBES_DIR=tmp):
            root = Path(tmp).resolve()
            self.assertEqual(paths.space_dir('abc-1'), root / 'abc-1')
            self.assertEqual(paths.board_dir(bid), root / 'boards' / str(bid))

    def test_resolve_inside_rejects_escape_and_accepts_file(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(VIBES_DIR=tmp):
            root = Path(tmp).resolve()
            (root / 'boards' / 'b1').mkdir(parents=True)
            (root / 'boards' / 'b1' / 'p1.jpg').write_bytes(b'\xff\xd8\xff')
            self.assertIsNone(paths.resolve_inside('../../etc/passwd'))
            self.assertIsNone(paths.resolve_inside('boards/b1'))  # a directory
            self.assertIsNone(paths.resolve_inside('boards/b1/missing.jpg'))
            self.assertEqual(
                paths.resolve_inside('boards/b1/p1.jpg'), root / 'boards' / 'b1' / 'p1.jpg'
            )
