"""Pinterest board imports ("import my vibes").

    start_import(space, url) -> queued VibeImport -> dispatch() -> worker
    (`manage.py run_vibe_import <id>`) -> execute(id) -> dispatch() again.

Same shape as api.searcher.runner: SQLite runs with IMMEDIATE transactions, so
every `transaction.atomic()` block below holds the write lock from its first
statement, and status changes are conditional UPDATEs. The worker is started
detached (own session, so its pid is its process-group id) and told which
database to use through DJANGO_DB_PATH.

execute() runs four steps, each only moving forward:
  reading-board -> downloading -> sorting -> saving
Pins are kept in `SpaceInspiration` rows (uncategorized after step 2, labeled
batch by batch in step 3), so a failed sort never loses the downloaded pins.

A space maps 1:1 to a Pinterest board: once a new import has read its board
successfully, step 2 first prunes every earlier pin of the space that is not on
that board (rows and image files), so only the most recent board's pins remain.
Pins on both boards are kept (no re-download) and re-pointed to the new import.
Nothing is pruned when reading the board fails or the board has no pins.
"""
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from api import claude_cli
from api.models import (
    InspirationSource,
    Space,
    SpaceInspiration,
    VibeImport,
    VibeImportStatus,
    VibeImportStep,
    VoteChoice,
)
from api.searcher import runner

from . import categorize, images, paths, pinterest

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(settings.BASE_DIR)
MANAGE_PY = BACKEND_DIR / 'manage.py'
ACTIVE = (VibeImportStatus.QUEUED, VibeImportStatus.RUNNING)
STALE_NO_PID_SECONDS = 60
DOWNLOAD_PAUSE = 0.05  # seconds between image downloads
PROGRESS_INTERVAL = 0.25  # seconds between download-counter writes
WORKER_DIED_MESSAGE = 'The import stopped unexpectedly. Try again.'
NETWORK_MESSAGE = "We couldn't reach Pinterest. Try again in a bit."
SORT_FAILED_MESSAGE = "We couldn't sort your pins right now. Try again in a bit."
INTERNAL_MESSAGE = 'Something went wrong importing this board.'
CANCELED_MESSAGE = 'The import was stopped.'

_STEP_ORDER = {step: i for i, step in enumerate(VibeImportStep.values)}
_children = []

# Set by the worker's SIGTERM handler (run_vibe_import); checked by execute().
CANCEL = threading.Event()


class ImportInProgress(Exception):
    def __init__(self, active):
        super().__init__('import_in_progress')
        self.active = active


class _Gone(Exception):
    """The import (or its space) disappeared, or was stopped, mid-run."""


class _Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------------ queue

def active_import(space):
    """The space's oldest queued/running import, or None."""
    return VibeImport.objects.filter(space=space, status__in=ACTIVE).order_by('created_at').first()


def start_import(space, board_url):
    """(import, created). Raises pinterest.PinterestError for a bad link and
    ImportInProgress when a different board is already importing."""
    url = pinterest.normalize_input_url(board_url)
    with transaction.atomic():
        active = active_import(space)
        if active is not None:
            if active.board_url == url:
                return active, False
            raise ImportInProgress(active)
        VibeImport.objects.filter(space=space).update(dismissed=True)
        imp = VibeImport.objects.create(space=space, board_url=url)
    dispatch()
    imp.refresh_from_db()
    return imp, True


def _reap_children():
    for proc in list(_children):
        try:
            finished = proc.poll() is not None
        except Exception:
            finished = True
        if finished:
            _children.remove(proc)


def _fail(import_id, code, message):
    """Conditional: only an active import can fail. True when it changed."""
    changed = VibeImport.objects.filter(pk=import_id, status__in=ACTIVE).update(
        status=VibeImportStatus.FAILED, error=message, error_code=code,
        finished_at=timezone.now(),
    )
    return bool(changed)


def reap_stale():
    """Fail running imports whose worker is gone. Returns the reaped ids."""
    reaped = []
    cutoff = timezone.now() - timedelta(seconds=STALE_NO_PID_SECONDS)
    for imp in VibeImport.objects.filter(status=VibeImportStatus.RUNNING).only(
            'pk', 'worker_pid', 'started_at', 'created_at'):
        if imp.worker_pid:
            if runner._pid_alive(imp.worker_pid):
                continue
        elif (imp.started_at or imp.created_at) > cutoff:
            continue
        changed = VibeImport.objects.filter(
            pk=imp.pk, status=VibeImportStatus.RUNNING, worker_pid=imp.worker_pid,
        ).update(status=VibeImportStatus.FAILED, error=WORKER_DIED_MESSAGE,
                 error_code='worker_died', finished_at=timezone.now())
        if changed:
            reaped.append(imp.pk)
    return reaped


