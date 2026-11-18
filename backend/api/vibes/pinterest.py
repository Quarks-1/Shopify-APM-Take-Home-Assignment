"""Download pins from a public Pinterest board (stdlib only, no login, no Django).

The server fetches a URL a user typed, so everything here is defensive:
the input link is validated before any request, every redirect hop is
re-checked against an allowlist of Pinterest hosts, image downloads are https
and `i.pinimg.com` only, and every read is byte-capped.

`scripts/download_board.py` is a thin wrapper around `main()`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Callable, Sequence

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
IMAGE_SIZE_ORDER = ("orig", "originals", "736x", "564x", "474x", "236x")
CLI_IMAGE_SIZES = IMAGE_SIZE_ORDER
JOB_IMAGE_SIZES = ("736x", "564x", "474x", "orig", "originals", "236x")
IMAGE_HOSTS = ("i.pinimg.com",)
FEED_PAUSE_SEC = 0.35
DOWNLOAD_PAUSE_SEC = 0.15
MAX_RETRIES = 4
RETRY_BACKOFF_SEC = 1.5
MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_INPUT_LEN = 2000
RETRYABLE_STATUS = (429, 500, 502, 503, 504)

PIN_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")

_TLDS = (
    "com|ca|co\\.uk|com\\.au|com\\.mx|com\\.br|de|fr|es|it|pt|ie|nz|ch|at|se|dk|"
    "jp|co\\.kr|nl|no|fi|pl|cl|ph|ru|co|in|id|com\\.co|hu|cz|gr|ro|sk|tr"
)
PINTEREST_HOST_RE = re.compile(rf"^(?:(?:www|m|[a-z]{{2,3}})\.)?pinterest\.(?:{_TLDS})$")
SHORT_HOST = "pin.it"
API_HOST = "api.pinterest.com"
RESERVED_FIRST_SEGMENTS = frozenset(
    {"pin", "search", "ideas", "_", "settings", "business", "today", "explore", "login", "news_hub"}
)
RESERVED_SECOND_SEGMENTS = frozenset({"_created", "_saved", "_tools", "pins", "boards"})
SEGMENT_RE = re.compile(r"^[\w%.~-]+$")
SHORT_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]+/?$")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


# --- errors -----------------------------------------------------------------


class PinterestError(Exception):
    """Expected failure. `.code` is machine readable, `str(err)` is user facing."""

    code = "pinterest_error"
    default_message = "Pinterest returned something unexpected."

    def __init__(self, message: str | None = None, code: str | None = None) -> None:
        self.message = message or self.default_message
        if code:
            self.code = code
        super().__init__(self.message)


BoardError = PinterestError


class InvalidBoardUrl(PinterestError):
    code = "invalid_url"
    default_message = "That doesn't look like a Pinterest board link."


class PinNotBoard(PinterestError):
    code = "pin_not_board"
    default_message = "That's a link to a single pin. Paste a board link instead."


class PrivateBoard(PinterestError):
    code = "private_board"
    default_message = "This board is private. Make it public on Pinterest and try again."


class BoardNotFound(PinterestError):
    code = "not_found"
    default_message = "We couldn't open this board. If it's secret, make it public and try again."


class EmptyBoard(PinterestError):
    code = "empty_board"
    default_message = "This board has no pins we can use."


# --- url validation ----------------------------------------------------------


def _split_https(url: str) -> urllib.parse.SplitResult:
    """Parse `url`, requiring https, no userinfo, no port. Raises InvalidBoardUrl."""
    if not isinstance(url, str) or len(url) > MAX_INPUT_LEN or "\\" in url:
        raise InvalidBoardUrl()
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        raise InvalidBoardUrl()
    try:
        parts = urllib.parse.urlsplit(url)
        _ = parts.port
    except ValueError:
        raise InvalidBoardUrl() from None
    if parts.scheme != "https":
        raise InvalidBoardUrl()
    netloc = parts.netloc
    if not netloc or "@" in netloc or ":" in netloc or netloc.endswith("."):
        raise InvalidBoardUrl()
    return parts


def is_allowed_host(host: str) -> bool:
    host = (host or "").lower()
    return host in (SHORT_HOST, API_HOST) or bool(PINTEREST_HOST_RE.match(host))


def _check_hop(url: str) -> None:
    """Raise InvalidBoardUrl unless `url` is https on an allowed Pinterest host."""
    parts = _split_https(url)
    if not is_allowed_host(parts.hostname or ""):
        raise InvalidBoardUrl()


def _board_segments(path: str) -> tuple[str, str]:
    """(user, board) from a pinterest.* path; raises PinNotBoard / InvalidBoardUrl."""
    segments = [s for s in path.split("/") if s]
    if not segments:
        raise InvalidBoardUrl()
    first = segments[0].lower()
    if first == "pin":
        if len(segments) >= 2 and SEGMENT_RE.match(segments[1]):
            raise PinNotBoard()
        raise InvalidBoardUrl()
    if first in RESERVED_FIRST_SEGMENTS or len(segments) < 2:
        raise InvalidBoardUrl()
    user, board = segments[0], segments[1]
    if not SEGMENT_RE.match(user) or not SEGMENT_RE.match(board):
        raise InvalidBoardUrl()
    if board.lower() in RESERVED_SECOND_SEGMENTS or board.startswith("_"):
        raise InvalidBoardUrl()
    return user, board


def normalize_input_url(raw: str) -> str:
    """Validate a user-typed board link without touching the network.

    Returns `https://pin.it/<id>` or `https://<pinterest host>/<user>/<board>/`
    (query and fragment dropped). Raises InvalidBoardUrl or PinNotBoard.
    """
    if not isinstance(raw, str):
        raise InvalidBoardUrl()
    text = raw.strip()
    if not text or len(text) > MAX_INPUT_LEN:
        raise InvalidBoardUrl()
    if not _SCHEME_RE.match(text):
        text = "https://" + text
    parts = _split_https(text)
    host = (parts.hostname or "").lower()
    if host == SHORT_HOST:
        if not SHORT_PATH_RE.match(parts.path):
            raise InvalidBoardUrl()
        return f"https://{SHORT_HOST}/{parts.path.strip('/')}"
    if not PINTEREST_HOST_RE.match(host):
        raise InvalidBoardUrl()
    user, board = _board_segments(parts.path)
    return f"https://{host}/{user}/{board}/"


# --- redirects and reads -----------------------------------------------------


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect hop must pass `check(newurl)` (which raises to refuse)."""

    max_redirections = MAX_REDIRECTS

    def __init__(self, check: Callable[[str], None]) -> None:
        self.check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_capped(resp, max_bytes: int) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise PinterestError("That response was too large to read.", code="too_large")
    return data


