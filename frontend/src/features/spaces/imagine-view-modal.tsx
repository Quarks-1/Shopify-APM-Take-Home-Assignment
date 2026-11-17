import { RiDownload2Line, RiRefreshLine, RiSparkling2Line } from "@remixicon/react";
import { ButtonLink, Button } from "@/components/base/buttons/button";
import { Modal, ModalBody, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { Spinner } from "@/components/base/spinner/spinner";
import type { ApiRender } from "@/lib/api";

export interface ImagineViewModalProps {
  spaceName: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  render: ApiRender | null;
  error: string | null;
  onRegenerate: () => void;
  onRetry: () => void;
}

const ERROR_COPY: Record<string, string> = {
  provider_not_configured: "Image generation isn't set up. Add GEMINI_API_KEY to the backend environment and restart it.",
  no_items: "Add an object to the space first.",
  interrupted: "Generation was interrupted. Try again.",
};

function errorCode(render: ApiRender | null, error: string | null): string | null {
  if (error) return error;
  if (render?.status === "failed") return render.error ?? "failed";
  return null;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ImagineViewModal({
  spaceName,
  isOpen,
  onOpenChange,
  render,
  error,
  onRegenerate,
  onRetry,
}: ImagineViewModalProps) {
  const code = errorCode(render, error);
  const status = code !== null ? "failed" : render?.status === "done" ? "done" : "running";
  const missingPhotos = render?.items.filter((item) => !item.hasPhoto).map((item) => item.name) ?? [];

  return (
    <Modal size="xl" isOpen={isOpen} onOpenChange={onOpenChange}>
      {({ close }) => (
        <>
          <ModalHeader title="Imagine view" description={spaceName} icon={RiSparkling2Line} />
          <ModalBody className="flex flex-col gap-3">
            {status === "running" ? (
              <div className="flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-2xl bg-background-secondary-default text-center">
                <Spinner size="lg" />
                <p className="text-body-medium text-text-primary">Imagining your space…</p>
                <p className="text-caption-1-medium text-text-tertiary">This can take up to a minute.</p>
              </div>
            ) : null}

            {status === "done" && render?.imageUrl ? (
              <>
                <img
                  src={render.imageUrl}
                  alt={`Imagined top-down view of ${spaceName}`}
                  className="max-h-[55vh] mx-auto w-full rounded-2xl border border-separator-border object-contain bg-background-secondary-default"
                />
                <p className="text-caption-1-medium text-text-tertiary">
                  Generated {formatTime(render.createdAt)} · {render.itemCount} items · {render.photoCount} with product photos
                </p>
                {missingPhotos.length > 0 ? (
                  <p className="text-caption-1-medium text-text-tertiary">No product photo: {missingPhotos.join(", ")}</p>
                ) : null}
              </>
            ) : null}

            {status === "failed" ? (
              <div className="rounded-2xl border border-border-error-default bg-background-primary-default p-4">
                <p className="text-body-medium text-text-error-primary">
                  {code !== null && ERROR_COPY[code] ? ERROR_COPY[code] : "Couldn't generate the image."}
                </p>
                {code !== null && !ERROR_COPY[code] ? (
                  <p className="mt-1 text-caption-1-medium text-text-tertiary">{code}</p>
                ) : null}
              </div>
            ) : null}
          </ModalBody>
          <ModalFooter>
            {status === "done" ? (
              <>
                <ButtonLink
                  variant="secondary"
                  leadingIcon={RiDownload2Line}
                  href={render?.imageUrl ?? undefined}
                  download
                >
                  Download
                </ButtonLink>
                <Button leadingIcon={RiRefreshLine} onClick={onRegenerate}>
                  Regenerate
                </Button>
              </>
            ) : null}
            {status === "failed" ? (
              <Button onClick={onRetry}>Try again</Button>
            ) : null}
            {status === "running" ? (
              <Button variant="secondary" onClick={close}>
                Close
              </Button>
            ) : null}
          </ModalFooter>
        </>
      )}
    </Modal>
  );
}