def dispatch():
    """Reap stale imports, then promote the oldest queued ones while fewer than
    VIBES_MAX_CONCURRENT are running. Returns the promoted ids."""
    _reap_children()
    reap_stale()
    limit = max(1, settings.VIBES_MAX_CONCURRENT)
    promoted = []
    while True:
        with transaction.atomic():
            if VibeImport.objects.filter(status=VibeImportStatus.RUNNING).count() >= limit:
                break
            nxt = (VibeImport.objects.filter(status=VibeImportStatus.QUEUED)
                   .order_by('created_at', 'pk').only('pk').first())
            if nxt is None:
                break
            changed = VibeImport.objects.filter(pk=nxt.pk, status=VibeImportStatus.QUEUED).update(
                status=VibeImportStatus.RUNNING, step=VibeImportStep.READING_BOARD,
                started_at=timezone.now(), worker_pid=None,
            )
        if not changed:
            continue
        _spawn_worker(nxt.pk)
        promoted.append(nxt.pk)
    return promoted


def _spawn_worker(import_id):
    env = dict(os.environ)
    env['DJANGO_DB_PATH'] = runner.db_path()  # the worker must use this same database
    argv = [sys.executable, str(MANAGE_PY), 'run_vibe_import', str(import_id)]
    try:
        log = paths.import_log_path(import_id)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, 'ab') as fh:
            proc = subprocess.Popen(
                argv, cwd=str(BACKEND_DIR), env=env, shell=False, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
            )
    except OSError as exc:
        logger.error('could not start vibe import worker: %s', exc)
        _fail(import_id, 'internal', INTERNAL_MESSAGE)
        return None
    _children.append(proc)
    VibeImport.objects.filter(pk=import_id).update(worker_pid=proc.pid)
    return proc


def cancel_for_space(space_id):
    """SIGTERM the worker of every running import of the space."""
    pids = VibeImport.objects.filter(
        space_id=space_id, status=VibeImportStatus.RUNNING, worker_pid__isnull=False,
    ).values_list('worker_pid', flat=True)
    for pid in list(pids):
        claude_cli.signal_group(pid, signal.SIGTERM)


# ------------------------------------------------------------------ worker

def _update(import_id, **fields):
    """Update a still-running import; raises _Gone when it no longer is."""
    if CANCEL.is_set():
        raise _Gone()
    if not VibeImport.objects.filter(pk=import_id, status=VibeImportStatus.RUNNING).update(**fields):
        raise _Gone()


def _set_step(import_id, step, **fields):
    """Advance the step, never backwards. Extra fields are saved either way."""
    if CANCEL.is_set():
        raise _Gone()
    with transaction.atomic():
        rows = list(VibeImport.objects.filter(pk=import_id, status=VibeImportStatus.RUNNING)
                    .values_list('step', flat=True))
        if not rows:
            raise _Gone()
        current = rows[0]
        if current is not None and _STEP_ORDER.get(current, -1) >= _STEP_ORDER[step]:
            if fields:
                VibeImport.objects.filter(pk=import_id).update(**fields)
            return False
        VibeImport.objects.filter(pk=import_id, status=VibeImportStatus.RUNNING).update(
            step=step, **fields)
        return True


def _pin_text(record):
    return {'title': record.title, 'description': record.description, 'altText': record.alt_text}


def _read_board(imp, client):
    _set_step(imp.pk, VibeImportStep.READING_BOARD)
    try:
        board, pins = pinterest.fetch_board(
            imp.board_url, max_pins=settings.VIBES_MAX_PINS, client=client)
    except pinterest.PinterestError:
        raise
    except (urllib.error.URLError, OSError):
        raise _Fail('network', NETWORK_MESSAGE) from None
    _update(imp.pk, resolved_url=board.url, board_title=(board.title or '')[:200],
            pins_on_board=board.pin_count, pins_found=len(pins))
    return board, pins


