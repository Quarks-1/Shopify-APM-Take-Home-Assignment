import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import {
  RiDeleteBinLine,
  RiFolderOpenLine,
  RiSearchLine,
  RiSettings3Line,
  RiShoppingCart2Line,
} from "@remixicon/react";
import { Breadcrumb, BreadcrumbItem } from "@/components/base/breadcrumb/breadcrumb";
import { Button } from "@/components/base/buttons/button";
import { AlertDialog } from "@/components/base/modal/modal";
import { Skeleton } from "@/components/base/skeleton/skeleton";
import { toast } from "@/components/base/toast/toast";
import { ObjectCarouselList } from "@/features/objects/object-carousel-list";
import { useObjectDrawer } from "@/features/objects/use-object-drawer";
import { useObjectSearch } from "@/features/objects/use-object-search";
import { PanelResizer, usePanelSplit } from "@/features/spaces/panel-resizer";
import { SpaceDesigner } from "@/features/spaces/space-designer";
import { SpaceFormModal } from "@/features/spaces/space-form-modal";
import { useVibeImport } from "@/features/spaces/use-vibe-import";
import { VibeImportStatus } from "@/features/spaces/vibe-import-status";
import { formatDims, formatPrice } from "@/lib/format";
import { SPACE_TYPE_OPTIONS } from "@/mocks/types";
import { useAppActions, useAppState } from "@/state/app-store";
import { getObjectsForSpace, getSpace, getSpaceEstimatedTotal } from "@/state/selectors";
import { cx } from "@/utils/cx";

const DESIGNER_ID = "space-editor-designer";
const CARD = "rounded-3xl border border-border-button-default bg-background-primary-default";

function useDocumentTitle(title: string) {
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}

export function SpaceEditorPage() {
  const { spaceId } = useParams<{ spaceId: string }>();
  return spaceId ? <EditSpace key={spaceId} spaceId={spaceId} /> : <CreateSpace />;
}

/* ------------------------------------------------------------------ create */

function CreateSpace() {
  const navigate = useNavigate();
  const location = useLocation();
  const [isOpen, setIsOpen] = useState(true);
  useDocumentTitle("New space · Spaces");

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <Breadcrumb>
            <BreadcrumbItem href="/">Home</BreadcrumbItem>
            <BreadcrumbItem current>New space</BreadcrumbItem>
          </Breadcrumb>
          <h1 className="text-title-1-medium text-text-primary">New space</h1>
          <p className="text-body-regular text-text-secondary">Set the name, type and dimensions to start planning.</p>
        </div>
      </header>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:items-start">
        <div className={`${CARD} p-4`}>
          <Skeleton className="aspect-[4/3] w-full rounded-2xl" />
        </div>
        <div className={`${CARD} min-h-64 p-5`} aria-hidden />
      </div>
      <SpaceFormModal
        isOpen={isOpen}
        onOpenChange={setIsOpen}
        onSaved={(id) => navigate(`/spaces/${id}`, { replace: true })}
        onCancel={() => (location.key !== "default" ? navigate(-1) : navigate("/"))}
      />
    </>
  );
}

/* -------------------------------------------------------------------- edit */

