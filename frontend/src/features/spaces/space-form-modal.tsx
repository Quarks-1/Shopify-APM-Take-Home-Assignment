import { useEffect, useRef, useState } from "react";
import { RiPinterestLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { hintTextClassName } from "@/components/base/input/hint-text";
import { Input } from "@/components/base/input/input";
import { Modal, ModalBody, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { NumberField } from "@/components/base/number-field/number-field";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { Select, SelectItem } from "@/components/base/select/select";
import { toast } from "@/components/base/toast/toast";
import { listSpaceVibeImports, listVibeBoards } from "@/lib/api";
import type { ApiVibeBoard } from "@/lib/api";
import { fromDisplayLength, fromDisplayLengthKeeping, toDisplayLength } from "@/lib/format";
import { boardDisplayName, parseBoardUrl } from "@/features/spaces/board-url";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { SPACE_TYPE_OPTIONS } from "@/mocks/types";
import type { LengthUnit, Space, SpaceType } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { getSpace } from "@/state/selectors";

export interface SpaceFormModalProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  spaceId?: string;
  onSaved?: (id: string) => void;
  onCancel?: () => void;
}

const BOARD_NONE = "__none__";
const BOARD_NEW = "__new__";

interface FormValues {
  name: string;
  type: SpaceType;
  unit: LengthUnit;
  /** Display-unit values; undefined while the field is empty. */
  x?: number;
  y?: number;
  z?: number;
  weightLimit?: number;
  /** `BOARD_NONE`, `BOARD_NEW`, or a `VibeBoard` id. */
  boardChoice: string;
  /** Pinterest board link when importing a new board; empty otherwise. */
  boardUrl: string;
}

function initialValues(space: Space | undefined): FormValues {
  if (!space) {
    return {
      name: "Untitled space",
      type: "floor",
      unit: "in",
      x: 120,
      y: 96,
      boardChoice: BOARD_NONE,
      boardUrl: "",
    };
  }
  return {
    name: space.name,
    type: space.type,
    unit: space.unit,
    x: toDisplayLength(space.dims.x, space.unit),
    y: toDisplayLength(space.dims.y, space.unit),
    z: space.dims.z === undefined ? undefined : toDisplayLength(space.dims.z, space.unit),
    weightLimit: space.weightLimit,
    boardChoice: space.vibeBoardId ?? BOARD_NONE,
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
  const [boards, setBoards] = useState<ApiVibeBoard[]>([]);
  const [boardsLoaded, setBoardsLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    listVibeBoards(controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setBoards(loaded);
        setBoardsLoaded(true);
      })
      .catch(() => {
        if (!controller.signal.aborted) setBoardsLoaded(true);
      });
    return () => controller.abort();
  }, []);

  const existingId = existing?.id;
  const activeImport = existingId !== undefined && vibes.isActive(existingId);

  // When an import is actively running on this space, display its URL while the control is disabled.
  useEffect(() => {
    if (existingId === undefined || !activeImport) return;
    let cancelled = false;
    listSpaceVibeImports(existingId)
      .then((imports) => {
        if (cancelled) return;
        const running = imports.find((i) => i.status === "running" || i.status === "queued");
        if (!running) return;
        setValues((v) =>
          v.boardUrl === "" ? { ...v, boardChoice: BOARD_NEW, boardUrl: running.boardUrl } : v,
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [existingId, activeImport]);

  const patch = (next: Partial<FormValues>) => setValues((v) => ({ ...v, ...next }));
  const isWall = values.type === "wall";
  const unitSuffix = values.unit;

  const hasSavedBoards = boards.length > 0;
  // Guard selectedKey if a previously referenced board had all of its pins removed.
  const effectiveChoice =
    !hasSavedBoards
      ? BOARD_NEW
      : values.boardChoice === BOARD_NONE || values.boardChoice === BOARD_NEW
        ? values.boardChoice
        : !boardsLoaded || boards.some((b) => b.id === values.boardChoice)
          ? values.boardChoice
          : BOARD_NONE;

  const selectedBoard = boards.find((b) => b.id === effectiveChoice);

  const nameError = values.name.trim() === "" ? "Enter a name." : null;
  const xError = (finite(values.x) ?? 0) > 0 ? null : "Must be greater than 0.";
  const yError = (finite(values.y) ?? 0) > 0 ? null : "Must be greater than 0.";

  const boardResult = parseBoardUrl(values.boardUrl);
  const parsedUrlError = "error" in boardResult ? boardResult.error : null;
  const missingNewUrlError =
    hasSavedBoards && effectiveChoice === BOARD_NEW && !activeImport && values.boardUrl.trim() === ""
      ? "Paste a Pinterest board link, or choose No vibe board."
      : null;
  const boardUrlError =
    effectiveChoice === BOARD_NEW
      ? parsedUrlError ?? (submitted ? missingNewUrlError : null)
      : null;
  const boardSelectionValid =
    activeImport ||
    effectiveChoice !== BOARD_NEW ||
    (parsedUrlError === null && missingNewUrlError === null);

  const valid = !nameError && !xError && !yError && boardSelectionValid;

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

    let nextVibeBoardId: string | undefined = existing?.vibeBoardId;
    let urlToImport = "";

    if (!activeImport) {
      if (effectiveChoice === BOARD_NEW) {
        urlToImport = "url" in boardResult ? boardResult.url : "";
      } else if (effectiveChoice === BOARD_NONE) {
        nextVibeBoardId = undefined;
      } else {
        nextVibeBoardId = effectiveChoice;
      }
    }

    const base = {
      name: values.name.trim(),
      type: values.type,
      unit: values.unit,
      dims: z === undefined ? { x, y } : { x, y, z },
      weightLimit,
      vibeBoardId: nextVibeBoardId,
    };

    let id: string;
    if (existing) {
      actions.updateSpace(existing.id, base);
      id = existing.id;
    } else {
      id = actions.createSpace(base);
    }

    if (!activeImport) {
      if (urlToImport !== "") {
        vibes.start(id, urlToImport);
      } else if (nextVibeBoardId !== existing?.vibeBoardId) {
        vibes.dismiss(id);
        if (selectedBoard) {
          toast.success(`Using "${boardDisplayName(selectedBoard)}" vibes for ${base.name}`);
        }
      }
    }

    savedRef.current = true;
    close();
    onSaved?.(id);
  };

  const selectHint = activeImport
    ? "A board is still importing…"
    : selectedBoard
      ? selectedBoard.id === existing?.vibeBoardId
        ? `Using "${boardDisplayName(selectedBoard)}" · ${selectedBoard.usableCount} ${selectedBoard.usableCount === 1 ? "pin" : "pins"} steering searches in this space.`
        : `Applies "${boardDisplayName(selectedBoard)}" (${selectedBoard.usableCount} ${selectedBoard.usableCount === 1 ? "pin" : "pins"}) to new searches in this space.`
      : existing?.vibeBoardId
        ? "Clears this space's vibe board. Imported boards stay in your library."
        : "Choose a previously imported board (one board per space) or import a new link.";

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
          isInvalid={submitted && nameError !== null}
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

        {hasSavedBoards ? (
          <div className="flex flex-col gap-2">
            <Select
              label="Import my vibes ✨"
              className="w-full"
              isDisabled={activeImport}
              selectedKey={effectiveChoice}
              onSelectionChange={(key) => {
                if (key !== null) patch({ boardChoice: String(key) });
              }}
              renderValue={() => {
                if (selectedBoard) {
                  const title = boardDisplayName(selectedBoard);
                  return (
                    <span className="flex min-w-0 items-center gap-1.5 truncate">
                      <RiPinterestLine className="size-4 shrink-0 text-foreground-icon-tertiary" aria-hidden />
                      <span className="truncate">{title}</span>
                      <span className="shrink-0 text-caption-1-medium text-text-tertiary">
                        · {selectedBoard.pinCount} {selectedBoard.pinCount === 1 ? "pin" : "pins"}
                      </span>
                    </span>
                  );
                }
                if (effectiveChoice === BOARD_NEW) {
                  return (
                    <span className="flex min-w-0 items-center gap-1.5 truncate">
                      <RiPinterestLine className="size-4 shrink-0 text-foreground-icon-tertiary" aria-hidden />
                      <span className="truncate">Import a new board…</span>
                    </span>
                  );
                }
                return <span className="truncate text-text-secondary">No vibe board</span>;
              }}
            >
              <SelectItem id={BOARD_NONE} textValue="No vibe board">
                No vibe board
              </SelectItem>
              {boards.map((board) => {
                const title = boardDisplayName(board);
                return (
                  <SelectItem key={board.id} id={board.id} textValue={title}>
                    <span className="flex w-full min-w-0 items-center gap-2">
                      <RiPinterestLine className="size-4 shrink-0 text-foreground-icon-tertiary" aria-hidden />
                      <span className="truncate">{title}</span>
                      <span className="ms-auto shrink-0 ps-2 text-caption-1-medium text-text-tertiary">
                        {board.pinCount} {board.pinCount === 1 ? "pin" : "pins"}
                      </span>
                    </span>
                  </SelectItem>
                );
              })}
              <SelectItem id={BOARD_NEW} textValue="Import a new board…">
                <span className="flex min-w-0 items-center gap-2">
                  <RiPinterestLine className="size-4 shrink-0 text-foreground-icon-tertiary" aria-hidden />
                  <span className="truncate">Import a new board…</span>
                </span>
              </SelectItem>
            </Select>

            {effectiveChoice === BOARD_NEW ? (
              <Input
                aria-label="Pinterest board link"
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
                    : isEdit
                      ? "Imports this board and applies it to new searches in this space."
                      : "Paste a public Pinterest board link."
                }
              />
            ) : (
              <p className={hintTextClassName(false)}>{selectHint}</p>
            )}
          </div>
        ) : (
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
                : isEdit
                  ? "Imports this board and applies it to new searches in this space."
                  : "Paste a public Pinterest board link."
            }
          />
        )}
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