def remove_files(rel_paths):
    """Best-effort delete of stored image files (relative to vibes_dir) and
    their cached crops."""
    for rel in rel_paths:
        if not rel:
            continue
        target = paths.inside_vibes_dir(rel)
        if target is None:
            continue
        for victim in (target, target.with_name(f'{target.stem}{paths.CROP_SUFFIX}')):
            try:
                victim.unlink()
            except OSError:
                pass


def _prune_to_board(imp, pins):
    """A space maps 1:1 to a board: drop the space's pins that are not on the
    board just read, and re-point the shared ones to this import. Called only
    after a successful read, and never for a board without pins."""
    if not pins:
        return
    ranks = {p.id: p.board_rank for p in pins}
    with transaction.atomic():
        mine = SpaceInspiration.objects.filter(space_id=imp.space_id)
        stale = list(mine.exclude(pin_id__in=list(ranks)).values_list('pk', 'image_path'))
        if stale:
            SpaceInspiration.objects.filter(pk__in=[pk for pk, _ in stale]).delete()
        kept = list(mine.filter(pin_id__in=list(ranks)).only('pk', 'pin_id', 'vibe_import_id', 'board_rank'))
        for row in kept:
            if row.vibe_import_id != imp.pk or row.board_rank != ranks[row.pin_id]:
                SpaceInspiration.objects.filter(pk=row.pk).update(
                    vibe_import=imp, board_rank=ranks[row.pin_id])
    remove_files(path for _, path in stale)


def _download(imp, board, pins, client):
    space_id = imp.space_id
    _prune_to_board(imp, pins)
    existing = set(SpaceInspiration.objects.filter(space_id=space_id).values_list('pin_id', flat=True))
    skipped = sum(1 for p in pins if p.id in existing)
    new = len(pins) - skipped
    _set_step(imp.pk, VibeImportStep.DOWNLOADING, pins_skipped=skipped)

    state = {'last': 0.0, 'ok': 0}

    def on_saved(done, total, record):
        if record.file:
            state['ok'] += 1
        now = time.monotonic()
        if done == total or now - state['last'] >= PROGRESS_INTERVAL:
            state['last'] = now
            _update(imp.pk, pins_downloaded=state['ok'], pins_failed=done - state['ok'])
        elif CANCEL.is_set() or not Space.objects.filter(pk=space_id).exists():
            raise _Gone()

    saved, failed = pinterest.download_pins(
        client, board, pins, paths.space_dir(space_id),
        transform=images.normalize_jpeg, skip=lambda r: r.id in existing,
        on_image_saved=on_saved, pause=DOWNLOAD_PAUSE, image_hosts=tuple(settings.VIBES_IMAGE_HOSTS),
        max_bytes=settings.VIBES_IMAGE_MAX_BYTES,
    )
    root = paths.vibes_dir()
    rows = [
        SpaceInspiration(
            space_id=space_id, vibe_import=imp, source=InspirationSource.PINTEREST,
            vote=VoteChoice.UP, pin_id=r.id, pin_url=r.url, image_url=r.image_url or '',
            image_path=str(Path(r.file).relative_to(root)), pin_text=_pin_text(r),
            board_rank=r.board_rank, categories=[], categorized_at=None,
        )
        for r in saved
    ]
    try:
        SpaceInspiration.objects.bulk_create(rows, ignore_conflicts=True)
    except IntegrityError:
        raise _Gone() from None  # the space was deleted under us
    _update(imp.pk, pins_downloaded=len(saved), pins_failed=failed)
    if new > 0 and not saved:
        raise _Fail('network', NETWORK_MESSAGE)


def _save_batch(import_id, batch, result):
    ok = bool(result.labels)
    when = timezone.now()
    with transaction.atomic():
        for idx, label in result.labels.items():
            SpaceInspiration.objects.filter(pk=batch[idx].pk).update(
                categorized_at=when, **categorize.row_fields(label))
        changed = VibeImport.objects.filter(pk=import_id, status=VibeImportStatus.RUNNING).update(
            batches_done=F('batches_done') + (1 if ok else 0),
            batches_failed=F('batches_failed') + (0 if ok else 1),
            pins_categorized=F('pins_categorized') + len(result.labels),
        )
        if not changed:
            raise _Gone()
    if not ok:
        logger.warning('vibe import %s: batch failed: %s', import_id, result.failure)


