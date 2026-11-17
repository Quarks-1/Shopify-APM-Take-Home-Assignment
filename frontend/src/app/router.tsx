import { Navigate, createBrowserRouter } from "react-router";
import { AppLayout } from "@/app/app-layout";
import { RootRoute, RouteError } from "@/app/root-route";
import { CartPage } from "@/pages/cart-page";
import { BackendStatusPage } from "@/pages/backend-status-page";
import { FindObjectsPage } from "@/pages/find-objects-page";
import { HomePage } from "@/pages/home-page";
import { SpaceEditorPage } from "@/pages/space-editor-page";

export const router = createBrowserRouter([
  {
    element: <RootRoute />,
    errorElement: <RouteError />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <HomePage /> },
          { path: "spaces/new", element: <SpaceEditorPage /> },
          { path: "spaces/:spaceId", element: <SpaceEditorPage /> },
          { path: "find", element: <FindObjectsPage /> },
          { path: "cart", element: <CartPage /> },
          { path: "dev/backend", element: <BackendStatusPage /> },
        ],
      },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
