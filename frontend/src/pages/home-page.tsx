import { useEffect, useRef, useState } from "react";
import { RiAddLine, RiDownloadCloud2Line, RiRestartLine, RiSearchLine } from "@remixicon/react";
import { useNavigate } from "react-router";
import { Button } from "@/components/base/buttons/button";
import { Carousel, CarouselItem } from "@/components/base/carousel/carousel";
import { AlertDialog } from "@/components/base/modal/modal";
import { toast } from "@/components/base/toast/toast";
import { SectionTitle } from "@/features/home/section-title";
import { VibesInspirationSection } from "@/features/home/vibes-inspiration";
import { ObjectCard } from "@/features/objects/object-card";
import { NewSpaceCard, SpaceCard } from "@/features/spaces/space-card";
import { formatRelativeTime } from "@/lib/format";
import { useAppActions, useAppState } from "@/state/app-store";
import { getImportedObjects, getSearchableObjects } from "@/state/selectors";

const IMPORT_DELAY_MS = 1000;

export function HomePage() {
  const state = useAppState();
  const { resetDemo, importFromShopify } = useAppActions();
  const navigate = useNavigate();
  const [confirmReset, setConfirmReset] = useState(false);
  const [importing, setImporting] = useState(false);
  const importTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (importTimer.current) clearTimeout(importTimer.current);
    },
    [],
  );

  const objects = getSearchableObjects(state);
  const imported = getImportedObjects(state);

  const handleImport = () => {
    if (importing || state.importedAt) return;
    setImporting(true);
    importTimer.current = setTimeout(() => {
      importTimer.current = null;
      importFromShopify();
      setImporting(false);
      toast.success("Imported 4 items from Shopify");
    }, IMPORT_DELAY_MS);
  };

  const objectsHeader = <SectionTitle id="home-objects" title="Current objects" count={objects.length} />;
  const objectsAction = (
    <Button variant="ghost" size="small" onClick={() => navigate("/find")}>
      Find new objects
    </Button>
  );
  const importedHeader = (
    <SectionTitle
      id="home-imported"
      title="Imported objects"
      count={imported.length}
      description="Things you already own, from your Shopify order history."
    />
  );
  const importedAction = (
    <Button
      variant="secondary"
      leadingIcon={RiDownloadCloud2Line}
      disabled={importing || Boolean(state.importedAt)}
      onClick={handleImport}
    >
      {state.importedAt
        ? `Imported ${formatRelativeTime(state.importedAt)}`
        : importing
          ? "Importing…"
          : "Import from Shopify"}
    </Button>
  );

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-title-1-medium text-text-primary">Your spaces</h1>
          <p className="text-body-regular text-text-secondary">
            Design a space, find objects for it, and buy them store by store.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" leadingIcon={RiRestartLine} onClick={() => setConfirmReset(true)}>
            Reset demo data
          </Button>
          <Button variant="secondary" leadingIcon={RiSearchLine} onClick={() => navigate("/find")}>
            Find new objects
          </Button>
          <Button leadingIcon={RiAddLine} onClick={() => navigate("/spaces/new")}>
            Create new space
          </Button>
        </div>
      </header>

      <section className="flex flex-col gap-4" aria-labelledby="home-spaces">
        <Carousel
          aria-label="Current spaces"
          showDots={false}
          gap={16}
          header={<SectionTitle id="home-spaces" title="Current spaces" count={state.spaces.length} />}
        >
          <CarouselItem className="w-72">
            <NewSpaceCard />
          </CarouselItem>
          {state.spaces.map((space) => (
            <CarouselItem key={space.id} className="w-72">
              <SpaceCard spaceId={space.id} />
            </CarouselItem>
          ))}
        </Carousel>
        {state.spaces.length === 0 && (
          <p className="text-body-regular text-text-secondary">No spaces yet. Start with a new one.</p>
        )}
      </section>

      <VibesInspirationSection />

      <section className="flex flex-col gap-4" aria-labelledby="home-objects">
        {objects.length > 0 ? (
          <Carousel
            aria-label="Current objects"
            showDots={false}
            gap={16}
            header={objectsHeader}
            actions={objectsAction}
          >
            {objects.map((o) => (
              <CarouselItem key={o.id} className="w-56">
                <ObjectCard objectId={o.id} />
              </CarouselItem>
            ))}
          </Carousel>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
              <div className="min-w-0">{objectsHeader}</div>
              <div className="ms-auto flex items-center gap-2">{objectsAction}</div>
            </div>
            <div className="flex flex-col items-center gap-3 rounded-3xl border border-border-button-default bg-background-primary-default p-8 text-center">
              <h3 className="text-headline-medium text-text-primary">No objects yet</h3>
              <p className="text-body-regular text-text-secondary">Define an object and we'll search the catalog for it.</p>
              <Button variant="secondary" leadingIcon={RiSearchLine} onClick={() => navigate("/find")}>
                Find objects
              </Button>
            </div>
          </>
        )}
      </section>

      <section className="flex flex-col gap-4" aria-labelledby="home-imported">
        {imported.length > 0 ? (
          <Carousel
            aria-label="Imported objects"
            showDots={false}
            gap={16}
            header={importedHeader}
            actions={importedAction}
          >
            {imported.map((o) => (
              <CarouselItem key={o.id} className="w-56">
                <ObjectCard objectId={o.id} />
              </CarouselItem>
            ))}
          </Carousel>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
              <div className="min-w-0">{importedHeader}</div>
              <div className="ms-auto flex items-center gap-2">{importedAction}</div>
            </div>
            <div className="rounded-3xl border border-dashed border-border-button-default p-8 text-center text-body-regular text-text-secondary">
              Importing pulls items you've already bought from Shopify, so you can place them in a space without
              buying them again.
            </div>
          </>
        )}
      </section>

      <AlertDialog
        isOpen={confirmReset}
        onOpenChange={setConfirmReset}
        title="Reset demo data?"
        description="This restores the original spaces and objects and clears your cart."
        confirmLabel="Reset"
        onConfirm={() => {
          if (importTimer.current !== null) {
            clearTimeout(importTimer.current);
            importTimer.current = null;
          }
          setImporting(false);
          resetDemo();
          toast.success("Demo data reset");
        }}
      />
    </>
  );
}
