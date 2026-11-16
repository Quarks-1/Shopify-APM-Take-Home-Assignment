import { useRef, useState } from "react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Modal, ModalBody, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { NumberField } from "@/components/base/number-field/number-field";
import { SegmentedControl, SegmentedControlItem } from "@/components/base/segmented-control/segmented-control";
import { fromDisplayLength, toDisplayLength } from "@/lib/format";
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

interface FormValues {
  name: string;
  type: SpaceType;
  unit: LengthUnit;
  /** Display-unit values; undefined while the field is empty. */
  x?: number;
  y?: number;
  z?: number;
  weightLimit?: number;
}

function initialValues(space: Space | undefined): FormValues {
  if (!space) return { name: "Untitled space", type: "floor", unit: "in", x: 120, y: 96 };
  return {
    name: space.name,
    type: space.type,
    unit: space.unit,
    x: toDisplayLength(space.dims.x, space.unit),
    y: toDisplayLength(space.dims.y, space.unit),
    z: space.dims.z === undefined ? undefined : toDisplayLength(space.dims.z, space.unit),
    weightLimit: space.weightLimit,
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
  const existing = spaceId ? getSpace(state, spaceId) : undefined;
  const isEdit = existing !== undefined;

  const [values, setValues] = useState<FormValues>(() => initialValues(existing));
  const [submitted, setSubmitted] = useState(false);

  const patch = (next: Partial<FormValues>) => setValues((v) => ({ ...v, ...next }));
  const isWall = values.type === "wall";
  const unitSuffix = values.unit;

  const nameError = values.name.trim() === "" ? "Enter a name." : null;
  const xError = (finite(values.x) ?? 0) > 0 ? null : "Must be greater than 0.";
  const yError = (finite(values.y) ?? 0) > 0 ? null : "Must be greater than 0.";
  const valid = !nameError && !xError && !yError;

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
    const x = fromDisplayLength(finite(values.x) ?? 0, values.unit);
    const y = fromDisplayLength(finite(values.y) ?? 0, values.unit);
    const zRaw = finite(values.z);
    const z = zRaw !== undefined && zRaw > 0 ? fromDisplayLength(zRaw, values.unit) : undefined;
    const limit = finite(values.weightLimit);
    const weightLimit = limit !== undefined && limit > 0 ? limit : undefined;
    const base = {
      name: values.name.trim(),
      type: values.type,
      unit: values.unit,
      dims: z === undefined ? { x, y } : { x, y, z },
      weightLimit,
    };
    let id: string;
    if (existing) {
      actions.updateSpace(existing.id, base);
      id = existing.id;
    } else {
      id = actions.createSpace(base);
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
