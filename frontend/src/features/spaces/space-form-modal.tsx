import { useEffect, useRef, useState } from "react";
import { RiPinterestLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Modal, ModalBody, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { NumberField } from "@/components/base/number-field/number-field";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { listSpaceVibeImports } from "@/lib/api";
import type { ApiVibeImport } from "@/lib/api";
import { fromDisplayLength, fromDisplayLengthKeeping, toDisplayLength } from "@/lib/format";
import { parseBoardUrl } from "@/features/spaces/board-url";
import { clearedVibeSpaces } from "@/features/spaces/use-space-inspirations";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { SPACE_TYPE_OPTIONS } from "@/mocks/types";
import type { LengthUnit, Space, SpaceType } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { footprint, getObject, getSpace } from "@/state/selectors";

export interface SpaceFormModalProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  spaceId?: string;
  pinCount?: number;
  onClearVibes?: () => void;
  onSaved?: (id: string) => void;
  onCancel?: () => void;
}

interface FormValues {
  name: string;
  type: SpaceType;
  unit: LengthUnit;
  /** Display-unit values; undefined while the field is empty. */
  x?: number;
  y?: number;
  z?: number;
  weightLimit?: number;
  /** Pinterest board link to import as the space's vibes; empty for none. */
  boardUrl: string;
}

function initialValues(space: Space | undefined): FormValues {
  if (!space) return { name: "Untitled space", type: "floor", unit: "in", x: 120, y: 96, boardUrl: "" };
  return {
    name: space.name,
    type: space.type,
    unit: space.unit,
    x: toDisplayLength(space.dims.x, space.unit),
    y: toDisplayLength(space.dims.y, space.unit),
    z: space.dims.z === undefined ? undefined : toDisplayLength(space.dims.z, space.unit),
    weightLimit: space.weightLimit,
    boardUrl: "",
  };
}

function finite(n: number | undefined): number | undefined {
  return n === undefined || Number.isNaN(n) ? undefined : n;
}

export function SpaceFormModal(props: SpaceFormModalProps) {
  const savedRef = useRef(false);
  // The form remounts each time the dialog opens, so it starts from fresh values.
  return (
    <Modal
      size="lg"
      isOpen={props.isOpen}
      onOpenChange={(open) => {
        if (open) savedRef.current = false;
        else if (!savedRef.current) props.onCancel?.();
        props.onOpenChange(open);
      }}
    >
      {({ close }) => <SpaceForm {...props} close={close} savedRef={savedRef} />}
    </Modal>
  );
}

