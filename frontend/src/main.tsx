import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "@/styles/fonts.css";
import "@/styles/globals.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { DirectionProvider } from "@/components/foundations/direction/direction";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DirectionProvider locale="en-US">
      <App />
    </DirectionProvider>
  </StrictMode>,
);
