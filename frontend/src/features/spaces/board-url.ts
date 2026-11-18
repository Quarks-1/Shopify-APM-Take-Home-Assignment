/**
 * Client-side check of a pasted Pinterest board link, mirroring the backend's
 * rules so the form can show an inline error. The backend stays authoritative.
 *
 * An empty (or whitespace-only) string means "no import" and returns `{ url: "" }`.
 */
export type BoardUrlResult = { url: string } | { error: string };

const TLDS = "com|ca|co\\.uk|com\\.au|com\\.mx|de|fr|es|it|pt|ie|nz|ch|at|se|dk|jp";
const PINTEREST_HOST = new RegExp(`^(?:[a-z]{2}\\.|www\\.)?pinterest\\.(?:${TLDS})$`);
const PIN_IT_PATH = /^\/[A-Za-z0-9]+\/?$/;

const INVALID = "That doesn't look like a Pinterest board link.";
const SINGLE_PIN = "That's a link to a single pin. Paste a board link instead.";

export function parseBoardUrl(raw: string): BoardUrlResult {
  let text = raw.trim();
  if (text === "") return { url: "" };
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = `https://${text}`;

  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return { error: INVALID };
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return { error: INVALID };

  const host = url.hostname.toLowerCase();
  if (host === "pin.it") {
    return PIN_IT_PATH.test(url.pathname) ? { url: url.toString() } : { error: INVALID };
  }
  if (!PINTEREST_HOST.test(host)) return { error: INVALID };

  const segments = url.pathname.split("/").filter(Boolean);
  if (segments[0] === "pin") return { error: SINGLE_PIN };
  if (segments.length < 2) return { error: INVALID };
  return { url: url.toString() };
}

/** Readable name for an imported Pinterest board, falling back to its URL slug when untitled. */
export function boardDisplayName(board: { title: string; resolvedUrl?: string; boardUrl: string }): string {
  const trimmed = board.title.trim();
  if (trimmed) return trimmed;
  const raw = board.resolvedUrl || board.boardUrl;
  try {
    const segments = new URL(raw).pathname
      .split("/")
      .filter(Boolean)
      .map((s) => decodeURIComponent(s).replace(/[-_]+/g, " "));
    if (segments.length >= 2) {
      const slug = segments[1].trim();
      if (slug) return slug.charAt(0).toUpperCase() + slug.slice(1);
    }
    if (segments.length === 1) return `pin.it/${segments[0]}`;
  } catch {
    // Fall through to raw URL or generic label.
  }
  return raw || "Pinterest board";
}