def _check_image_url(url: str, image_hosts: Sequence[str]) -> None:
    try:
        parts = _split_https(url)
    except InvalidBoardUrl:
        raise PinterestError("Refused a non-https image URL.", code="bad_image_url") from None
    if (parts.hostname or "").lower() not in image_hosts:
        raise PinterestError("Refused an image from an unexpected host.", code="bad_image_url")


class PinterestClient:
    def __init__(self, timeout: float = 20, image_timeout: float = 60) -> None:
        self.timeout = timeout
        self.image_timeout = image_timeout
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar), _AllowedRedirects(_check_hop)
        )
        self._image_hosts: tuple[str, ...] = IMAGE_HOSTS
        self._image_opener = urllib.request.build_opener(
            _AllowedRedirects(lambda url: _check_image_url(url, self._image_hosts))
        )
        self.app_version: str | None = None
        self.csrf: str | None = None

    def _csrf_token(self) -> str:
        token = next((c.value for c in self._jar if c.name == "csrftoken"), None)
        if not token:
            raise PinterestError("Pinterest did not return a CSRF token (unexpected response).")
        return token

    def _open_page(self, req: urllib.request.Request):
        _check_hop(req.full_url)
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as err:
            if err.code in (403, 404, 410):
                raise BoardNotFound() from None
            raise

    def get(self, url: str, *, referer: str | None = None) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        if referer:
            headers["Referer"] = referer
        with self._open_page(urllib.request.Request(url, headers=headers)) as resp:
            return _read_capped(resp, MAX_PAGE_BYTES)

    def fetch_text(self, url: str, *, referer: str | None = None) -> str:
        return self.get(url, referer=referer).decode("utf-8", errors="replace")

    def resolve_board_url(self, url: str) -> str:
        """Validate `url`, follow redirects within Pinterest, return the canonical
        `https://www.pinterest.com/<user>/<board>/`."""
        start = normalize_input_url(url)
        req = urllib.request.Request(start, headers={"User-Agent": USER_AGENT})
        with self._open_page(req) as resp:
            final = resp.geturl()
        parts = _split_https(final)
        host = (parts.hostname or "").lower()
        if not PINTEREST_HOST_RE.match(host):
            raise InvalidBoardUrl()
        user, board = _board_segments(parts.path)
        return f"https://www.pinterest.com/{user}/{board}/"

    def load_board(self, board_url: str) -> "Board":
        html = self.fetch_text(board_url, referer="https://www.pinterest.com/")
        try:
            self.csrf = self._csrf_token()
        except PinterestError:
            raise BoardNotFound() from None

        pws_match = re.search(r'<script id="__PWS_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not pws_match:
            raise BoardNotFound()
        try:
            pws = json.loads(pws_match.group(1))
        except ValueError:
            raise BoardNotFound() from None
        self.app_version = pws.get("appVersion")
        if not self.app_version:
            raise BoardNotFound()

        redux = None
        for match in re.finditer(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL
        ):
            if '"initialReduxState"' in match.group(1):
                try:
                    redux = json.loads(match.group(1)).get("initialReduxState")
                except ValueError:
                    redux = None
                break
        boards = redux.get("boards") if isinstance(redux, dict) else None
        if not boards:
            raise BoardNotFound()

        board_id, data = next(iter(boards.items()))
        if not isinstance(data, dict):
            raise BoardNotFound()
        board = Board(
            url=board_url,
            board_id=str(board_id),
            title=first_nonempty_str(data.get("name"), data.get("seo_title")),
            pin_count=data.get("pin_count"),
            privacy=(data.get("privacy") or "").lower(),
            source_url=data.get("url") or path_from_board_url(board_url),
        )
        ensure_public_board(board)
        return board

    def board_feed(
        self, board_id: str, source_url: str, referer: str, bookmark: str | None = None
    ) -> dict:
        options: dict = {
            "board_id": board_id,
            "field_set_key": "react_grid_pin",
            "page_size": 25,
            "add_vase": True,
            "filter_section_pins": False,
            "gated": True,
            "is_react": True,
            "prepend": False,
            "redux_normalize_feed": True,
        }
        if bookmark:
            options["bookmarks"] = [bookmark]

        query = urllib.parse.urlencode(
            {
                "source_url": source_url,
                "data": json.dumps({"options": options, "context": {}}),
            }
        )
        api_url = f"https://www.pinterest.com/resource/BoardFeedResource/get/?{query}"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "X-Pinterest-AppState": "active",
            "X-APP-VERSION": self.app_version or "",
            "Referer": referer,
            "X-CSRFToken": self.csrf or self._csrf_token(),
            "X-Pinterest-PWS-Handler": handler_from_source_url(source_url),
        }
        with self._open_page(urllib.request.Request(api_url, headers=headers)) as resp:
            raw = _read_capped(resp, MAX_PAGE_BYTES)
        try:
            body = json.loads(raw)
        except ValueError:
            raise PinterestError("Board feed request failed (unexpected API response).") from None
        if not isinstance(body, dict) or "resource_response" not in body:
            raise PinterestError("Board feed request failed (unexpected API response).")
        return body["resource_response"]


