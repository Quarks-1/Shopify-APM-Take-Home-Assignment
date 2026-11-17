"""The import worker end to end with a fake Pinterest client and the fake
claude: no network, no real Claude."""
import threading
import time
from pathlib import Path
from unittest import mock

from django.test import TestCase

from .models import Space, SpaceInspiration, VibeImport, VibeImportStatus, VibeImportStep
from .test_fixtures.vibes_helpers import (
    BOARD_URL, FakeClient, TempVibes, make_space, pins_for, raw_pin,
)
from .vibes import categorize, importer, paths


def make_import(space, url=BOARD_URL):
    return VibeImport.objects.create(space=space, board_url=url)


class ImportBase(TempVibes, TestCase):
    def setUp(self):
        super().setUp()
        self.space = make_space()

    def run_import(self, client, space=None, url=BOARD_URL):
        imp = make_import(space or self.space, url)
        importer.execute(imp.pk, client_factory=lambda: client)
        return VibeImport.objects.filter(pk=imp.pk).first()


class ImportFlowTests(ImportBase):
    def test_forty_pins_in_three_batches(self):
        pins = pins_for('p', 40)
        pins[0]['grid_title'] = 'cat=furniture,lighting item=sofa'
        client = FakeClient(pins, pin_count=57)
        sizes, steps = [], []
        real_batch = categorize.categorize_batch
        real_step = importer._set_step

        def spy_batch(no, batch, **kw):
            sizes.append(len(batch))
            return real_batch(no, batch, **kw)

        def spy_step(import_id, step, **kw):
            steps.append(step)
            return real_step(import_id, step, **kw)

        with mock.patch.object(categorize, 'categorize_batch', side_effect=spy_batch), \
                mock.patch.object(importer, '_set_step', side_effect=spy_step):
            imp = self.run_import(client)

        self.assertEqual(sorted(sizes), [10, 15, 15])
        self.assertEqual(imp.status, VibeImportStatus.DONE)
        self.assertEqual(imp.step, VibeImportStep.SAVING)
        order = list(VibeImportStep.values)
        self.assertEqual(steps, sorted(steps, key=order.index))
        self.assertEqual(set(steps), set(order))
        self.assertEqual((imp.pins_on_board, imp.pins_found, imp.pins_skipped), (57, 40, 0))
        self.assertEqual((imp.pins_downloaded, imp.pins_failed, imp.pins_categorized), (40, 0, 40))
        self.assertEqual((imp.batches_total, imp.batches_done, imp.batches_failed), (3, 3, 0))
        self.assertEqual(imp.pins_usable, 40)
        self.assertEqual((imp.board_title, imp.resolved_url), ('Cozy living', BOARD_URL))
        self.assertIsNotNone(imp.finished_at)
        self.assertEqual(imp.error, '')
        self.assertIsNone(client.on_image)

        rows = list(SpaceInspiration.objects.filter(space=self.space))
        self.assertEqual(len(rows), 40)
        for row in rows:
            self.assertEqual((row.vote, row.source, row.vibe_import_id), ('up', 'pinterest', imp.pk))
            self.assertIsNotNone(row.categorized_at)
            self.assertEqual(row.image_path, f'space1/{row.pin_id}.jpg')
            self.assertTrue(paths.resolve_inside(row.image_path).is_file())
            self.assertEqual(set(row.pin_text), {'title', 'description', 'altText'})
        first = SpaceInspiration.objects.get(space=self.space, pin_id='p000')
        self.assertEqual((first.categories, first.item, first.board_rank),
                         (['furniture', 'lighting'], 'sofa', 0))
        self.assertEqual(SpaceInspiration.objects.get(pin_id='p001').categories, ['decor'])
        # Temp stdin files are gone; logs / outputs remain.
        leftovers = [p.name for p in paths.import_dir(imp.pk).iterdir()]
        self.assertFalse([n for n in leftovers if n.endswith('.stdin.jsonl')])
        self.assertTrue([n for n in leftovers if n.endswith('.out.json')])

    def test_images_are_normalized_jpegs(self):
        self.run_import(FakeClient(pins_for('p', 2)))
        data = (paths.space_dir('space1') / 'p000.jpg').read_bytes()
        self.assertEqual(data[:2], b'\xff\xd8')

    def test_private_board(self):
        imp = self.run_import(FakeClient(pins_for('p', 3), privacy='secret'))
        self.assertEqual(imp.status, VibeImportStatus.FAILED)
        self.assertEqual(imp.error_code, 'private_board')
        self.assertEqual(imp.error, 'This board is private. Make it public on Pinterest and try again.')
        self.assertEqual(SpaceInspiration.objects.count(), 0)

    def test_empty_board(self):
        imp = self.run_import(FakeClient([]))
        self.assertEqual((imp.status, imp.error_code), (VibeImportStatus.FAILED, 'empty_board'))
        self.assertEqual(imp.error, 'This board has no pins we can use.')

    def test_network_error_while_reading(self):
        import urllib.error
        imp = self.run_import(FakeClient([], load_error=urllib.error.URLError('down')))
        self.assertEqual((imp.status, imp.error_code), (VibeImportStatus.FAILED, 'network'))
        self.assertEqual(imp.error, "We couldn't reach Pinterest. Try again in a bit.")

    def test_unexpected_error_is_internal(self):
        imp = self.run_import(FakeClient([], load_error=RuntimeError('boom')))
        self.assertEqual((imp.status, imp.error_code), (VibeImportStatus.FAILED, 'internal'))
        self.assertEqual(imp.error, 'Something went wrong importing this board.')
        self.assertNotIn('boom', imp.error)

    def test_every_download_failing_is_a_network_error(self):
        pins = pins_for('p', 3)
        client = FakeClient(pins, bad_images=[p['id'] for p in pins])
        imp = self.run_import(client)
        self.assertEqual((imp.status, imp.error_code), (VibeImportStatus.FAILED, 'network'))
        self.assertEqual(imp.pins_failed, 3)

    def test_some_downloads_failing_still_succeeds(self):
        client = FakeClient(pins_for('p', 4), bad_images=['p001'])
        imp = self.run_import(client)
        self.assertEqual(imp.status, VibeImportStatus.DONE)
        self.assertEqual((imp.pins_downloaded, imp.pins_failed), (3, 1))
        self.assertFalse(SpaceInspiration.objects.filter(pin_id='p001').exists())

    def test_all_batches_failing_is_sort_failed_and_rows_stay(self):
        pins = pins_for('p', 20, marker='fake=exit1')
        imp = self.run_import(FakeClient(pins))
        self.assertEqual((imp.status, imp.error_code), (VibeImportStatus.FAILED, 'sort_failed'))
        self.assertEqual(imp.error, "We couldn't sort your pins right now. Try again in a bit.")
        self.assertEqual((imp.batches_total, imp.batches_done, imp.batches_failed), (2, 0, 2))
        rows = SpaceInspiration.objects.filter(space=self.space)
        self.assertEqual(rows.count(), 20)
        self.assertFalse(rows.exclude(categorized_at=None).exists())

    def test_one_failed_batch_still_done_and_uncategorized_retried_next_time(self):
        pins = pins_for('p', 20)
        for pin in pins[15:]:
            pin['grid_title'] += ' fake=exit1'
        imp = self.run_import(FakeClient(pins))
        self.assertEqual(imp.status, VibeImportStatus.DONE)
        self.assertEqual((imp.batches_done, imp.batches_failed, imp.pins_categorized), (1, 1, 15))
        self.assertEqual(SpaceInspiration.objects.filter(categorized_at=None).count(), 5)
        # A re-import sorts the leftovers (the rows now return real labels).
        SpaceInspiration.objects.filter(categorized_at=None).update(
            pin_text={'title': 'cat=rug item=rug', 'description': '', 'altText': ''})
        imp2 = self.run_import(FakeClient(pins))
        self.assertEqual(imp2.status, VibeImportStatus.DONE)
        self.assertEqual((imp2.batches_total, imp2.pins_categorized), (1, 5))
        self.assertEqual(SpaceInspiration.objects.filter(categorized_at=None).count(), 0)
        self.assertEqual(SpaceInspiration.objects.get(pin_id='p016').categories, ['rug'])

    def test_reimport_same_board_skips_everything(self):
        pins = pins_for('p', 20)
        self.run_import(FakeClient(pins))
        client = FakeClient(pins)
        imp = self.run_import(client)
        self.assertEqual(imp.status, VibeImportStatus.DONE)
        self.assertEqual((imp.pins_found, imp.pins_skipped, imp.pins_downloaded), (20, 20, 0))
        self.assertEqual((imp.batches_total, imp.pins_categorized), (0, 0))
        self.assertEqual(client.image_requests, [])
        self.assertEqual(SpaceInspiration.objects.filter(space=self.space).count(), 20)
        self.assertEqual(imp.pins_usable, 20)

    def test_second_board_replaces_the_first(self):
        first = self.run_import(FakeClient(pins_for('a', 5)))
        old_files = [paths.space_dir('space1') / f'a{i:03d}.jpg' for i in range(5)]
        self.assertTrue(all(f.is_file() for f in old_files))
        imp = self.run_import(FakeClient(pins_for('b', 4)))
        self.assertEqual(imp.status, VibeImportStatus.DONE)
        self.assertEqual((imp.pins_found, imp.pins_skipped, imp.pins_downloaded), (4, 0, 4))
        rows = SpaceInspiration.objects.filter(space=self.space)
        self.assertEqual(sorted(rows.values_list('pin_id', flat=True)),
                         ['b000', 'b001', 'b002', 'b003'])
        self.assertEqual({r.vibe_import_id for r in rows}, {imp.pk})
        self.assertNotEqual(first.pk, imp.pk)
        self.assertEqual(imp.pins_usable, 4)
        self.assertFalse(any(f.exists() for f in old_files))
        self.assertTrue((paths.space_dir('space1') / 'b000.jpg').is_file())

    def test_overlapping_pins_are_kept_and_repointed(self):
        first = self.run_import(FakeClient(pins_for('a', 5)))
        kept_before = SpaceInspiration.objects.get(space=self.space, pin_id='a001')
        # New board: b000, b001, then a001 and a000 (reversed ranks).
        board = pins_for('b', 2) + [raw_pin('a001', 'sofa 1'), raw_pin('a000', 'sofa 0')]
        client = FakeClient(board)
        imp = self.run_import(client)
        self.assertEqual(imp.status, VibeImportStatus.DONE)
        self.assertEqual((imp.pins_found, imp.pins_skipped, imp.pins_downloaded), (4, 2, 2))
        self.assertEqual(len(client.image_requests), 2)  # no re-download of a000 / a001
        rows = {r.pin_id: r for r in SpaceInspiration.objects.filter(space=self.space)}
        self.assertEqual(sorted(rows), ['a000', 'a001', 'b000', 'b001'])
        self.assertEqual({r.vibe_import_id for r in rows.values()}, {imp.pk})
        self.assertNotEqual(first.pk, imp.pk)
        self.assertEqual((rows['b000'].board_rank, rows['b001'].board_rank,
                          rows['a001'].board_rank, rows['a000'].board_rank), (0, 1, 2, 3))
        self.assertEqual(rows['a001'].pk, kept_before.pk)
        self.assertIsNotNone(rows['a001'].categorized_at)  # keeps its labels
        self.assertTrue((paths.space_dir('space1') / 'a001.jpg').is_file())
        self.assertFalse((paths.space_dir('space1') / 'a002.jpg').exists())

    def test_failed_reads_leave_the_old_pins_intact(self):
        import urllib.error
        self.run_import(FakeClient(pins_for('a', 3)))
        for client in (
            FakeClient(pins_for('b', 3), privacy='secret'),
            FakeClient(pins_for('b', 3), load_error=urllib.error.URLError('down')),
            FakeClient([]),  # empty board
        ):
            imp = self.run_import(client)
            self.assertEqual(imp.status, VibeImportStatus.FAILED)
            self.assertEqual(sorted(SpaceInspiration.objects.filter(space=self.space)
                                    .values_list('pin_id', flat=True)),
                             ['a000', 'a001', 'a002'])
            for i in range(3):
                self.assertTrue((paths.space_dir('space1') / f'a{i:03d}.jpg').is_file())

    def test_reimport_after_pins_left_the_board_removes_them(self):
        self.run_import(FakeClient(pins_for('p', 5)))
        imp = self.run_import(FakeClient(pins_for('p', 3)))
        self.assertEqual((imp.pins_found, imp.pins_skipped, imp.pins_downloaded), (3, 3, 0))
        self.assertEqual(SpaceInspiration.objects.filter(space=self.space).count(), 3)
        self.assertFalse((paths.space_dir('space1') / 'p004.jpg').exists())
        self.assertEqual(imp.pins_usable, 3)

    def test_pruning_is_per_space(self):
        other = make_space('space2')
        self.run_import(FakeClient(pins_for('a', 3)), space=other)
        self.run_import(FakeClient(pins_for('a', 3)))
        self.run_import(FakeClient(pins_for('b', 2)))
        self.assertEqual(sorted(SpaceInspiration.objects.filter(space=self.space)
                                .values_list('pin_id', flat=True)), ['b000', 'b001'])
        theirs = SpaceInspiration.objects.filter(space=other)
        self.assertEqual(sorted(theirs.values_list('pin_id', flat=True)), ['a000', 'a001', 'a002'])
        self.assertNotIn(None, theirs.values_list('vibe_import_id', flat=True))
        for i in range(3):
            self.assertTrue((paths.space_dir('space2') / f'a{i:03d}.jpg').is_file())
        self.assertFalse((paths.space_dir('space1') / 'a000.jpg').exists())

    def test_pruning_never_deletes_outside_the_vibes_dir(self):
        outside = self.var / 'precious.jpg'
        outside.write_bytes(b'x')
        self.run_import(FakeClient(pins_for('a', 2)))
        SpaceInspiration.objects.filter(pin_id='a000').update(image_path='../precious.jpg')
        self.run_import(FakeClient(pins_for('b', 1)))
        self.assertTrue(outside.is_file())
        self.assertFalse(SpaceInspiration.objects.filter(pin_id='a000').exists())

    def test_other_spaces_pins_are_independent(self):
        other = make_space('space2')
        pins = pins_for('p', 3)
        self.run_import(FakeClient(pins))
        imp = self.run_import(FakeClient(pins), space=other)
        self.assertEqual(imp.pins_skipped, 0)
        self.assertEqual(SpaceInspiration.objects.filter(space=other).count(), 3)
        self.assertTrue((paths.space_dir('space2') / 'p000.jpg').is_file())

    def test_queued_import_is_promoted_by_execute(self):
        imp = self.run_import(FakeClient(pins_for('p', 1)))
        self.assertIsNotNone(imp.started_at)

    def test_finished_import_is_not_rerun(self):
        imp = self.run_import(FakeClient(pins_for('p', 2)))
        again = FakeClient(pins_for('q', 2))
        importer.execute(imp.pk, client_factory=lambda: again)
        self.assertEqual(again.image_requests, [])

    def test_dispatch_runs_after_every_execute(self):
        with mock.patch.object(importer, 'dispatch') as dispatch:
            self.run_import(FakeClient([], privacy='secret'))
        dispatch.assert_called_once()


