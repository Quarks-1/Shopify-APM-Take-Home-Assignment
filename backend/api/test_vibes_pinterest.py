"""Pinterest board module: URL validation, redirects, paging, image picking and
downloads. No network: the client's openers are replaced by a fake that serves
canned responses (and applies the real redirect handler on 3xx)."""
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import Cookie
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from .vibes import pinterest as pin

FIXTURES = Path(__file__).parent / 'test_fixtures' / 'pinterest'
REPO = Path(__file__).resolve().parents[2]
JPG = b'\xff\xd8\xff\xe0' + b'\x01' * 64
BOARD_URL = 'https://www.pinterest.com/someuser/cozy-living/'


def fixture(name):
    return (FIXTURES / name).read_bytes()


class FakeResp:
    def __init__(self, body, url):
        self._body = body
        self._url = url
        self.status = 200
        self.headers = {}

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """`route(url)` returns bytes (200), an int (HTTP error) or ('redirect', url)."""

    def __init__(self, route, handler, jar=None):
        self.route = route
        self.handler = handler
        self.jar = jar
        self.requests = []

    def open(self, req, timeout=None):
        for _hop in range(10):
            url = req.full_url
            self.requests.append(url)
            result = self.route(url)
            if isinstance(result, tuple):
                req = self.handler.redirect_request(req, None, 302, 'Found', {}, result[1])
                continue
            if isinstance(result, int):
                raise urllib.error.HTTPError(url, result, 'error', {}, None)
            if self.jar is not None and b'__PWS_DATA__' in result:
                self.jar.set_cookie(Cookie(0, 'csrftoken', 'tok', None, False, 'www.pinterest.com',
                                           True, False, '/', True, True, None, False, None, None, {}))
            return FakeResp(result, url)
        raise AssertionError('redirect loop')


def make_client(page_route, image_route=None):
    client = pin.PinterestClient()
    page_handler = next(h for h in client._opener.handlers if isinstance(h, pin._AllowedRedirects))
    image_handler = next(h for h in client._image_opener.handlers if isinstance(h, pin._AllowedRedirects))
    client._opener = FakeOpener(page_route, page_handler, client._jar)
    client._image_opener = FakeOpener(image_route or (lambda url: JPG), image_handler)
    return client


def board_route(url):
    host = urllib.parse.urlsplit(url).hostname
    if host == 'pin.it':
        return ('redirect', 'https://api.pinterest.com/url_shortener/x/redirect/')
    if host == 'api.pinterest.com':
        return ('redirect', BOARD_URL + '?invite_code=zzz&sfo=1')
    if 'BoardFeedResource' in url:
        data = json.loads(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['data'][0])
        return fixture('feed_2.json' if data['options'].get('bookmarks') else 'feed_1.json')
    return fixture('board.html')


