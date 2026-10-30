/**
 * The app: which page shows for which URL (`routes`), and the providers every page needs
 * (TanStack Query's cache, the logged-in user, the router). Tests render the same
 * `routes` in a memory router (see `src/test/render.tsx`).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";
import { ApiError } from "./api/client";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth, RequireRole } from "./auth/guards";
import { Layout } from "./components/Layout";
import { EmptyState } from "./components/Page";
import { AuditPage } from "./pages/AuditPage";
import { CampaignsPage } from "./pages/CampaignsPage";
import { LeadPage } from "./pages/LeadPage";
import { LeadsPage } from "./pages/LeadsPage";
import { LoginPage } from "./pages/LoginPage";
import { NewCampaignPage } from "./pages/NewCampaignPage";
import { QueuePage } from "./pages/QueuePage";
import { RunItemPage } from "./pages/RunItemPage";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Layout />,
        children: [
          { index: true, element: <Navigate to="/campaigns" replace /> },
          { path: "queue", element: <QueuePage /> },
          { path: "campaigns", element: <CampaignsPage /> },
          {
            path: "campaigns/new",
            element: (
              <RequireRole allowed="admin">
                <NewCampaignPage />
              </RequireRole>
            ),
          },
          { path: "runs", element: <RunsPage /> },
          { path: "runs/:runId", element: <RunPage /> },
          { path: "run-items/:itemId", element: <RunItemPage /> },
          { path: "leads", element: <LeadsPage /> },
          { path: "leads/:leadId", element: <LeadPage /> },
          { path: "audit", element: <AuditPage /> },
          { path: "*", element: <EmptyState title="Page not found." /> },
        ],
      },
    ],
  },
];

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // A 4xx answer won't change on a retry; a network hiccup might.
        retry: (failures, error) =>
          failures < 2 && !(error instanceof ApiError && error.status < 500),
        refetchOnWindowFocus: false,
      },
    },
  });
}

const queryClient = makeQueryClient();
const router = createBrowserRouter(routes);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  );
}