def _sort(imp):
    todo = []
    for row in SpaceInspiration.objects.filter(
            space_id=imp.space_id, categorized_at__isnull=True).exclude(image_path='').order_by(
            'board_rank', 'pk'):
        file = paths.resolve_inside(row.image_path)
        if file is not None:
            row.file = str(file)
            todo.append(row)
    batches = pinterest.chunk(todo, settings.VIBES_BATCH_SIZE)
    _set_step(imp.pk, VibeImportStep.SORTING, batches_total=len(batches))
    if not batches:
        return
    work_dir = paths.import_dir(imp.pk)
    work_dir.mkdir(parents=True, exist_ok=True)
    cancel = threading.Event()
    stop = False
    with ThreadPoolExecutor(max_workers=max(1, settings.VIBES_BATCH_CONCURRENCY)) as pool:
        futures = {
            pool.submit(categorize.categorize_batch, no, batch, work_dir=work_dir, cancel=cancel,
                        batch_id=f'{imp.pk}-{no}'): batch
            for no, batch in enumerate(batches)
        }
        while futures:
            done, _ = wait(list(futures), timeout=1, return_when=FIRST_COMPLETED)
            if not stop and (CANCEL.is_set() or not VibeImport.objects.filter(
                    pk=imp.pk, status=VibeImportStatus.RUNNING).exists()):
                stop = True
                cancel.set()
            for future in done:
                batch = futures.pop(future)
                if stop:
                    continue
                try:
                    result = future.result()
                except Exception as exc:  # categorize_batch should never raise
                    result = categorize.BatchResult(failure=f'{type(exc).__name__}: {exc}')
                _save_batch(imp.pk, batch, result)
    if stop:
        raise _Gone()


def usable_count(space_id):
    """How many of the space's pins are sorted into at least one category."""
    return sum(1 for cats in SpaceInspiration.objects.filter(
        space_id=space_id, categorized_at__isnull=False).values_list('categories', flat=True)
        if cats)


def _finish(imp):
    _set_step(imp.pk, VibeImportStep.SAVING)
    usable = usable_count(imp.space_id)
    work_dir = paths.import_dir(imp.pk)
    if work_dir.is_dir():
        for entry in work_dir.iterdir():  # keep logs and the CLI's result files
            if entry.is_file() and not entry.name.endswith(('.log', '.out.json')):
                try:
                    entry.unlink()
                except OSError:
                    pass
    cur = VibeImport.objects.filter(pk=imp.pk).values('batches_total', 'batches_done').first()
    if cur is None:
        raise _Gone()
    if cur['batches_total'] == 0 or cur['batches_done'] > 0:
        _update(imp.pk, pins_usable=usable, status=VibeImportStatus.DONE,
                finished_at=timezone.now())
    else:
        _update(imp.pk, pins_usable=usable, status=VibeImportStatus.FAILED,
                error=SORT_FAILED_MESSAGE, error_code='sort_failed', finished_at=timezone.now())


def execute(import_id, client_factory=pinterest.PinterestClient):
    """Run one import to completion in this process. Never raises."""
    space_id = None
    try:
        VibeImport.objects.filter(pk=import_id, status=VibeImportStatus.QUEUED).update(
            status=VibeImportStatus.RUNNING, started_at=timezone.now())
        imp = VibeImport.objects.filter(pk=import_id, status=VibeImportStatus.RUNNING).first()
        if imp is None:
            return
        space_id = imp.space_id
        VibeImport.objects.filter(pk=import_id).update(worker_pid=os.getpid())
        client = client_factory()
        board, pins = _read_board(imp, client)
        _download(imp, board, pins, client)
        _sort(imp)
        _finish(imp)
    except _Gone:
        _after_gone(import_id, space_id)
    except _Fail as exc:
        _fail(import_id, exc.code, exc.message)
    except pinterest.PinterestError as exc:
        _fail(import_id, exc.code, exc.message)
    except Exception:
        logger.exception('vibe import %s crashed', import_id)
        traceback.print_exc(file=sys.stderr)
        _fail(import_id, 'internal', INTERNAL_MESSAGE)
    finally:
        try:
            dispatch()
        except Exception:
            logger.exception('dispatch failed after vibe import %s', import_id)


def _after_gone(import_id, space_id):
    """The import row vanished (space deleted) or the worker was told to stop."""
    if space_id and not Space.objects.filter(pk=space_id).exists():
        shutil.rmtree(paths.space_dir(space_id), ignore_errors=True)  # we may have re-created it
    _fail(import_id, 'canceled', CANCELED_MESSAGE)  # no-op when the row is gone