class UrlValidationTests(SimpleTestCase):
    ACCEPT = {
        'https://www.pinterest.com/someuser/cozy-living/': 'https://www.pinterest.com/someuser/cozy-living/',
        'pinterest.com/someuser/cozy-living': 'https://pinterest.com/someuser/cozy-living/',
        '  https://uk.pinterest.com/someuser/cozy-living/?x=1#frag  ': 'https://uk.pinterest.com/someuser/cozy-living/',
        'https://www.pinterest.co.uk/someuser/cozy-living/': 'https://www.pinterest.co.uk/someuser/cozy-living/',
        'https://www.pinterest.de/someuser/cozy-living/more/': 'https://www.pinterest.de/someuser/cozy-living/',
        'https://pin.it/1s1rrkzfA': 'https://pin.it/1s1rrkzfA',
        'pin.it/AbC123/': 'https://pin.it/AbC123',
        'https://m.pinterest.com/someuser/cozy-living/': 'https://m.pinterest.com/someuser/cozy-living/',
        'https://pin.it/AbC_12-3/': 'https://pin.it/AbC_12-3',
    }
    REJECT = {
        'http://www.pinterest.com/someuser/cozy-living/': pin.InvalidBoardUrl,
        'https://pinterest.com.evil.net/someuser/cozy-living/': pin.InvalidBoardUrl,
        'https://evilpinterest.com/someuser/cozy-living/': pin.InvalidBoardUrl,
        'https://pinterest.com@evil.net/someuser/cozy-living/': pin.InvalidBoardUrl,
        'https://www.pinterest.com@evil.net/a/b/': pin.InvalidBoardUrl,
        'https://user:pw@www.pinterest.com/a/b/': pin.InvalidBoardUrl,
        'https://www.pinterest.com:8443/a/b/': pin.InvalidBoardUrl,
        'https://www.pinterest.com./a/b/': pin.InvalidBoardUrl,
        'https://www.pinterest.xyz/a/b/': pin.InvalidBoardUrl,
        'https://pin.it.evil.net/abc': pin.InvalidBoardUrl,
        'https://pin.it/abc/def': pin.InvalidBoardUrl,
        'https://pin.it/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/pin/123456/': pin.PinNotBoard,
        'https://www.pinterest.com/pin/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/someuser/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/search/pins/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/ideas/home/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/_/_/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/settings/account/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/someuser/_created/': pin.InvalidBoardUrl,
        'ftp://www.pinterest.com/a/b/': pin.InvalidBoardUrl,
        'javascript:alert(1)': pin.InvalidBoardUrl,
        'https://www.pinterest.com/a/b c/': pin.InvalidBoardUrl,
        'https://www.pinterest.com/a/b\x00/': pin.InvalidBoardUrl,
        '': pin.InvalidBoardUrl,
        '   ': pin.InvalidBoardUrl,
        'not a board': pin.InvalidBoardUrl,
        'https://www.pinterest.com/' + 'a' * 2100 + '/b/': pin.InvalidBoardUrl,
    }

    def test_accepted(self):
        for raw, expected in self.ACCEPT.items():
            with self.subTest(raw=raw):
                self.assertEqual(pin.normalize_input_url(raw), expected)

    def test_rejected(self):
        for raw, error in self.REJECT.items():
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(error):
                    pin.normalize_input_url(raw)

    def test_messages_and_codes(self):
        cases = [
            (pin.InvalidBoardUrl, 'invalid_url', "That doesn't look like a Pinterest board link."),
            (pin.PinNotBoard, 'pin_not_board', "That's a link to a single pin. Paste a board link instead."),
            (pin.PrivateBoard, 'private_board', 'This board is private. Make it public on Pinterest and try again.'),
            (pin.BoardNotFound, 'not_found', "We couldn't open this board. If it's secret, make it public and try again."),
            (pin.EmptyBoard, 'empty_board', 'This board has no pins we can use.'),
        ]
        for cls, code, message in cases:
            err = cls()
            self.assertEqual((err.code, err.message, str(err)), (code, message, message))
            self.assertIsInstance(err, pin.PinterestError)
        self.assertIs(pin.BoardError, pin.PinterestError)

    def test_rejected_input_makes_no_network_calls(self):
        client = make_client(lambda url: self.fail(f'unexpected request {url}'))
        for raw in self.REJECT:
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(pin.PinterestError):
                    client.resolve_board_url(raw)
                with self.assertRaises(pin.PinterestError):
                    pin.fetch_board(raw, client=client)
        self.assertEqual(client._opener.requests, [])


class ResolveAndLoadTests(SimpleTestCase):
    def test_short_link_chain_resolves_to_canonical_board(self):
        client = make_client(board_route)
        self.assertEqual(client.resolve_board_url('https://pin.it/1s1rrkzfA'), BOARD_URL)
        self.assertEqual(client._opener.requests, [
            'https://pin.it/1s1rrkzfA',
            'https://api.pinterest.com/url_shortener/x/redirect/',
            BOARD_URL + '?invite_code=zzz&sfo=1',
        ])

    def test_redirect_to_other_host_is_refused_and_never_requested(self):
        def route(url):
            if 'pin.it' in url:
                return ('redirect', 'https://evil.example.com/someuser/cozy-living/')
            self.fail(f'unexpected request {url}')
        client = make_client(route)
        with self.assertRaises(pin.InvalidBoardUrl):
            client.resolve_board_url('https://pin.it/abc')
        self.assertEqual(client._opener.requests, ['https://pin.it/abc'])

    def test_redirect_to_lookalike_or_http_is_refused(self):
        for target in ('https://pinterest.com.evil.net/a/b/', 'http://www.pinterest.com/a/b/'):
            client = make_client(lambda url, t=target: ('redirect', t) if 'pin.it' in url else self.fail(url))
            with self.subTest(target=target), self.assertRaises(pin.InvalidBoardUrl):
                client.resolve_board_url('https://pin.it/abc')
            self.assertEqual(len(client._opener.requests), 1)

    def test_redirect_to_a_pin_is_not_a_board(self):
        def route(url):
            if 'pin.it' in url:
                return ('redirect', 'https://www.pinterest.com/pin/12345/')
            return fixture('board.html')
        with self.assertRaises(pin.PinNotBoard):
            make_client(route).resolve_board_url('https://pin.it/abc')

    def test_redirect_to_short_host_itself_is_invalid(self):
        client = make_client(lambda url: fixture('board.html'))
        # a short link that does not redirect stays on pin.it: not a board
        with self.assertRaises(pin.InvalidBoardUrl):
            client.resolve_board_url('https://pin.it/abc')

    def test_load_board(self):
        client = make_client(board_route)
        board = client.load_board(BOARD_URL)
        self.assertEqual((board.board_id, board.title, board.pin_count, board.privacy),
                         ('555', 'Cozy Living', 50, 'public'))
        self.assertEqual(board.source_url, '/someuser/cozy-living/')

    def test_private_board(self):
        client = make_client(lambda url: fixture('board_secret.html'))
        with self.assertRaises(pin.PrivateBoard) as ctx:
            client.load_board(BOARD_URL)
        self.assertEqual(str(ctx.exception),
                         'This board is private. Make it public on Pinterest and try again.')

    def test_missing_board_data_and_http_errors_are_not_found(self):
        client = make_client(lambda url: b'<html>nothing here</html>')
        with self.assertRaises(pin.BoardNotFound):
            client.load_board(BOARD_URL)
        for status in (403, 404):
            client = make_client(lambda url, s=status: s)
            with self.subTest(status=status), self.assertRaises(pin.BoardNotFound):
                client.load_board(BOARD_URL)

    def test_page_reads_are_byte_capped(self):
        client = make_client(lambda url: b'x' * (pin.MAX_PAGE_BYTES + 1))
        with self.assertRaises(pin.PinterestError) as ctx:
            client.fetch_text(BOARD_URL)
        self.assertEqual(ctx.exception.code, 'too_large')


