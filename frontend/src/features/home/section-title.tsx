import { Counter } from "@/components/base/badges/counter";

export function SectionTitle({
  id,
  title,
  count,
  description,
}: {
  id: string;
  title: string;
  count: number;
  description?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-center gap-2">
        <h2 id={id} className="text-title-3-semibold text-text-primary">
          {title}
        </h2>
        <Counter>{count}</Counter>
      </div>
      {description ? <p className="text-body-regular text-text-secondary">{description}</p> : null}
    </div>
  );
}
