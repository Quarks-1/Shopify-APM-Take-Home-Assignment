"""Re-sort a space's or board's imported pins: `manage.py relabel_vibes <space_or_board_id>`.

Runs the same categorize batches as an import's sorting step over the pins
already stored on disk (no Pinterest requests, only the claude CLI) and
rewrites their labels, including patterns and the item box. With
`--patterns-only` nothing is sent to Claude: `patterns` is recomputed from the
stored `model_patterns` and text, so stale text-derived entries drop out.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.models import Space, SpaceInspiration, VibeBoard
from api.vibes import categorize, importer, paths, patterns, pinterest


def _resolve_board_id(target: str):
    space = Space.objects.filter(pk=target).only('pk', 'vibe_board_id').first()
    if space is not None:
        return space.vibe_board_id, True
    try:
        parsed = uuid.UUID(target)
    except ValueError:
        return None, False
    board = VibeBoard.objects.filter(pk=parsed).only('pk').first()
    return (board.pk, True) if board is not None else (None, False)


class Command(BaseCommand):
    help = "Re-label a space's or board's imported pins from their stored images."

    def add_arguments(self, parser):
        parser.add_argument('space_id', help='space id or VibeBoard UUID')
        parser.add_argument('--patterns-only', action='store_true',
                            help='only recompute patterns from the stored text; no Claude run')

    def handle(self, *args, **options):
        target = options['space_id']
        board_id, found = _resolve_board_id(target)
        if not found:
            raise CommandError(f'space or board not found: {target}')
        rows = (
            SpaceInspiration.objects.filter(board_id=board_id)
            if board_id is not None
            else SpaceInspiration.objects.none()
        )
        if options['patterns_only']:
            self._patterns_only(rows)
        else:
            self._relabel(board_id, rows)

    def _patterns_only(self, rows):
        changed = 0
        for row in rows.filter(categorized_at__isnull=False):
            found = patterns.derive_patterns(row)
            if found != list(row.patterns or []):  # model_patterns is the base, not patterns
                SpaceInspiration.objects.filter(pk=row.pk).update(patterns=found)
                changed += 1
        self.stdout.write(f'patterns updated on {changed} pins')

    def _relabel(self, board_id, rows):
        todo, skipped = [], 0
        for row in rows.order_by('board_rank', 'pk'):
            file = paths.resolve_inside(row.image_path) if row.image_path else None
            if file is None:
                skipped += 1
                continue
            row.file = str(file)
            todo.append(row)
        batches = pinterest.chunk(todo, settings.VIBES_BATCH_SIZE)
        batch_id = f'relabel-{uuid.uuid4().hex[:8]}'
        work_dir = paths.imports_dir() / batch_id
        cancel = threading.Event()
        relabeled = failed = 0
        with ThreadPoolExecutor(max_workers=max(1, settings.VIBES_BATCH_CONCURRENCY)) as pool:
            futures = [
                (batch, pool.submit(categorize.categorize_batch, no, batch, work_dir=work_dir,
                                    cancel=cancel, batch_id=f'{batch_id}-{no}'))
                for no, batch in enumerate(batches)
            ]
            for batch, future in futures:
                result = future.result()
                when = timezone.now()
                for idx, label in result.labels.items():
                    SpaceInspiration.objects.filter(pk=batch[idx].pk).update(
                        categorized_at=when, **categorize.row_fields(label))
                relabeled += len(result.labels)
                failed += len(batch) - len(result.labels)
                if result.failure:
                    self.stderr.write(f'batch failed: {result.failure}')
        if board_id is not None:
            VibeBoard.objects.filter(pk=board_id).update(
                pins_usable=importer.usable_count(board_id), updated_at=timezone.now()
            )
        self._cleanup(work_dir)
        self.stdout.write(
            f'relabeled {relabeled} pins, {failed} failed, {skipped} skipped (no image file)')

    @staticmethod
    def _cleanup(work_dir):
        try:
            for entry in work_dir.iterdir():
                if entry.is_file():
                    entry.unlink()
            work_dir.rmdir()
        except OSError:
            pass
