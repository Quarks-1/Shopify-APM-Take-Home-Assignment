import { AgentThinking } from "@/components/application/agent-thinking/agent-thinking";
import { SEARCH_STEP_LABELS, type SearchStep } from "@/mocks/types";
import { cx } from "@/utils/cx";

const STEPS: SearchStep[] = ["planning", "building-query", "querying-catalog", "ranking"];

export interface SearchProgressProps {
  step?: SearchStep;
  className?: string;
}

export function SearchProgress({ step = "planning", className }: SearchProgressProps) {
  const index = STEPS.indexOf(step) + 1;
  return (
    <div className={cx("flex flex-wrap items-center gap-x-3 gap-y-1", className)}>
      <AgentThinking variant="wave" label={SEARCH_STEP_LABELS[step]} showTimer={false} />
      <span className="text-caption-1-medium text-text-tertiary">
        Step {index} of {STEPS.length}
      </span>
    </div>
  );
}