class ImportGoneTests(ImportBase):
    def test_space_deleted_while_reading_board_exits_quietly(self):
        client = FakeClient(pins_for('p', 3), on_load=lambda: Space.objects.filter(pk='space1').delete())
        imp = make_import(self.space)
        importer.execute(imp.pk, client_factory=lambda: client)  # must not raise
        self.assertFalse(VibeImport.objects.filter(pk=imp.pk).exists())
        self.assertEqual(client.image_requests, [])

    def test_space_deleted_while_downloading_exits_quietly_and_cleans_up(self):
        def delete_on_third(n):
            if n == 3:
                Space.objects.filter(pk='space1').delete()

        client = FakeClient(pins_for('p', 6), on_image=delete_on_third)
        imp = make_import(self.space)
        with mock.patch.object(categorize, 'categorize_batch') as batch:
            importer.execute(imp.pk, client_factory=lambda: client)
        batch.assert_not_called()
        self.assertEqual(len(client.image_requests), 3)
        self.assertFalse(VibeImport.objects.filter(pk=imp.pk).exists())
        self.assertEqual(SpaceInspiration.objects.count(), 0)
        self.assertFalse(paths.space_dir('space1').exists())

    def test_sigterm_flag_stops_the_import(self):
        def stop(n):
            importer.CANCEL.set()

        client = FakeClient(pins_for('p', 6), on_image=stop)
        imp = self.run_import(client)
        self.assertEqual((imp.status, imp.error_code), (VibeImportStatus.FAILED, 'canceled'))

    def test_import_stopped_during_sorting_cancels_batches(self):
        def slow_batch(no, batch, *, work_dir, cancel, batch_id):
            cancel.wait(30)
            return categorize.BatchResult(failure='canceled')

        imp = make_import(self.space)

        # Flip the status on the main thread's connection, from inside the
        # second poll of the sorting loop (a worker thread can't see this TestCase's data).
        real_wait = importer.wait
        calls = []

        def patched_wait(futures, timeout=None, return_when=None):
            calls.append(1)
            if len(calls) == 2:
                VibeImport.objects.filter(pk=imp.pk).update(status=VibeImportStatus.FAILED)
            return real_wait(futures, timeout=0.2, return_when=return_when)

        with mock.patch.object(categorize, 'categorize_batch', side_effect=slow_batch), \
                mock.patch.object(importer, 'wait', side_effect=patched_wait):
            started_at = time.monotonic()
            importer.execute(imp.pk, client_factory=lambda: FakeClient(pins_for('p', 20)))
        self.assertLess(time.monotonic() - started_at, 20)
        imp.refresh_from_db()
        self.assertEqual(imp.status, VibeImportStatus.FAILED)
        self.assertEqual(imp.batches_done + imp.batches_failed, 0)


class ConcurrencyTests(ImportBase):
    def test_never_more_than_three_batches_at_once(self):
        live = peak = 0
        lock = threading.Lock()

        def counting(no, batch, *, work_dir, cancel, batch_id):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.15)
            with lock:
                live -= 1
            return categorize.BatchResult(
                labels={i: {'categories': ['decor'], 'item': 'vase', 'styles': [], 'colors': [],
                            'materials': [], 'description': ''} for i in range(len(batch))},
                attempts=1)

        pins = pins_for('p', 150)  # 10 batches
        with mock.patch.object(categorize, 'categorize_batch', side_effect=counting):
            imp = self.run_import(FakeClient(pins))
        self.assertEqual(imp.batches_total, 10)
        self.assertEqual(imp.batches_done, 10)
        self.assertEqual(peak, 3)
