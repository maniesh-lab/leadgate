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
import { RequireAuth } from "./auth/guards";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Layout />,
        children: [
          { index: true, element: <Navigate to="/campaigns" replace /> },
          { path: "*", element: <p className="text-slate-600">Page not found.</p> },
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
