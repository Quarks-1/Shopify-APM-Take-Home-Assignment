export type AppView = "overview" | "editor";

export interface AppRoute {
  view: AppView;
  spaceId: number | null;
}

export function parseAppRoute(hash: string): AppRoute {
  const normalized = hash.replace(/^#/, "").trim();
  if (normalized === "space-editor" || normalized.startsWith("space-editor/")) {
    const segment = normalized.split("/")[1];
    const parsed = segment ? Number(segment) : NaN;
    return {
      view: "editor",
      spaceId: Number.isFinite(parsed) ? parsed : null,
    };
  }
  return { view: "overview", spaceId: null };
}

export function editorHref(spaceId?: number): string {
  return spaceId != null ? `#space-editor/${spaceId}` : "#space-editor";
}

export function overviewHref(): string {
  return "#overview";
}