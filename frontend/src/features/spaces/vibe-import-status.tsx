import { AgentThinking } from "@/components/application/agent-thinking/agent-thinking";
import { CloseButton } from "@/components/base/buttons/close-button";
import { Notification } from "@/components/base/notification/notification";
import { ProgressBar } from "@/components/base/progress/progress";
import { VIBE_IMPORT_STEPS, vibeImportLabel } from "@/mocks/types";
import { cx } from "@/utils/cx";
import { useVibeImport } from "./use-vibe-import";

export function VibeImportStatus({ spaceId }: { spaceId: string }) {
  const vibes = useVibeImport();
  const view = vibes.viewFor(spaceId);

  let content = null;
  if (view && (view.kind === "starting" || view.kind === "active") && !view.hidden) {
    const imp = view.kind === "active" ? view.imp : undefined;
    const step = view.kind === "active" ? view.step : undefined;
    const index = step ? VIBE_IMPORT_STEPS.indexOf(step) + 1 : 1;
    content = (
      <div
        key="vibe-import-running"
        className={cx(
          "relative flex w-full flex-col gap-2 rounded-3xl border border-border-button-default",
          "bg-background-primary-default px-4 py-3 pe-11 shadow-dropdown",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <AgentThinking variant="wave" label={vibeImportLabel(imp, step)} showTimer={false} />
          <span className="text-caption-1-medium text-text-tertiary">
            Step {index} of {VIBE_IMPORT_STEPS.length}
          </span>
        </div>
        {imp && step === "downloading" && imp.pins.new > 0 ? (
          <ProgressBar aria-label="Downloading pins" value={imp.pins.downloaded} maxValue={imp.pins.new} showValueLabel={false} size="sm" />
        ) : null}
        <CloseButton
          size="sm"
          aria-label="Hide import status"
          className="absolute end-3 top-3"
          onClick={() => vibes.hide(spaceId)}
        />
      </div>
    );
  } else if (view?.kind === "done") {
    const { imp } = view;
    const alreadyThere = imp.pins.new === 0 && imp.pins.skipped > 0;
    content = (
      <Notification
        key={`vibe-import-done-${imp.id}`}
        status="success"
        title="Your space's vibes are ready"
        description={alreadyThere ? "This board's pins were already in your space." : `Sorted ${imp.pins.categorized} pins from "${imp.boardTitle}"`}
        autoDismissDuration={5000}
        onDismiss={() => vibes.dismiss(spaceId)}
      />
    );
  } else if (view?.kind === "failed") {
    content = (
      <Notification
        key={`vibe-import-failed-${view.imp?.id ?? "start"}`}
        status="error"
        title="Couldn't import your vibes"
        description={view.message}
        onDismiss={() => vibes.dismiss(spaceId)}
      />
    );
  }

  if (!content) return null;

  return (
    <div className="flex w-full shrink-0 justify-center">
      <div className="w-full max-w-sm">{content}</div>
    </div>
  );
}
