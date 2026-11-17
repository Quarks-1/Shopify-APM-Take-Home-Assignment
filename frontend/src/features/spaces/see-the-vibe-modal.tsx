import { useEffect } from "react";
import { RiDeleteBinLine } from "@remixicon/react";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { Modal, ModalBody, ModalHeader } from "@/components/base/modal/modal";
import { Tooltip, TooltipTrigger } from "@/components/base/tooltip/tooltip";
import { GROUP_ORDER, GroupIcon, PinCard, groupLabel } from "@/features/home/vibes-inspiration";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import type { ApiInspiration } from "@/lib/api";
import { cx } from "@/utils/cx";

export interface SeeTheVibeModalProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  spaceId: string;
  inspirations: ApiInspiration[];
  onRemove: (id: number) => void;
}

function RemovePinButton({ disabled, onPress }: { disabled: boolean; onPress: () => void }) {
  const button = (
    <button
      type="button"
      aria-label="Remove pin"
      disabled={disabled}
      onClick={onPress}
      className={cx(
        "absolute end-2 top-2 flex size-8 items-center justify-center rounded-full border border-border-button-default bg-background-primary-default shadow-sm",
        "outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
        "pointer-events-none opacity-0 transition-opacity duration-150",
        "group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100",
        "disabled:cursor-not-allowed disabled:bg-background-primary-disabled",
      )}
    >
      <RiDeleteBinLine
        className={cx("size-4", disabled ? "text-foreground-disabled-danger" : "text-foreground-icon-error")}
        aria-hidden
      />
    </button>
  );
  if (!disabled) return button;
  return (
    <TooltipTrigger>
      {button}
      <Tooltip>Import in progress</Tooltip>
    </TooltipTrigger>
  );
}

export function SeeTheVibeModal({ isOpen, onOpenChange, spaceId, inspirations, onRemove }: SeeTheVibeModalProps) {
  const { isActive } = useVibeImport();
  const importing = isActive(spaceId);
  const empty = inspirations.length === 0;

  useEffect(() => {
    if (isOpen && empty) onOpenChange(false);
  }, [isOpen, empty, onOpenChange]);

  const groups = GROUP_ORDER.map((group) => ({
    group,
    pins: inspirations.filter((pin) => pin.primaryType === group),
  })).filter((g) => g.pins.length > 0);

  return (
    <Modal size="xl" isOpen={isOpen} onOpenChange={onOpenChange}>
      <ModalHeader
        title="See the Vibe"
        description={`${inspirations.length} ${inspirations.length === 1 ? "pin" : "pins"} from your Pinterest board`}
      />
      <ModalBody className="flex flex-col gap-6">
        {groups.map(({ group, pins }) => (
          <section key={group} className="flex flex-col gap-3" aria-label={groupLabel(group)}>
            <Carousel
              aria-label={`${groupLabel(group)} pins`}
              showDots={false}
              showArrows
              gap={12}
              header={
                <div className="flex items-center gap-2">
                  <GroupIcon group={group} className="size-4 shrink-0" />
                  <h3 className="text-body-medium text-text-primary">{groupLabel(group)}</h3>
                  <span className="text-caption-1-medium text-text-tertiary">{pins.length}</span>
                </div>
              }
            >
              {pins.map((pin) => (
                <CarouselItem key={pin.id} className="w-40">
                  <div className="group relative">
                    <PinCard pin={pin} />
                    <RemovePinButton disabled={importing} onPress={() => onRemove(pin.id)} />
                  </div>
                </CarouselItem>
              ))}
            </Carousel>
          </section>
        ))}
      </ModalBody>
    </Modal>
  );
}
