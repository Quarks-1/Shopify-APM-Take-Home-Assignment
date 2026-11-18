/**
 * Client-side check of a pasted Pinterest board link, mirroring the backend's
 * rules so the form can show an inline error. The backend stays authoritative.
 *
 * An empty (or whitespace-only) string means "no import" and returns `{ url: "" }`.
 */
export type BoardUrlResult = { url: string } | { error: string };

const MAX_INPUT_LEN = 2000;
const TLDS =
  "com|ca|co\\.uk|com\\.au|com\\.mx|com\\.br|de|fr|es|it|pt|ie|nz|ch|at|se|dk|" +
  "jp|co\\.kr|nl|no|fi|pl|cl|ph|ru|co|in|id|com\\.co|hu|cz|gr|ro|sk|tr";
const PINTEREST_HOST = new RegExp(`^(?:[a-z]{2,3}\\.)?pinterest\\.(?:${TLDS})$`);
const PIN_IT_PATH = /^\/[A-Za-z0-9]+\/?$/;
const SEGMENT_RE = /^[\w%.~-]+$/;

const RESERVED_FIRST_SEGMENTS = new Set([
  "pin",
  "search",
  "ideas",
  "_",
  "settings",
  "business",
  "today",
  "explore",
  "login",
  "news_hub",
  "about",
]);

const RESERVED_SECOND_SEGMENTS = new Set([
  "_created",
  "_saved",
  "_tools",
  "pins",
  "boards",
  "followers",
  "following",
]);

const INVALID = "That doesn't look like a Pinterest board link.";
const SINGLE_PIN = "That's a link to a single pin. Paste a board link instead.";

function hasUnsafeChars(text: string): boolean {
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code <= 0x20 || code === 0x5c || code === 0x7f) return true;
  }
  return false;
}

export function parseBoardUrl(raw: string): BoardUrlResult {
  let text = raw.trim();
  if (text === "") return { url: "" };
  if (text.length > MAX_INPUT_LEN) return { error: INVALID };
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = `https://${text}`;
  // Reject backslashes, spaces, and control characters before URL parsing percent-encodes them.
  if (text.length > MAX_INPUT_LEN || hasUnsafeChars(text)) return { error: INVALID };

  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return { error: INVALID };
  }
  const host = url.hostname.toLowerCase();
  if (url.protocol !== "https:" || url.username || url.password || url.port || !host || host.endsWith(".")) {
    return { error: INVALID };
  }

  if (host === "pin.it") {
    return PIN_IT_PATH.test(url.pathname)
      ? { url: `https://pin.it/${url.pathname.replace(/^\/+|\/+$/g, "")}` }
      : { error: INVALID };
  }
  if (!PINTEREST_HOST.test(host)) return { error: INVALID };

  const segments = url.pathname.split("/").filter(Boolean);
  if (segments.length === 0) return { error: INVALID };
  const first = segments[0].toLowerCase();
  if (first === "pin") {
    return segments.length >= 2 && SEGMENT_RE.test(segments[1]) ? { error: SINGLE_PIN } : { error: INVALID };
  }
  if (RESERVED_FIRST_SEGMENTS.has(first) || segments.length < 2) return { error: INVALID };

  const [user, board] = segments;
  if (!SEGMENT_RE.test(user) || !SEGMENT_RE.test(board)) return { error: INVALID };
  if (RESERVED_SECOND_SEGMENTS.has(board.toLowerCase()) || board.startsWith("_")) {
    return { error: INVALID };
  }

  return { url: `https://${host}/${user}/${board}/` };
}
