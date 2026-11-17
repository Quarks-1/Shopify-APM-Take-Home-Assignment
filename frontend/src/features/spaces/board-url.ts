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
