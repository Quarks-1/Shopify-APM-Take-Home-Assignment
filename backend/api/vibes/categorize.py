"""Sort a batch of pins with one headless `claude` run. Never touches the DB."""
import base64
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from api import claude_cli
from api.models import ObjectType
from api.searcher import runner

from . import prompts

logger = logging.getLogger(__name__)

TYPES = prompts.TYPES
FAILURE_LEN = 300


@dataclass
class BatchResult:
    labels: dict = field(default_factory=dict)  # batch-local index -> label dict
    failure: str = ''
    attempts: int = 0


def build_argv():
    return [
        settings.SEARCHER_CLAUDE_BIN, '-p',
        '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
        '--model', settings.VIBES_MODEL,
        '--json-schema', prompts.schema_json(),
        '--system-prompt', prompts.CATEGORIZE_SYSTEM_PROMPT,
        '--tools', '',
        '--permission-mode', 'dontAsk',
        '--strict-mcp-config',
        '--setting-sources', 'project,local',
        '--safe-mode',
        '--max-turns', str(settings.VIBES_MAX_TURNS),
        '--no-session-persistence',
    ]


def _clip(value):
    return str(value or '')[:prompts.PIN_TEXT_LEN]


def build_message(pins):
    """One stream-json user line (bytes). `pins` are objects with `.pin_text`
    ({title, description, altText}) and `.file` (absolute JPEG path); the
    index of a pin is its position in the list."""
    listing = []
    for i, pin in enumerate(pins):
        text = pin.pin_text if isinstance(pin.pin_text, dict) else {}
        listing.append({'index': i, 'title': _clip(text.get('title')),
                        'description': _clip(text.get('description')),
                        'altText': _clip(text.get('altText'))})
    content = [{'type': 'text', 'text': prompts.intro_text(json.dumps(listing, ensure_ascii=False))}]
    for i, pin in enumerate(pins):
        content.append({'type': 'text', 'text': f'Image {i}:'})
        data = Path(pin.file).read_bytes()
        content.append({'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/jpeg',
            'data': base64.b64encode(data).decode('ascii')}})
    line = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content}})
    return (line + '\n').encode('utf-8')


def _clean(value, limit):
    """Model-written text: printable, one line, lowercase, capped."""
    text = ''.join(ch if ch.isprintable() else ' ' for ch in str(value or ''))
    return re.sub(r'\s+', ' ', text).strip().lower()[:limit].strip()


def _clean_list(value, max_items, limit=40):
    out = []
    for entry in value if isinstance(value, list) else []:
        text = _clean(entry, limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def parse_labels(structured, n):
    """Clean `structured_output` into {index: label} for 0 <= index < n.
    Out-of-range and repeated indexes are dropped; categories are filtered to
    known types, deduped and put in canonical order."""
    pins = structured.get('pins') if isinstance(structured, dict) else None
    labels = {}
    for entry in pins if isinstance(pins, list) else []:
        if not isinstance(entry, dict):
            continue
        idx = entry.get('index')
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < n or idx in labels:
            continue
        raw = entry.get('categories')
        raw = {c for c in raw if isinstance(c, str)} if isinstance(raw, list) else set()
        categories = [t for t in TYPES if t in raw]
        labels[idx] = {
            'categories': categories,
            'item': _clean(entry.get('item'), prompts.ITEM_LEN) if categories else '',
            'styles': _clean_list(entry.get('style'), prompts.STYLE_MAX),
            'colors': _clean_list(entry.get('colors'), prompts.COLORS_MAX),
            'materials': _clean_list(entry.get('materials'), prompts.MATERIALS_MAX),
            'description': _clean(entry.get('description'), prompts.DESCRIPTION_LEN),
        }
    return labels


def _failure_text(run, labels, n):
    if run.spawn_error:
        return f'could not start claude: {run.spawn_error}'
    if run.canceled:
        return 'canceled'
    if run.timed_out:
        return f'timed out after {settings.VIBES_BATCH_TIMEOUT:g}s'
    data = run.data
    if run.rc not in (0, None) and not data:
        return f'claude exited with code {run.rc}'
    if not isinstance(data, dict):
        return 'claude returned no result'
    if data.get('is_error'):
        return f"claude reported an error ({data.get('subtype') or 'unknown'})"
    if not isinstance(data.get('structured_output'), dict):
        return f"no structured output ({data.get('subtype') or 'unknown'})"
    if len(labels) * 2 < n:
        return f'labels for only {len(labels)} of {n} pins'
    return ''


def _attempt(batch_no, argv, message, *, work_dir, cancel, batch_id):
    work_dir = Path(work_dir)
    stdin_file = work_dir / f'batch-{batch_no}.stdin.jsonl'
    fd = os.open(stdin_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as fh:
        fh.write(message)
    try:
        return claude_cli.run(
            argv, cwd=runner.agent_cwd(),
            env=claude_cli.base_env({'VIBES_BATCH_ID': batch_id}),
            out_path=work_dir / f'batch-{batch_no}.out.json',
            err_path=work_dir / f'batch-{batch_no}.err.log',
            timeout=settings.VIBES_BATCH_TIMEOUT, stdin_path=stdin_file,
            is_canceled=cancel.is_set,
        )
    finally:
        try:
            stdin_file.unlink()
        except OSError:
            pass


def categorize_batch(batch_no, pins, *, work_dir, cancel: threading.Event, batch_id) -> BatchResult:
    """Label `pins` with one `claude` run, retrying once. Returned labels are
    keyed by index into `pins`. Never raises for CLI problems."""
    result = BatchResult()
    n = len(pins)
    try:
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        argv = build_argv()
        message = build_message(pins)
        for attempt in (1, 2):
            if cancel.is_set():
                result.failure = result.failure or 'canceled'
                break
            result.attempts = attempt
            run = _attempt(batch_no, argv, message, work_dir=work_dir, cancel=cancel,
                           batch_id=batch_id)
            structured = run.data.get('structured_output') if isinstance(run.data, dict) else None
            labels = parse_labels(structured, n)
            failure = _failure_text(run, labels, n)
            if len(labels) >= len(result.labels):
                result.labels = labels
            result.failure = failure[:FAILURE_LEN]
            if not failure or run.canceled:
                break
    except Exception as exc:  # never raise for CLI/setup problems
        logger.exception('categorize batch %s failed', batch_no)
        result.failure = f'{type(exc).__name__}: {exc}'[:FAILURE_LEN]
    return result