# --- data --------------------------------------------------------------------


@dataclass
class Board:
    url: str
    board_id: str
    title: str
    pin_count: int | None
    privacy: str
    source_url: str


@dataclass
class PinRecord:
    id: str
    url: str
    title: str
    description: str
    alt_text: str
    source_url: str | None
    image_url: str | None
    is_video: bool
    board_rank: int
    file: str | None = None


def path_from_board_url(board_url: str) -> str:
    path = urllib.parse.urlparse(board_url).path
    return path if path.endswith("/") else path + "/"


def handler_from_source_url(source_url: str) -> str:
    path = source_url.strip("/")
    return "www/" + path + ("" if path.endswith("/") else "/")


def parse_board_path(board_url: str) -> tuple[str, str]:
    """Return (username, slug) from a canonical board URL."""
    parts = urllib.parse.urlparse(board_url).path.strip("/").split("/")
    if len(parts) < 2:
        raise InvalidBoardUrl()
    return parts[0], parts[1]


def folder_name_for_board(board_url: str) -> str:
    user, slug = parse_board_path(board_url)
    return f"{user}-{slug}"


def first_nonempty_str(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def ensure_public_board(board: Board | dict) -> None:
    privacy = board.privacy if isinstance(board, Board) else (board.get("privacy") or "")
    if (privacy or "").lower() != "public":
        raise PrivateBoard()


def _pick_from_images(images: object, sizes: Sequence[str]) -> str | None:
    if not isinstance(images, dict) or not images:
        return None
    for key in sizes:
        entry = images.get(key)
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    for entry in images.values():
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    return None


def pick_image_url(pin: dict, sizes: Sequence[str] = JOB_IMAGE_SIZES) -> str | None:
    """Best image URL for a pin: `images` by size order, then any `images` entry,
    then a video cover, then a story pin page image. None if nothing usable."""
    url = _pick_from_images(pin.get("images"), sizes)
    if url:
        return url

    videos = pin.get("videos")
    video_list = videos.get("video_list") if isinstance(videos, dict) else None
    if isinstance(video_list, dict):
        for entry in video_list.values():
            if isinstance(entry, dict) and entry.get("thumbnail"):
                return entry["thumbnail"]

    story = pin.get("story_pin_data")
    pages = story.get("pages") if isinstance(story, dict) else None
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            candidates = [page.get("image")]
            blocks = page.get("blocks")
            if isinstance(blocks, list):
                candidates.extend(b.get("image") for b in blocks if isinstance(b, dict))
            for cand in candidates:
                if isinstance(cand, dict):
                    url = _pick_from_images(cand.get("images"), sizes)
                    if url:
                        return url
    return None


def signature_from_image_url(image_url: str) -> str | None:
    name = Path(urllib.parse.urlparse(image_url).path).name
    if not name:
        return None
    base, _dot, _ext = name.partition(".")
    return base or None


def extension_from_image_url(image_url: str) -> str:
    ext = Path(urllib.parse.urlparse(image_url).path).suffix.lower().lstrip(".")
    return ext if re.fullmatch(r"[a-z0-9]{1,5}", ext or "") else "jpg"


def pin_is_video(pin: dict) -> bool:
    if pin.get("is_video"):
        return True
    videos = pin.get("videos")
    return isinstance(videos, dict) and bool(videos)


def pin_to_record(pin: dict, rank: int, sizes: Sequence[str] = JOB_IMAGE_SIZES) -> PinRecord | None:
    """PinRecord for an API pin, or None when its id is unusable. `image_url`
    is None when the pin has no usable image."""
    pin_id = pin.get("id")
    if not isinstance(pin_id, str) or not PIN_ID_RE.match(pin_id):
        return None
    source = first_nonempty_str(pin.get("link"))
    return PinRecord(
        id=pin_id,
        url=f"https://www.pinterest.com/pin/{pin_id}/",
        title=first_nonempty_str(pin.get("grid_title"), pin.get("title")),
        description=first_nonempty_str(
            pin.get("description"),
            pin.get("closeup_unified_description"),
            pin.get("grid_description"),
        ),
        alt_text=first_nonempty_str(
            pin.get("alt_text"),
            pin.get("auto_alt_text"),
            pin.get("seo_alt_text"),
            pin.get("grid_description"),
        ),
        source_url=source or None,
        image_url=pick_image_url(pin, sizes),
        is_video=pin_is_video(pin),
        board_rank=rank,
    )


def collect_pins(client: PinterestClient, board: Board, max_pins: int | None = None) -> list[dict]:
    """Raw API pins in feed order, deduped by id. Stops paging at `max_pins`."""
    pins: list[dict] = []
    seen: set[str] = set()
    bookmark: str | None = None

    while True:
        response = client.board_feed(board.board_id, board.source_url, board.url, bookmark)
        items = response.get("data") or []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "pin":
                continue
            pin_id = item.get("id")
            if not pin_id or pin_id in seen:
                continue
            seen.add(pin_id)
            pins.append(item)

        if max_pins is not None and len(pins) >= max_pins:
            return pins[:max_pins]
        bookmark = response.get("bookmark")
        if not bookmark or bookmark == "-end-" or not items:
            break
        time.sleep(FEED_PAUSE_SEC)

    return pins


def fetch_board(
    input_url: str,
    *,
    max_pins: int | None = 150,
    image_sizes: Sequence[str] = JOB_IMAGE_SIZES,
    client: PinterestClient | None = None,
    on_pins_found: Callable[[Board, int], None] | None = None,
) -> tuple[Board, list[PinRecord]]:
    """Resolve, load and page a board. Pins without an id or image are dropped.
    Raises EmptyBoard when nothing usable is left."""
    client = client or PinterestClient()
    board = client.load_board(client.resolve_board_url(input_url))
    raw = collect_pins(client, board, max_pins)
    records: list[PinRecord] = []
    for rank, pin in enumerate(raw):
        record = pin_to_record(pin, rank, image_sizes)
        if record is not None and record.image_url:
            records.append(record)
    if not records:
        raise EmptyBoard()
    if on_pins_found:
        on_pins_found(board, len(records))
    return board, records


# --- downloads ---------------------------------------------------------------


def download_image(
    client: PinterestClient,
    url: str,
    *,
    referer: str,
    image_hosts: Sequence[str] = IMAGE_HOSTS,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> bytes:
    """Fetch one image: https, exact host match, redirects re-checked, byte capped.
    Retries 429/5xx and network errors with backoff."""
    hosts = tuple(h.lower() for h in image_hosts)
    _check_image_url(url, hosts)
    client._image_hosts = hosts
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    for attempt in range(MAX_RETRIES):
        last = attempt == MAX_RETRIES - 1
        try:
            req = urllib.request.Request(url, headers=headers)
            with client._image_opener.open(req, timeout=client.image_timeout) as resp:
                return _read_capped(resp, max_bytes)
        except urllib.error.HTTPError as err:
            if err.code in RETRYABLE_STATUS and not last:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            raise
        except PinterestError:
            raise
        except OSError:
            if not last:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            raise
    raise PinterestError("Image download failed.")  # pragma: no cover


def _write_atomic(dest: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def download_pins(
    client: PinterestClient,
    board: Board,
    pins: Sequence[PinRecord],
    dest_dir: str | Path,
    *,
    transform: Callable[[bytes], tuple[bytes, str]] | None = None,
    skip: Callable[[PinRecord], bool] | None = None,
    on_image_saved: Callable[[int, int, PinRecord], None] | None = None,
    pause: float = 0.05,
    image_hosts: Sequence[str] = IMAGE_HOSTS,
    max_bytes: int = MAX_IMAGE_BYTES,
    name_for: Callable[[PinRecord], str] | None = None,
) -> tuple[list[PinRecord], int]:
    """Download each pin's image into `dest_dir` as `<pin_id>.<ext>` (or `name_for(pin)`).

    Returns `(saved, failed)`: the records saved successfully (`record.file`
    set to the absolute path) and the number of pins that failed. Pins for
    which `skip(pin)` is true are neither saved nor failed. Failures never
    raise. `on_image_saved(done, total, record)` runs after each attempt
    (success or failure); `total` excludes skipped pins.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    todo = [p for p in pins if not (skip and skip(p))]
    saved: list[PinRecord] = []
    failed = 0
    for done, record in enumerate(todo, start=1):
        try:
            if not record.image_url:
                raise PinterestError("No image for this pin.", code="no_image")
            data = download_image(
                client,
                record.image_url,
                referer=board.url,
                image_hosts=image_hosts,
                max_bytes=max_bytes,
            )
            if transform is not None:
                data, ext = transform(data)
            else:
                ext = extension_from_image_url(record.image_url)
            name = name_for(record) if name_for else f"{record.id}.{ext}"
            path = dest / name
            _write_atomic(path, data)
            record.file = str(path.resolve())
            saved.append(record)
        except Exception:
            failed += 1
        if on_image_saved:
            on_image_saved(done, len(todo), record)
        if pause and record.file:
            time.sleep(pause)
    return saved, failed


def chunk(items: Sequence, size: int = 15) -> list[list]:
    if size < 1:
        raise ValueError("size must be >= 1")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


# --- CLI ---------------------------------------------------------------------


def run_download(board_input_url: str, out_dir: Path, *, max_pins: int | None = None) -> dict:
    client = PinterestClient()
    board = client.load_board(client.resolve_board_url(board_input_url))
    raw = collect_pins(client, board, max_pins)

    board_dir = Path(out_dir) / folder_name_for_board(board.url)
    images_dir = board_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    records: list[PinRecord] = []
    names: dict[str, str] = {}
    for rank, pin in enumerate(raw):
        record = pin_to_record(pin, rank, CLI_IMAGE_SIZES)
        if record is None:
            continue
        records.append(record)
        if record.image_url:
            sig = (
                pin.get("image_signature")
                if isinstance(pin.get("image_signature"), str)
                and re.fullmatch(r"[0-9A-Za-z_-]{1,128}", pin["image_signature"])
                else None
            ) or signature_from_image_url(record.image_url)
            if not sig or not re.fullmatch(r"[0-9A-Za-z_-]{1,128}", sig):
                sig = record.id
            names[record.id] = f"{sig}.{extension_from_image_url(record.image_url)}"

    skipped = 0

    def already_there(record: PinRecord) -> bool:
        nonlocal skipped
        name = names.get(record.id)
        if not name:
            return True  # no image: nothing to download
        existing = images_dir / name
        if existing.is_file() and existing.stat().st_size > 0:
            record.file = str(existing.resolve())
            skipped += 1
            return True
        return False

    _saved, failed = download_pins(
        client,
        board,
        records,
        images_dir,
        skip=already_there,
        pause=DOWNLOAD_PAUSE_SEC,
        max_bytes=32 * 1024 * 1024,
        name_for=lambda r: names[r.id],
    )
    saved = len(_saved)

    manifest_pins = []
    for r in records:
        manifest_pins.append(
            {
                "id": r.id,
                "url": r.url,
                "title": r.title,
                "description": r.description,
                "alt_text": r.alt_text,
                "source_url": r.source_url,
                "image_url": r.image_url,
                "file": f"images/{Path(r.file).name}" if r.file else None,
                "is_video": r.is_video,
            }
        )
    manifest = {
        "board_title": board.title,
        "board_url": board.url,
        "pin_count_on_board": board.pin_count,
        "pins_returned": len(manifest_pins),
        "images_saved": saved,
        "images_skipped": skipped,
        "images_failed": failed,
        "pins": manifest_pins,
    }
    (board_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "board_title": board.title,
        "board_dir": str(board_dir),
        "pin_count_on_board": board.pin_count,
        "pins_returned": len(manifest_pins),
        "images_saved": saved,
        "images_skipped": skipped,
        "images_failed": failed,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download images from a public Pinterest board.")
    parser.add_argument("board_url", help="pin.it short link or pinterest.com/{user}/{board} URL")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for board folders")
    parser.add_argument("--max-pins", type=int, default=None, help="Stop after this many pins")
    args = parser.parse_args(argv)
    if args.max_pins is not None and args.max_pins < 1:
        parser.error("--max-pins must be at least 1")

    try:
        summary = run_download(args.board_url, args.out, max_pins=args.max_pins)
    except PinterestError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as err:
        print(f"error: HTTP {err.code} while fetching Pinterest ({err.reason})", file=sys.stderr)
        return 1
    except urllib.error.URLError as err:
        print(f"error: network failure ({err.reason})", file=sys.stderr)
        return 1

    print(f"Board: {summary['board_title']}")
    print(f"Folder: {summary['board_dir']}")
    print(f"Pins on board: {summary['pin_count_on_board']}")
    print(f"Pins returned: {summary['pins_returned']}")
    print(f"Images saved: {summary['images_saved']}")
    print(f"Images skipped: {summary['images_skipped']}")
    print(f"Images failed: {summary['images_failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