class FeedTests(SimpleTestCase):
    def setUp(self):
        sleep = mock.patch.object(pin.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def feed_requests(self, client):
        return [u for u in client._opener.requests if 'BoardFeedResource' in u]

    def test_max_pins_stops_paging_and_keeps_order(self):
        client = make_client(board_route)
        board = client.load_board(BOARD_URL)
        pins = pin.collect_pins(client, board, max_pins=30)
        self.assertEqual(len(self.feed_requests(client)), 2)
        self.assertEqual(len(pins), 30)
        ids = [p['id'] for p in pins]
        self.assertEqual(ids[:2], ['1000001', '1000002'])
        self.assertEqual(ids[-1], '1000030')
        self.assertEqual(len(set(ids)), 30)

    def test_small_max_pins_needs_one_request(self):
        client = make_client(board_route)
        board = client.load_board(BOARD_URL)
        self.assertEqual(len(pin.collect_pins(client, board, max_pins=10)), 10)
        self.assertEqual(len(self.feed_requests(client)), 1)

    def test_no_max_pages_to_the_end_and_dedupes(self):
        client = make_client(board_route)
        board = client.load_board(BOARD_URL)
        pins = pin.collect_pins(client, board)
        self.assertEqual(len(self.feed_requests(client)), 2)
        self.assertEqual(len(pins), 50)

    def test_fetch_board_filters_and_ranks(self):
        client = make_client(board_route)
        found = []
        board, records = pin.fetch_board('https://pin.it/1s1rrkzfA', max_pins=30, client=client,
                                         on_pins_found=lambda b, n: found.append((b.title, n)))
        # 30 raw pins: one has an unusable id, one has no image
        self.assertEqual(len(records), 28)
        self.assertEqual(found, [('Cozy Living', 28)])
        ids = [r.id for r in records]
        self.assertNotIn('1000004', ids)
        self.assertNotIn('bad id/../x', ids)
        self.assertEqual(records[0].board_rank, 0)
        self.assertEqual(records[0].image_url, 'https://i.pinimg.com/736x/aa/bb/1000001.jpg')
        self.assertTrue(all(pin.PIN_ID_RE.match(i) for i in ids))
        self.assertEqual(ids, sorted(ids, key=lambda i: int(i)))
        video = next(r for r in records if r.id == '1000003')
        self.assertTrue(video.is_video)
        self.assertIn('vid3.0000000.jpg', video.image_url)
        self.assertIsNone(records[0].file)
        self.assertEqual(records[0].url, 'https://www.pinterest.com/pin/1000001/')

    def test_empty_board(self):
        def route(url):
            if 'BoardFeedResource' in url:
                return json.dumps({'resource_response': {'data': [], 'bookmark': '-end-'}}).encode()
            return fixture('board.html')
        with self.assertRaises(pin.EmptyBoard):
            pin.fetch_board(BOARD_URL, client=make_client(route))


class PickImageTests(SimpleTestCase):
    IMAGES = {
        '236x': {'url': 'https://i.pinimg.com/236x/a.jpg'},
        '474x': {'url': 'https://i.pinimg.com/474x/a.jpg'},
        '736x': {'url': 'https://i.pinimg.com/736x/a.jpg'},
        'orig': {'url': 'https://i.pinimg.com/originals/a.jpg'},
    }

    def test_job_prefers_mid_size_cli_prefers_orig(self):
        p = {'images': self.IMAGES}
        self.assertEqual(pin.pick_image_url(p, pin.JOB_IMAGE_SIZES), 'https://i.pinimg.com/736x/a.jpg')
        self.assertEqual(pin.pick_image_url(p, pin.CLI_IMAGE_SIZES), 'https://i.pinimg.com/originals/a.jpg')
        self.assertEqual(pin.CLI_IMAGE_SIZES, pin.IMAGE_SIZE_ORDER)

    def test_falls_back_to_any_entry(self):
        p = {'images': {'weird': {'url': 'https://i.pinimg.com/w.jpg'}}}
        self.assertEqual(pin.pick_image_url(p, pin.JOB_IMAGE_SIZES), 'https://i.pinimg.com/w.jpg')

    def test_video_cover(self):
        p = {'videos': {'video_list': {'V_720P': {'url': 'https://v/x.mp4', 'thumbnail': 'https://i.pinimg.com/t.jpg'}}}}
        self.assertEqual(pin.pick_image_url(p, pin.JOB_IMAGE_SIZES), 'https://i.pinimg.com/t.jpg')

    def test_story_pin_page_image(self):
        p = {'story_pin_data': {'pages': [{'blocks': [{'image': {'images': self.IMAGES}}]}]}}
        self.assertEqual(pin.pick_image_url(p, pin.JOB_IMAGE_SIZES), 'https://i.pinimg.com/736x/a.jpg')

    def test_nothing_usable(self):
        self.assertIsNone(pin.pick_image_url({}, pin.JOB_IMAGE_SIZES))
        self.assertIsNone(pin.pick_image_url({'images': {'x': {}}, 'videos': {}}, pin.JOB_IMAGE_SIZES))


class DownloadTests(SimpleTestCase):
    def setUp(self):
        sleep = mock.patch.object(pin.time, 'sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / 'out'
        self.board = pin.Board(BOARD_URL, '555', 'Cozy', 3, 'public', '/someuser/cozy-living/')

    def record(self, n, url=None, rank=0):
        return pin.PinRecord(id=str(n), url=f'https://www.pinterest.com/pin/{n}/', title='', description='',
                             alt_text='', source_url=None,
                             image_url=url or f'https://i.pinimg.com/736x/aa/{n}.jpg', is_video=False,
                             board_rank=rank)

    def test_download_image_rejects_bad_scheme_and_host(self):
        client = make_client(board_route, lambda url: self.fail(f'unexpected request {url}'))
        for url in ('http://i.pinimg.com/a.jpg', 'https://evil.com/a.jpg',
                    'https://i.pinimg.com.evil.com/a.jpg', 'https://evil.i.pinimg.com/a.jpg',
                    'https://i.pinimg.com:444/a.jpg', 'https://u@i.pinimg.com/a.jpg', 'file:///etc/passwd'):
            with self.subTest(url=url), self.assertRaises(pin.PinterestError) as ctx:
                pin.download_image(client, url, referer=BOARD_URL)
            self.assertEqual(ctx.exception.code, 'bad_image_url')
        self.assertEqual(client._image_opener.requests, [])

    def test_image_redirect_to_other_host_is_refused(self):
        def route(url):
            if 'i.pinimg.com' in url:
                return ('redirect', 'https://evil.com/a.jpg')
            self.fail(f'unexpected request {url}')
        client = make_client(board_route, route)
        with self.assertRaises(pin.PinterestError):
            pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL)
        self.assertEqual(client._image_opener.requests, ['https://i.pinimg.com/a.jpg'])

    def test_custom_image_hosts(self):
        client = make_client(board_route)
        self.assertEqual(pin.download_image(client, 'https://img.example.com/a.jpg', referer=BOARD_URL,
                                            image_hosts=('img.example.com',)), JPG)
        with self.assertRaises(pin.PinterestError):
            pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL,
                               image_hosts=('img.example.com',))

    def test_byte_cap(self):
        client = make_client(board_route, lambda url: b'x' * 101)
        with self.assertRaises(pin.PinterestError) as ctx:
            pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL, max_bytes=100)
        self.assertEqual(ctx.exception.code, 'too_large')
        self.assertEqual(len(client._image_opener.requests), 1)  # not retried
        client = make_client(board_route, lambda url: b'x' * 100)
        self.assertEqual(len(pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL,
                                                max_bytes=100)), 100)

    def test_429_is_retried(self):
        calls = []

        def route(url):
            calls.append(url)
            return 429 if len(calls) < 3 else JPG
        client = make_client(board_route, route)
        self.assertEqual(pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL), JPG)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_retries_are_bounded_and_404_is_not_retried(self):
        client = make_client(board_route, lambda url: 503)
        with self.assertRaises(urllib.error.HTTPError):
            pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL)
        self.assertEqual(len(client._image_opener.requests), pin.MAX_RETRIES)
        client = make_client(board_route, lambda url: 404)
        with self.assertRaises(urllib.error.HTTPError):
            pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL)
        self.assertEqual(len(client._image_opener.requests), 1)

    def test_network_errors_are_retried(self):
        calls = []

        def route(url):
            calls.append(url)
            if len(calls) == 1:
                raise ConnectionResetError('boom')
            return JPG
        client = make_client(board_route, route)
        self.assertEqual(pin.download_image(client, 'https://i.pinimg.com/a.jpg', referer=BOARD_URL), JPG)

    def test_download_pins_saves_counts_and_never_raises(self):
        def route(url):
            if url.endswith('/2.jpg'):
                return 404
            if url.endswith('/3.jpg'):
                return b'x' * 500
            return JPG
        client = make_client(board_route, route)
        pins = [self.record(1), self.record(2), self.record(3), self.record(4, url='https://evil.com/4.jpg'),
                self.record(5), self.record(6)]
        progress = []
        saved, failed = pin.download_pins(
            client, self.board, pins, self.dir, max_bytes=100, skip=lambda p: p.id == '6',
            on_image_saved=lambda done, total, rec: progress.append((done, total, rec.id)))
        self.assertEqual([r.id for r in saved], ['1', '5'])
        self.assertEqual(failed, 3)
        self.assertEqual(progress, [(1, 5, '1'), (2, 5, '2'), (3, 5, '3'), (4, 5, '4'), (5, 5, '5')])
        self.assertEqual(saved[0].file, str((self.dir / '1.jpg').resolve()))
        self.assertTrue(Path(saved[0].file).is_absolute())
        self.assertEqual((self.dir / '1.jpg').read_bytes(), JPG)
        self.assertIsNone(pins[1].file)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['1.jpg', '5.jpg'])  # no temp leftovers

    def test_transform_and_failure_in_transform(self):
        client = make_client(board_route)

        def transform(data):
            if data == JPG and transform.fail:
                raise ValueError('bad image')
            return b'T' + data, 'png'
        transform.fail = True
        saved, failed = pin.download_pins(client, self.board, [self.record(1)], self.dir, transform=transform)
        self.assertEqual((saved, failed), ([], 1))
        transform.fail = False
        saved, failed = pin.download_pins(client, self.board, [self.record(1)], self.dir, transform=transform)
        self.assertEqual(failed, 0)
        self.assertEqual(Path(saved[0].file).name, '1.png')
        self.assertEqual(Path(saved[0].file).read_bytes(), b'T' + JPG)

    def test_chunk(self):
        batches = pin.chunk(list(range(35)), 15)
        self.assertEqual([len(b) for b in batches], [15, 15, 5])
        self.assertEqual([x for b in batches for x in b], list(range(35)))
        self.assertEqual([len(b) for b in pin.chunk(list(range(35)))], [15, 15, 5])
        self.assertEqual(pin.chunk([], 15), [])
        with self.assertRaises(ValueError):
            pin.chunk([1], 0)


class ScriptTests(SimpleTestCase):
    def test_script_help_runs_standalone_from_another_cwd(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-I', str(REPO / 'scripts' / 'download_board.py'), '--help'],
                                    cwd=cwd, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--max-pins', result.stdout)

    def test_cli_rejects_bad_url_without_network(self):
        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(pin.urllib.request.OpenerDirector, 'open', side_effect=AssertionError('network')):
            with mock.patch('sys.stderr') as err:
                code = pin.main(['https://pinterest.com.evil.net/a/b/', '--out', out])
        self.assertEqual(code, 1)
        printed = ''.join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("error: That doesn't look like a Pinterest board link.", printed)
