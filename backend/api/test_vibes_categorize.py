"""Pin categorizer: argv, message layout, label cleaning and real subprocess
runs against the fake claude (never the real CLI)."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from .models import ObjectType
from .test_fixtures.vibes_helpers import TempVibes, jpeg_bytes
from .vibes import categorize, prompts


def make_pins(tmp, titles):
    pins = []
    for i, title in enumerate(titles):
        path = Path(tmp) / f'{i}.jpg'
        path.write_bytes(jpeg_bytes((i * 20 % 255, 80, 80)))
        pins.append(SimpleNamespace(
            pin_text={'title': title, 'description': f'd{i}', 'altText': 'x' * 500}, file=str(path)))
    return pins


class ArgvTests(SimpleTestCase):
    @override_settings(SEARCHER_CLAUDE_BIN='/bin/claude', VIBES_MODEL='haiku', VIBES_MAX_TURNS=2)
    def test_disables_tools_and_allows_none(self):
        argv = categorize.build_argv()
        self.assertEqual(argv[:2], ['/bin/claude', '-p'])
        i = argv.index('--tools')
        self.assertEqual(argv[i + 1], '')
        self.assertNotIn('--allowedTools', argv)
        for flag in ('--strict-mcp-config', '--safe-mode', '--no-session-persistence', '--verbose'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--setting-sources') + 1], 'project,local')
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(argv[argv.index('--model') + 1], 'haiku')
        self.assertEqual(argv[argv.index('--max-turns') + 1], '2')
        schema = json.loads(argv[argv.index('--json-schema') + 1])
        self.assertFalse(schema['additionalProperties'])
        item = schema['properties']['pins']['items']
        self.assertEqual(item['properties']['categories']['items']['enum'], list(ObjectType.values))
        self.assertEqual(set(item['required']), set(item['properties']))
        self.assertFalse(item['additionalProperties'])
        system = argv[argv.index('--system-prompt') + 1]
        for word in ('furniture', 'lighting', 'rug', 'wall-art', 'storage', 'decor', 'untrusted'):
            self.assertIn(word, system)


class MessageTests(SimpleTestCase):
    def test_layout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pins = make_pins(tmp, ['one', 'two'])
            raw = categorize.build_message(pins)
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(raw.count(b'\n'), 1)
        msg = json.loads(raw)
        self.assertEqual((msg['type'], msg['message']['role']), ('user', 'user'))
        blocks = msg['message']['content']
        self.assertEqual([b['type'] for b in blocks], ['text', 'text', 'image', 'text', 'image'])
        self.assertEqual(blocks[1]['text'], 'Image 0:')
        self.assertEqual(blocks[3]['text'], 'Image 1:')
        self.assertEqual(blocks[2]['source']['media_type'], 'image/jpeg')
        self.assertEqual(blocks[2]['source']['type'], 'base64')
        listing = json.loads(blocks[0]['text'][blocks[0]['text'].index('['):])
        self.assertEqual([p['index'] for p in listing], [0, 1])
        self.assertEqual(listing[0]['title'], 'one')
        self.assertEqual(len(listing[0]['altText']), 200)


def entry(index, **kw):
    base = {'index': index, 'categories': ['decor'], 'item': 'vase', 'style': [], 'colors': [],
            'materials': [], 'description': ''}
    base.update(kw)
    return base


class ParseLabelsTests(SimpleTestCase):
    def test_drops_bad_indexes_and_duplicates(self):
        out = categorize.parse_labels({'pins': [
            entry(0, item='first'), entry(0, item='dupe'), entry(5), entry(-1), entry(True),
            entry('1'), 'junk', entry(2),
        ]}, 3)
        self.assertEqual(sorted(out), [0, 2])
        self.assertEqual(out[0]['item'], 'first')

    def test_categories_filtered_deduped_canonical_order(self):
        out = categorize.parse_labels({'pins': [entry(
            0, categories=['decor', 'lamp', 'furniture', 'decor', 'rug', 7])]}, 1)
        self.assertEqual(out[0]['categories'], ['furniture', 'rug', 'decor'])

    def test_strings_cleaned_and_capped(self):
        out = categorize.parse_labels({'pins': [entry(
            0, item='  Floor\nLamp\x00  ' + 'x' * 100, style=['Boho', 'boho', '', 'A\tB', 'c', 'd', 'e'],
            colors=['Red'] * 3 + ['b', 'c', 'd', 'e'], materials=['Oak', 'Linen', 'x', 'y', 'z'],
            description='A\nnice   room ' + 'z' * 300)]}, 1)[0]
        self.assertTrue(out['item'].startswith('floor lamp'))
        self.assertLessEqual(len(out['item']), 40)
        self.assertEqual(out['styles'], ['boho', 'a b', 'c', 'd'])
        self.assertEqual(out['colors'], ['red', 'b', 'c', 'd', 'e'])
        self.assertEqual(out['materials'], ['oak', 'linen', 'x', 'y'])
        self.assertEqual(len(out['description']), 200)
        self.assertNotIn('\n', out['description'])

    def test_empty_categories_blank_item(self):
        out = categorize.parse_labels({'pins': [entry(0, categories=[], item='sofa')]}, 1)
        self.assertEqual(out[0]['categories'], [])
        self.assertEqual(out[0]['item'], '')

    def test_garbage_input(self):
        for bad in (None, [], {'pins': 'x'}, {'pins': None}, 'x'):
            self.assertEqual(categorize.parse_labels(bad, 3), {})


class FakeClaudeBatchTests(TempVibes, TestCase):
    def setUp(self):
        super().setUp()
        self.work = self.var / 'work'
        self.tmp = self.var / 'img'
        self.tmp.mkdir()

    def batch(self, titles, **kw):
        pins = make_pins(self.tmp, titles)
        return categorize.categorize_batch(
            0, pins, work_dir=self.work, cancel=threading.Event(), batch_id=kw.pop('batch_id', 'b0'),
            **kw)

    def test_labels_from_title_markers(self):
        res = self.batch(['cat=furniture,lighting item=sofa', 'plain', 'cat= item='])
        self.assertEqual(res.failure, '')
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.labels[0]['categories'], ['furniture', 'lighting'])
        self.assertEqual(res.labels[0]['item'], 'sofa')
        self.assertEqual(res.labels[1]['categories'], ['decor'])
        self.assertEqual(res.labels[1]['item'], 'vase')
        self.assertEqual(res.labels[2]['categories'], [])
        self.assertEqual(res.labels[2]['item'], '')
        self.assertEqual(sorted(res.labels), [0, 1, 2])

    def test_runs_outside_repo_with_batch_id_and_no_stdin_left_behind(self):
        self.batch(['a'], batch_id='batch-xyz')
        dump = json.loads((self.var / 'agent-cwd' / 'fake-claude-batch-xyz.json').read_text())
        self.assertEqual(dump['problems'], [])
        self.assertEqual(dump['mode'], 'categorize')
        self.assertEqual(dump['image_types'], ['image/jpeg'])
        self.assertEqual(dump['env']['VIBES_BATCH_ID'], 'batch-xyz')
        self.assertEqual(list(self.work.glob('*.stdin.jsonl')), [])
        self.assertTrue((self.work / 'batch-0.out.json').exists())

    def test_flaky_is_retried_then_ok(self):
        res = self.batch(['fake=flaky cat=rug item=rug', 'b'], batch_id='flaky1')
        self.assertEqual(res.attempts, 2)
        self.assertEqual(res.failure, '')
        self.assertEqual(res.labels[0]['categories'], ['rug'])

    def test_exit1_leaves_pins_uncategorized(self):
        res = self.batch(['fake=exit1', 'b'])
        self.assertEqual(res.labels, {})
        self.assertEqual(res.attempts, 2)
        self.assertIn('exited with code 1', res.failure)

    def test_error_json_and_notjson_fail_softly(self):
        res = self.batch(['fake=error_json'])
        self.assertEqual(res.labels, {})
        self.assertIn('error', res.failure)
        res = self.batch(['fake=notjson'])
        self.assertEqual(res.labels, {})
        self.assertTrue(res.failure)

    def test_slow_run_is_killed_on_timeout(self):
        with override_settings(VIBES_BATCH_TIMEOUT=0.5):
            started = time.monotonic()
            res = self.batch(['fake=slow'], batch_id='slow1')
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(res.labels, {})
        self.assertIn('timed out', res.failure)
        dump = json.loads((self.var / 'agent-cwd' / 'fake-claude-slow1.json').read_text())
        children = dump['children']
        from .test_search_runner import pid_gone
        self.assertTrue(children and all(pid_gone(pid) for pid in children))
        self.assertTrue(pid_gone(dump['pid']))

    def test_missing_binary_does_not_raise(self):
        with override_settings(SEARCHER_CLAUDE_BIN='/nonexistent/claude'):
            res = self.batch(['a'])
        self.assertEqual(res.labels, {})
        self.assertIn('could not start', res.failure)

    def test_cancel_before_start(self):
        pins = make_pins(self.tmp, ['a'])
        cancel = threading.Event()
        cancel.set()
        res = categorize.categorize_batch(0, pins, work_dir=self.work, cancel=cancel, batch_id='c')
        self.assertEqual((res.labels, res.attempts, res.failure), ({}, 0, 'canceled'))

    def test_partial_labels_kept_when_second_attempt_is_no_better(self):
        # 3 pins; the model only returns 1 (< half): retried once, partial labels kept.
        doc = {'type': 'result', 'structured_output': {'pins': [entry(0)]}}
        run = mock.Mock(rc=0, data=doc, timed_out=False, canceled=False, spawn_error=None)
        with mock.patch.object(categorize, '_attempt', return_value=run) as attempt:
            res = self.batch(['a', 'b', 'c'])
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual(sorted(res.labels), [0])
        self.assertIn('labels for only 1 of 3', res.failure)
