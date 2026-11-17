"""Shared helpers for the vibe import tests: a fake Pinterest client that never
touches the network, synthetic JPEGs and a temp-dir settings mixin."""
import io
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from PIL import Image

from api.models import Space
from api.vibes import importer, pinterest

FIXTURES = Path(__file__).parent
FAKE_CLAUDE = str(FIXTURES / 'fake_claude.py')
BOARD_URL = 'https://www.pinterest.com/someuser/cozy-living/'


def make_space(sid='space1'):
    now = timezone.now()
    return Space.objects.create(
        id=sid, name='Den', type='room', unit='in', dims={'x': 120, 'y': 96, 'z': 96},
        created_at=now, updated_at=now,
    )


def jpeg_bytes(color=(200, 60, 60), size=(64, 48)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='JPEG')
    return buf.getvalue()


def raw_pin(pin_id, title='', **extra):
    pin = {
        'type': 'pin', 'id': pin_id, 'grid_title': title, 'description': f'desc {pin_id}',
        'images': {'736x': {'url': f'https://i.pinimg.com/736x/{pin_id}.jpg'}},
    }
    pin.update(extra)
    return pin


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeImageOpener:
    def __init__(self, client):
        self.client = client

    def open(self, req, timeout=None):
        url = req.full_url
        self.client.image_requests.append(url)
        if self.client.on_image:
            self.client.on_image(len(self.client.image_requests))
        pin_id = url.rsplit('/', 1)[-1].split('.')[0]
        if pin_id in self.client.bad_images:
            return FakeResp(b'not an image')
        return FakeResp(jpeg_bytes())


class FakeClient(pinterest.PinterestClient):
    """A PinterestClient serving a synthetic board; `pins` are raw API pins."""

    def __init__(self, pins, *, title='Cozy living', privacy='public', pin_count=None,
                 bad_images=(), on_load=None, on_image=None, load_error=None):
        super().__init__()
        self.pins = pins
        self.title = title
        self.privacy = privacy
        self.pin_count = pin_count if pin_count is not None else len(pins)
        self.bad_images = set(bad_images)
        self.on_load = on_load
        self.on_image = on_image
        self.load_error = load_error
        self.image_requests = []
        self._image_opener = FakeImageOpener(self)

    def resolve_board_url(self, url):
        return BOARD_URL

    def load_board(self, board_url):
        if self.on_load:
            self.on_load()
        if self.load_error:
            raise self.load_error
        board = pinterest.Board(
            url=board_url, board_id='b1', title=self.title, pin_count=self.pin_count,
            privacy=self.privacy, source_url='/someuser/cozy-living/',
        )
        pinterest.ensure_public_board(board)
        return board

    def board_feed(self, board_id, source_url, referer, bookmark=None):
        return {'data': self.pins, 'bookmark': '-end-'}


def pins_for(prefix, n, marker=''):
    return [raw_pin(f'{prefix}{i:03d}', f'sofa {i} {marker}'.strip()) for i in range(n)]


class TempVibes:
    """Own VIBES_DIR / SEARCHER_VAR_DIR / agent cwd per test, the fake claude, no
    real workers and no pauses between downloads."""

    def setUp(self):
        super().setUp()
        self.var = Path(tempfile.mkdtemp(prefix='vibes-var-'))
        self.addCleanup(shutil.rmtree, self.var, True)
        cm = override_settings(
            SEARCHER_VAR_DIR=self.var, VIBES_DIR=str(self.var / 'vibes'),
            SEARCHER_AGENT_CWD=self.var / 'agent-cwd', SEARCHER_CLAUDE_BIN=FAKE_CLAUDE,
            VIBES_MODEL='haiku', VIBES_BATCH_TIMEOUT=60,
        )
        cm.enable()
        self.addCleanup(cm.disable)
        pause = mock.patch.object(importer, 'DOWNLOAD_PAUSE', 0)
        pause.start()
        self.addCleanup(pause.stop)
        spawn = mock.patch.object(importer, '_spawn_worker', return_value=None)
        self.spawn = spawn.start()
        self.addCleanup(spawn.stop)
        importer.CANCEL.clear()
        self.addCleanup(importer.CANCEL.clear)
