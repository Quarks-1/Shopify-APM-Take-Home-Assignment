#!/usr/bin/env python3
"""Download images from a public Pinterest board (stdlib only, no login)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
IMAGE_SIZE_ORDER = ("orig", "originals", "736x", "564x", "474x", "236x")
FEED_PAUSE_SEC = 0.35
DOWNLOAD_PAUSE_SEC = 0.15
MAX_RETRIES = 4
RETRY_BACKOFF_SEC = 1.5


class BoardError(Exception):
    """Expected failure (private board, bad URL, etc.)."""


class PinterestClient:
    def __init__(self) -> None:
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self.app_version: str | None = None
        self.csrf: str | None = None

    def _csrf_token(self) -> str:
        token = next((c.value for c in self._jar if c.name == "csrftoken"), None)
        if not token:
            raise BoardError("Pinterest did not return a CSRF token (unexpected response).")
        return token

    def get(self, url: str, *, referer: str | None = None) -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        with self._opener.open(req, timeout=60) as resp:
            return resp.read()

    def fetch_text(self, url: str, *, referer: str | None = None) -> str:
        return self.get(url, referer=referer).decode("utf-8", errors="replace")

    def resolve_board_url(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with self._opener.open(req, timeout=60) as resp:
            final = resp.geturl()
        return strip_query(final)

    def load_board_page(self, board_url: str) -> tuple[dict, dict]:
        html = self.fetch_text(board_url, referer="https://www.pinterest.com/")
        self.csrf = self._csrf_token()

        pws_match = re.search(r'<script id="__PWS_DATA__"[^>]*>(.*?)</script>', html)
        if not pws_match:
            raise BoardError("Could not read Pinterest page data for this board.")
        pws = json.loads(pws_match.group(1))
        self.app_version = pws.get("appVersion")
        if not self.app_version:
            raise BoardError("Missing Pinterest app version in board page.")

        redux = None
        for match in re.finditer(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL
        ):
            if '"initialReduxState"' in match.group(1):
                payload = json.loads(match.group(1))
                redux = payload.get("initialReduxState")
                break
        if not redux or not redux.get("boards"):
            raise BoardError("Could not read board metadata from the page.")

        board_id, board = next(iter(redux["boards"].items()))
        return board, {"board_id": board_id, "board": board, "source_url": board.get("url") or path_from_board_url(board_url)}

    def board_feed(self, board_id: str, source_url: str, referer: str, bookmark: str | None = None) -> dict:
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
        handler = handler_from_source_url(source_url)

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "X-Pinterest-AppState": "active",
            "X-APP-VERSION": self.app_version or "",
            "Referer": referer,
            "X-CSRFToken": self.csrf or self._csrf_token(),
            "X-Pinterest-PWS-Handler": handler,
        }
        req = urllib.request.Request(api_url, headers=headers)
        with self._opener.open(req, timeout=60) as resp:
            body = json.loads(resp.read())
        if "resource_response" not in body:
            raise BoardError("Board feed request failed (unexpected API response).")
        return body["resource_response"]


def strip_query(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def path_from_board_url(board_url: str) -> str:
    parsed = urllib.parse.urlparse(board_url)
    path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
    return path


def handler_from_source_url(source_url: str) -> str:
    path = source_url.strip("/")
    return "www/" + path + ("" if path.endswith("/") else "/")


def parse_board_path(board_url: str) -> tuple[str, str]:
    """Return (username, slug) from a canonical board URL."""
    path = urllib.parse.urlparse(board_url).path.strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        raise BoardError(f"Not a board URL: {board_url}")
    return parts[0], parts[1]


def folder_name_for_board(board_url: str) -> str:
    user, slug = parse_board_path(board_url)
    return f"{user}-{slug}"


def first_nonempty_str(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def pick_image_url(images: dict | None) -> str | None:
    if not images:
        return None
    for key in IMAGE_SIZE_ORDER:
        entry = images.get(key)
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    for entry in images.values():
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    return None


def signature_from_image_url(image_url: str) -> str | None:
    name = Path(urllib.parse.urlparse(image_url).path).name
    if not name:
        return None
    base, _dot, _ext = name.partition(".")
    return base or None


def extension_from_image_url(image_url: str) -> str:
    path = urllib.parse.urlparse(image_url).path
    ext = Path(path).suffix.lower().lstrip(".")
    return ext if ext else "jpg"


def pin_is_video(pin: dict) -> bool:
    if pin.get("is_video"):
        return True
    videos = pin.get("videos")
    return isinstance(videos, dict) and bool(videos)


def pin_record_from_api(pin: dict) -> dict:
    pin_id = pin.get("id") or ""
    images = pin.get("images")
    image_url = pick_image_url(images)

    title = first_nonempty_str(pin.get("grid_title"), pin.get("title"))
    description = first_nonempty_str(
        pin.get("description"),
        pin.get("closeup_unified_description"),
        pin.get("grid_description"),
    )
    alt_text = first_nonempty_str(
        pin.get("alt_text"),
        pin.get("auto_alt_text"),
        pin.get("seo_alt_text"),
        pin.get("grid_description"),
    )
    source_url = first_nonempty_str(pin.get("link"))

    return {
        "id": pin_id,
        "url": f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else "",
        "title": title,
        "description": description,
        "alt_text": alt_text,
        "source_url": source_url or None,
        "image_url": image_url,
        "file": None,
        "is_video": pin_is_video(pin),
    }


def download_with_retries(client: PinterestClient, image_url: str, dest: Path, referer: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            req = urllib.request.Request(image_url, headers=headers)
            with client._opener.open(req, timeout=120) as resp:
                status = getattr(resp, "status", 200)
                if status in (429, 500, 502, 503, 504):
                    raise urllib.error.HTTPError(image_url, status, "retryable", resp.headers, resp)
                data = resp.read()
            dest.write_bytes(data)
            return
        except urllib.error.HTTPError as err:
            last_error = err
            if err.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            raise
        except OSError as err:
            last_error = err
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            raise
    if last_error:
        raise last_error


def collect_pins(client: PinterestClient, board_id: str, source_url: str, referer: str) -> list[dict]:
    pins: list[dict] = []
    seen: set[str] = set()
    bookmark: str | None = None

    while True:
        response = client.board_feed(board_id, source_url, referer, bookmark)
        items = response.get("data") or []
        for item in items:
            if item.get("type") != "pin":
                continue
            pin_id = item.get("id")
            if not pin_id or pin_id in seen:
                continue
            seen.add(pin_id)
            pins.append(item)

        bookmark = response.get("bookmark")
        if not bookmark or bookmark == "-end-" or not items:
            break
        time.sleep(FEED_PAUSE_SEC)

    return pins


def ensure_public_board(board: dict) -> None:
    privacy = (board.get("privacy") or "").lower()
    if privacy not in ("public",):
        label = privacy or "unknown"
        raise BoardError(
            f"Board is not public (privacy={label}). "
            "This script only downloads public boards without signing in."
        )


def run_download(board_input_url: str, out_dir: Path) -> dict:
    client = PinterestClient()
    board_url = client.resolve_board_url(board_input_url.strip())
    if "pinterest.com" not in urllib.parse.urlparse(board_url).netloc:
        raise BoardError(f"URL did not resolve to a Pinterest board: {board_input_url}")

    board, meta = client.load_board_page(board_url)
    ensure_public_board(board)

    board_id = meta["board_id"]
    source_url = meta["source_url"] or path_from_board_url(board_url)
    pin_count = board.get("pin_count")
    board_title = board.get("name") or board.get("seo_title") or ""
    folder_name = folder_name_for_board(board_url)
    board_dir = out_dir / folder_name
    images_dir = board_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    api_pins = collect_pins(client, board_id, source_url, board_url)
    manifest_pins: list[dict] = []
    saved = skipped = failed = 0

    for pin in api_pins:
        record = pin_record_from_api(pin)
        image_url = record["image_url"]
        if not image_url:
            manifest_pins.append(record)
            continue

        signature = pin.get("image_signature") or signature_from_image_url(image_url)
        if not signature:
            record["file"] = None
            failed += 1
            manifest_pins.append(record)
            continue

        ext = extension_from_image_url(image_url)
        rel_path = f"images/{signature}.{ext}"
        dest = board_dir / rel_path

        if dest.is_file() and dest.stat().st_size > 0:
            record["file"] = rel_path
            skipped += 1
            manifest_pins.append(record)
            continue

        try:
            download_with_retries(client, image_url, dest, board_url)
            record["file"] = rel_path
            saved += 1
            time.sleep(DOWNLOAD_PAUSE_SEC)
        except (urllib.error.HTTPError, OSError):
            record["file"] = None
            failed += 1

        manifest_pins.append(record)

    manifest = {
        "board_title": board_title,
        "board_url": board_url,
        "pin_count_on_board": pin_count,
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
        "board_title": board_title,
        "board_dir": str(board_dir),
        "pin_count_on_board": pin_count,
        "pins_returned": len(manifest_pins),
        "images_saved": saved,
        "images_skipped": skipped,
        "images_failed": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download images from a public Pinterest board.")
    parser.add_argument("board_url", help="pin.it short link or pinterest.com/{user}/{board} URL")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for board folders")
    args = parser.parse_args()

    try:
        summary = run_download(args.board_url, args.out)
    except BoardError as err:
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