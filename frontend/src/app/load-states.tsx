import { RiErrorWarningLine, RiRefreshLine, RiWifiOffLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Skeleton, SkeletonText } from "@/components/base/skeleton/skeleton";
import { ApiError, describeError } from "@/lib/api";
import { cx } from "@/utils/cx";

const CARD = "rounded-3xl border border-border-button-default bg-background-primary-default";

/** Placeholder page while the first load from the backend runs. */
export function AppLoadingView({ className }: { className?: string }) {
  return (
    <div aria-busy className={cx("flex flex-col gap-6", className)}>
      <span role="status" className="sr-only">
        Loading your spaces…
      </span>
      <div className="flex flex-col gap-2">
        <Skeleton shape="text" className="h-8 w-56" />
        <Skeleton shape="text" className="h-4 w-80 max-w-full" />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className={cx(CARD, "flex flex-col gap-3 p-3")}>
            <Skeleton className="aspect-[4/3] w-full rounded-2xl" />
            <SkeletonText lines={2} className="px-1 pb-1" />
          </div>
        ))}
      </div>
    </div>
  );
}

export interface AppErrorViewProps {
  error: unknown;
  onRetry: () => void;
  className?: string;
}

/** The first load failed. Never falls back to mock data: the backend is the source of truth. */
export function AppErrorView({ error, onRetry, className }: AppErrorViewProps) {
  const unreachable = !(error instanceof ApiError) || error.isUnreachable;
  const Icon = unreachable ? RiWifiOffLine : RiErrorWarningLine;
  return (
    <section
      role="alert"
      className={cx(CARD, "flex flex-col items-center gap-4 px-6 py-12 text-center", className)}
    >
      <span className="flex size-12 items-center justify-center rounded-full bg-background-tertiary-default">
        <Icon aria-hidden className="size-6 text-foreground-icon-secondary" />
      </span>
      <div className="flex max-w-md flex-col gap-1">
        <h1 className="text-title-3-semibold text-text-primary">
          {unreachable ? "Can't reach the backend" : "Couldn't load your spaces"}
        </h1>
        <p className="text-body-regular text-text-secondary">
          {unreachable ? "Start it with" : "Check its log, or restart it with"}{" "}
          <code
            dir="ltr"
            className="rounded-md bg-background-secondary-default px-1.5 py-0.5 font-mono text-body-2-medium text-text-primary"
          >
            ./start.sh
          </code>{" "}
          from the project folder, then retry.
        </p>
        <p className="text-caption-1-medium text-text-tertiary">{describeError(error)}</p>
      </div>
      <Button leadingIcon={RiRefreshLine} onClick={onRetry}>
        Retry
      </Button>
    </section>
  );
}