function EditSpace({ spaceId }: { spaceId: string }) {
  const state = useAppState();
  const { deleteSpace, addAllToCart } = useAppActions();
  const { searchAll } = useObjectSearch();
  const vibes = useVibeImport();
  const drawer = useObjectDrawer();
  const navigate = useNavigate();

  const space = getSpace(state, spaceId);
  const objects = space ? getObjectsForSpace(state, spaceId) : [];
  const objectIds = objects.map((o) => o.id);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const panelSplit = usePanelSplit();

  // Pick up an import that is running or just finished (e.g. started in another tab).
  const { refresh: refreshVibes } = vibes;
  useEffect(() => refreshVibes(), [refreshVibes]);

  useDocumentTitle(space ? `${space.name} · Spaces` : "Space not found · Spaces");

  const drawerObjectId = drawer.objectId;
  const drawerObjectInSpace =
    drawerObjectId !== null && state.objects.some((o) => o.id === drawerObjectId) &&
    space?.placements.some((p) => p.objectId === drawerObjectId) === true;
  // Adjust state during render when the URL drawer param changes.
  const [syncedDrawerId, setSyncedDrawerId] = useState<string | null>(null);
  if (drawerObjectId !== syncedDrawerId) {
    setSyncedDrawerId(drawerObjectId);
    if (drawerObjectInSpace && drawerObjectId) setSelectedId(drawerObjectId);
  }

  if (!space) {
    return (
      <div className={`${CARD} flex flex-col items-center gap-4 px-6 py-16 text-center`}>
        <RiFolderOpenLine className="size-8 text-foreground-icon-tertiary" aria-hidden />
        <div className="flex flex-col gap-1">
          <h1 className="text-title-2-medium text-text-primary">Space not found</h1>
          <p className="text-body-regular text-text-secondary">It may have been deleted or the link is wrong.</p>
        </div>
        <Button variant="primary" onClick={() => navigate("/")}>
          Back to home
        </Button>
      </div>
    );
  }

  const typeLabel = SPACE_TYPE_OPTIONS.find((o) => o.value === space.type)?.label ?? space.type;
  const estimated = getSpaceEstimatedTotal(state, space.id);

  const buyAll = () => {
    const added = addAllToCart(objectIds);
    if (added > 0) {
      toast.success(`Added ${added} ${added === 1 ? "item" : "items"} to cart`);
      navigate("/cart");
    } else {
      toast("Nothing to buy yet", { description: "Search and pick products first." });
    }
  };

  const confirmDelete = () => {
    const name = space.name;
    deleteSpace(space.id);
    toast.success(`Deleted "${name}"`);
    navigate("/");
  };

  return (
  <>
    <div className="flex flex-col gap-3 lg:h-[calc(100dvh-3rem)] lg:overflow-hidden">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="truncate text-title-3-medium text-text-primary">{space.name}</h1>
          <p className="hidden text-body-regular text-text-secondary sm:inline">
            {typeLabel} · {formatDims(space.dims, space.unit)} · Est. {formatPrice(estimated)}
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
          <VibeImportStatus spaceId={space.id} />
          <Button variant="secondary" size="small" leadingIcon={RiSettings3Line} onClick={() => setSettingsOpen(true)}>
            Space settings
          </Button>
          <Button variant="secondary" size="small" leadingIcon={RiDeleteBinLine} onClick={() => setDeleteOpen(true)}>
            Delete space
          </Button>
          <Button
            variant="secondary"
            size="small"
            leadingIcon={RiSearchLine}
            disabled={objectIds.length === 0}
            onClick={() => searchAll(objectIds)}
          >
            Search all
          </Button>
          <Button variant="primary" size="small" leadingIcon={RiShoppingCart2Line} onClick={buyAll}>
            Buy all
          </Button>
        </div>
      </header>

      <div
        ref={panelSplit.containerRef}
        style={
          {
            "--split-start": `${panelSplit.split}fr`,
            "--split-end": `${1 - panelSplit.split}fr`,
          } as CSSProperties
        }
        className={cx(
          "grid min-h-0 flex-1 grid-cols-1 gap-6 lg:items-stretch lg:gap-x-0",
          "lg:grid-cols-[minmax(20rem,var(--split-start))_1.5rem_minmax(22rem,var(--split-end))]",
        )}
      >
        <div id={DESIGNER_ID} className="flex min-h-0 min-w-0 flex-col">
          <SpaceDesigner spaceId={space.id} selectedObjectId={selectedId} onSelectObject={setSelectedId} />
        </div>
        <PanelResizer panelSplit={panelSplit} controlsId={DESIGNER_ID} />
        <section
          aria-label="Objects in this space"
          className="flex min-h-0 min-w-0 flex-col gap-3 lg:h-full"
        >
          <ObjectCarouselList
            objectIds={objectIds}
            spaceId={space.id}
            highlightedId={selectedId}
            onHighlight={setSelectedId}
            scrollable
            scrollFadeSurfaceClassName="from-background-full"
            emptyState={
              <p className="text-body-regular text-text-secondary">
                No objects yet. Draw a rectangle on the plan or press Add object.
              </p>
            }
          />
        </section>
      </div>
    </div>

    <SpaceFormModal isOpen={settingsOpen} onOpenChange={setSettingsOpen} spaceId={space.id} />
    <AlertDialog
      isOpen={deleteOpen}
      onOpenChange={setDeleteOpen}
      title="Delete this space?"
      description="The space and its layout are removed. Its objects stay in your object list."
      confirmLabel="Delete space"
      onConfirm={confirmDelete}
    />
  </>
  );
}
