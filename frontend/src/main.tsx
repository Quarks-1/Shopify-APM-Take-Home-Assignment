import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@/styles/fonts.css";
import "@/styles/globals.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { router } from "@/app/router";
import { DirectionProvider } from "@/components/foundations/direction/direction";
import { ObjectSearchProvider } from "@/features/objects/use-object-search";
import { VibeImportProvider } from "@/features/spaces/use-vibe-import";
import { AppStoreProvider } from "@/state/app-store";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DirectionProvider locale="en-US">
      <AppStoreProvider>
        <ObjectSearchProvider>
          <VibeImportProvider>
            <RouterProvider router={router} />
          </VibeImportProvider>
        </ObjectSearchProvider>
      </AppStoreProvider>
    </DirectionProvider>
  </StrictMode>,
);