function SpaceForm({
  spaceId,
  pinCount,
  onClearVibes,
  onSaved,
  close,
  savedRef,
}: SpaceFormModalProps & { close: () => void; savedRef: { current: boolean } }) {
  const state = useAppState();
  const actions = useAppActions();
  const vibes = useVibeImport();
  const existing = spaceId ? getSpace(state, spaceId) : undefined;
  const isEdit = existing !== undefined;

  const [values, setValues] = useState<FormValues>(() => initialValues(existing));
  const [submitted, setSubmitted] = useState(false);
  const [savedBoard, setSavedBoard] = useState<ApiVibeImport | null>(null);

  const existingId = existing?.id;
  useEffect(() => {
    if (existingId === undefined || clearedVibeSpaces.has(existingId)) return;
    let cancelled = false;
    listSpaceVibeImports(existingId)
      .then((imports) => {
        if (cancelled || clearedVibeSpaces.has(existingId)) return;
        const current = imports.find((i) => i.status === "done" || i.status === "running" || i.status === "queued");
        if (!current) return;
        setSavedBoard(current);
        // Only prefill when the user hasn't started typing.
        setValues((v) => (v.boardUrl === "" ? { ...v, boardUrl: current.boardUrl } : v));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [existingId]);

  const patch = (next: Partial<FormValues>) => setValues((v) => ({ ...v, ...next }));
  const isWall = values.type === "wall";
  const unitSuffix = values.unit;

  const trimmedName = values.name.trim();
  const nameError =
    trimmedName === ""
      ? "Enter a name."
      : trimmedName.length > 120
        ? "Name must be 120 characters or fewer."
        : null;
  const xError = (finite(values.x) ?? 0) > 0 ? null : "Must be greater than 0.";
  const yError = (finite(values.y) ?? 0) > 0 ? null : "Must be greater than 0.";
  const boardResult = parseBoardUrl(values.boardUrl);
  const boardUrlError = "error" in boardResult ? boardResult.error : null;
  const activeImport = existing !== undefined && vibes.isActive(existing.id);
  const savedBoardResult = savedBoard ? parseBoardUrl(savedBoard.boardUrl) : null;
  const savedBoardUrl = savedBoardResult && "url" in savedBoardResult ? savedBoardResult.url : "";
  const currentUrl = "url" in boardResult ? boardResult.url : "";
  const matchesSaved = savedBoard !== null && currentUrl !== "" && currentUrl === savedBoardUrl;
  const displayPinCount = pinCount ?? savedBoard?.pins.usable ?? 0;
  const valid = !nameError && !xError && !yError && !boardUrlError;

  const changeUnit = (unit: LengthUnit) => {
    if (unit === values.unit) return;
    // Convert the displayed numbers so the physical size stays the same.
    const convert = (n: number | undefined) =>
      n === undefined ? undefined : toDisplayLength(fromDisplayLength(n, values.unit), unit);
    patch({ unit, x: convert(values.x), y: convert(values.y), z: convert(values.z) });
  };

  const save = () => {
    setSubmitted(true);
    if (!valid) return;
    const x = fromDisplayLengthKeeping(finite(values.x) ?? 0, values.unit, existing?.dims.x);
    const y = fromDisplayLengthKeeping(finite(values.y) ?? 0, values.unit, existing?.dims.y);
    const zRaw = finite(values.z);
    const z = zRaw !== undefined && zRaw > 0 ? fromDisplayLengthKeeping(zRaw, values.unit, existing?.dims.z) : undefined;
    const limit = finite(values.weightLimit);
    const weightLimit = limit !== undefined && limit > 0 ? limit : undefined;
    const base = {
      name: trimmedName,
      type: values.type,
      unit: values.unit,
      dims: z === undefined ? { x, y } : { x, y, z },
      weightLimit,
    };
    let id: string;
    if (existing) {
      actions.updateSpace(existing.id, base);
      if (x < existing.dims.x || y < existing.dims.y || values.type !== existing.type) {
        for (const p of existing.placements) {
          const obj = getObject(state, p.objectId);
          if (!obj) continue;
          const size = footprint(obj, { type: values.type });
          const maxX = Math.max(0, x - size.w);
          const maxY = Math.max(0, y - size.h);
          const clampedX = Math.min(Math.max(0, p.x), maxX);
          const clampedY = Math.min(Math.max(0, p.y), maxY);
          if (clampedX !== p.x || clampedY !== p.y) {
            actions.moveObject(existing.id, p.objectId, { x: clampedX, y: clampedY });
          }
        }
      }
      id = existing.id;
    } else {
      id = actions.createSpace(base);
    }
    const shouldStartImport =
      "url" in boardResult &&
      boardResult.url !== "" &&
      (boardResult.url !== savedBoardUrl || (matchesSaved && pinCount === 0));
    if (!activeImport && shouldStartImport) {
      clearedVibeSpaces.delete(id);
      vibes.start(id, boardResult.url);
    } else if (!activeImport && savedBoard !== null && "url" in boardResult && boardResult.url === "") {
      vibes.dismiss(id);
      onClearVibes?.();
    }
    savedRef.current = true;
    close();
    onSaved?.(id);
  };

  return (
    <form
      className="flex min-h-0 flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
      noValidate
    >
      <ModalHeader
        title={isEdit ? "Space settings" : "Define space"}
        description="X × Y is the plane you design on. Add a height or depth if it matters for fit."
      />
      <ModalBody className="flex flex-col gap-4 pb-1">
        <Input
          label="Name"
          isRequired
          autoFocus
          value={values.name}
          onChange={(name) => patch({ name })}
          isInvalid={(submitted || trimmedName.length > 120) && nameError !== null}
          errorMessage={nameError ?? undefined}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <span className="text-body-medium text-text-primary">Type</span>
            <SegmentedControl
              aria-label="Space type"
              selectedKeys={[values.type]}
              onSelectionChange={(keys) => {
                const next = [...keys][0];
                if (next) patch({ type: String(next) as SpaceType });
              }}
            >
              {SPACE_TYPE_OPTIONS.map((o) => (
                <SegmentedControlItem key={o.value} id={o.value}>
                  {o.label}
                </SegmentedControlItem>
              ))}
            </SegmentedControl>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-body-medium text-text-primary">Unit</span>
            <SegmentedControl
              aria-label="Unit"
              selectedKeys={[values.unit]}
              onSelectionChange={(keys) => {
                const next = [...keys][0];
                if (next) changeUnit(String(next) as LengthUnit);
              }}
            >
              <SegmentedControlItem id="in">in</SegmentedControlItem>
              <SegmentedControlItem id="ft">ft</SegmentedControlItem>
              <SegmentedControlItem id="cm">cm</SegmentedControlItem>
            </SegmentedControl>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <NumberField
            label="Width (X)"
            isRequired
            minValue={0}
            value={values.x ?? Number.NaN}
            onChange={(x) => patch({ x })}
            isInvalid={submitted && xError !== null}
            hint={submitted ? (xError ?? undefined) : unitSuffix}
          />
          <NumberField
            label={isWall ? "Height (Y)" : "Depth (Y)"}
            isRequired
            minValue={0}
            value={values.y ?? Number.NaN}
            onChange={(y) => patch({ y })}
            isInvalid={submitted && yError !== null}
            hint={submitted ? (yError ?? undefined) : unitSuffix}
          />
          <NumberField
            label={isWall ? "Depth (Z, optional)" : "Height (Z, optional)"}
            minValue={0}
            value={values.z ?? Number.NaN}
            onChange={(z) => patch({ z })}
            hint={unitSuffix}
          />
          <NumberField
            label="Weight limit (optional)"
            minValue={0}
            value={values.weightLimit ?? Number.NaN}
            onChange={(weightLimit) => patch({ weightLimit })}
            hint="lb"
          />
        </div>
        <Input
          label="Import my vibes ✨"
          type="url"
          leadingIcon={RiPinterestLine}
          placeholder="pin.it/… or pinterest.com/you/board"
          value={values.boardUrl}
          onChange={(boardUrl) => patch({ boardUrl })}
          isInvalid={boardUrlError !== null}
          errorMessage={boardUrlError ?? undefined}
          isDisabled={activeImport}
          hint={
            activeImport
              ? "A board is still importing…"
              : matchesSaved && savedBoard
                ? displayPinCount === 0
                  ? savedBoard.boardTitle
                    ? `No pins left from "${savedBoard.boardTitle}". Save changes to re-import this board, or paste another.`
                    : "No pins left from this board. Save changes to re-import it, or paste another."
                  : savedBoard.boardTitle
                    ? `Imported from "${savedBoard.boardTitle}" · ${displayPinCount} ${displayPinCount === 1 ? "pin" : "pins"} in your vibes. Paste another board to replace it, or clear to remove.`
                    : `Imported · ${displayPinCount} ${displayPinCount === 1 ? "pin" : "pins"} in your vibes. Paste another board to replace it, or clear to remove.`
                : savedBoard && currentUrl === ""
                  ? "Save changes to clear your space's imported vibes."
                  : isEdit
                    ? "Replaces your space's vibes with this board's pins."
                    : "Paste a public Pinterest board link."
          }
        />
      </ModalBody>
      <ModalFooter>
        <Button type="button" variant="secondary" onClick={close}>
          Cancel
        </Button>
        <Button type="submit">{isEdit ? "Save changes" : "Create space"}</Button>
      </ModalFooter>
    </form>
  );
}
