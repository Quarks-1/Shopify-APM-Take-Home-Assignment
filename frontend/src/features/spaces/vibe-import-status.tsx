import { RiCheckboxCircleFill, RiErrorWarningFill } from "@remixicon/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect } from "react";
import { AgentThinking } from "@/components/application/agent-thinking/agent-thinking";
import { CloseButton } from "@/components/base/buttons/close-button";
import { VIBE_IMPORT_STEPS, vibeImportLabel } from "@/mocks/types";
import { cx } from "@/utils/cx";
import { useVibeImport } from "./use-vibe-import";

const AUTO_DISMISS_MS = 5000;

const MOTION = {
  initial: { opacity: 0, scale: 0.96 },
  animate: { opacity: 1, scale: 1 },
  exit: { opacity: 0, scale: 0.96 },
  transition: { duration: 0.15 },
};

/** Compact inline pill for a space or section header's button group. */
export function VibeImportStatus({ spaceId = null }: { spaceId?: string | null }) {
  const vibes = useVibeImport();
  const view = vibes.viewFor(spaceId);
  const { dismiss } = vibes;
  const isSpace = spaceId !== null;

  const doneId = view?.kind === "done" ? view.imp.id : undefined;
  useEffect(() => {
    if (!doneId) return;
    const timer = setTimeout(() => dismiss(spaceId), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [doneId, spaceId, dismiss]);

  let content = null;
  if (view && (view.kind === "starting" || view.kind === "active") && !view.hidden) {
    const imp = view.kind === "active" ? view.imp : undefined;
    const step = view.kind === "active" ? view.step : undefined;
    const index = step ? VIBE_IMPORT_STEPS.indexOf(step) + 1 : 1;
    content = (
      <motion.div
        key="vibe-import-running"
        role="status"
        {...MOTION}
        className="inline-flex h-8 max-w-full min-w-0 items-center gap-2 rounded-full border border-border-button-default bg-background-primary-default ps-3 pe-1"
      >
        <div className="min-w-0 truncate">
          <AgentThinking variant="wave" label={vibeImportLabel(imp, step)} showTimer={false} />
        </div>
        <span className="text-caption-1-medium whitespace-nowrap text-text-tertiary">
          Step {index} of {VIBE_IMPORT_STEPS.length}
        </span>
        <CloseButton size="sm" aria-label="Hide import status" onClick={() => vibes.hide(spaceId)} />
      </motion.div>
    );
  } else if (view?.kind === "done") {
    const { imp } = view;
    const alreadyThere = imp.pins.new === 0 && imp.pins.skipped > 0;
    content = (
      <motion.div
        key={`vibe-import-done-${imp.id}`}
        role="status"
        {...MOTION}
        className="inline-flex h-8 max-w-full items-center gap-2 rounded-full bg-notification-success-background ps-2 pe-1"
      >
        <RiCheckboxCircleFill className="size-5 shrink-0 text-notification-success-foreground" aria-hidden />
        <span className="text-body-medium text-text-primary">
          {isSpace ? "Your space's vibes are ready" : "Your vibes are ready"}
        </span>
        <span className="text-caption-1-medium text-text-secondary">
          {alreadyThere
            ? isSpace
              ? "· already in your space"
              : "· already in your library"
            : `· ${imp.pins.categorized} ${imp.pins.categorized === 1 ? "pin" : "pins"}`}
        </span>
        <CloseButton size="sm" aria-label="Dismiss" onClick={() => dismiss(spaceId)} />
      </motion.div>
    );
  } else if (view?.kind === "failed") {
    content = (
      <motion.div
        key={`vibe-import-failed-${view.imp?.id ?? "start"}`}
        role="alert"
        {...MOTION}
        className={cx(
          "inline-flex min-h-8 max-w-md items-center gap-2 rounded-xl border border-border-error-default",
          "bg-notification-error-background py-1 ps-2 pe-1",
        )}
      >
        <RiErrorWarningFill className="size-5 shrink-0 text-text-error-primary" aria-hidden />
        <span className="min-w-0 text-body-medium text-text-primary">Couldn&apos;t import your vibes: {view.message}</span>
        <CloseButton size="sm" aria-label="Dismiss" className="self-start" onClick={() => dismiss(spaceId)} />
      </motion.div>
    );
  }

  return <AnimatePresence initial={false}>{content}</AnimatePresence>;
}
