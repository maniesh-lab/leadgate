/**
 * Test helpers: a fake API and the whole app rendered at a URL.
 *
 * `fakeApi({"GET /api/leads": () => page})` replaces `fetch`: each request is matched by
 * method + path (no query string), answered with the handler's value as JSON, and
 * recorded in `calls` so a test can check what was asked. A handler can return
 * `reply(status, body)` for an error answer. Unknown paths answer 404.
 *
 * `renderApp("/leads?status=new", {user})` renders the real routes in a memory router,
 * with a token saved when a user is given (so the app starts logged in).
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { vi } from "vitest";
import { makeQueryClient, routes } from "../App";
import type { Page, User } from "../api/types";
import { AuthProvider } from "../auth/AuthProvider";
import { setToken } from "../auth/token";

type Reply = { status: number; body: unknown };
type Handler = (url: URL, init: RequestInit) => unknown;

export function reply(status: number, body: unknown): Reply {
  return { status, body };
}

export function fakeApi(handlers: Record<string, Handler>) {
  const calls: { method: string; url: URL; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = new URL(input, "http://leadgate.test");
    const method = init.method ?? "GET";
    calls.push({ method, url, body: init.body });
    const handler = handlers[`${method} ${url.pathname}`];
    const answer = handler
      ? handler(url, init)
      : reply(404, { error: { code: "not_found", message: `no fake for ${url.pathname}` } });
    const { status, body } = isReply(answer) ? answer : { status: 200, body: answer };
    return new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

function isReply(value: unknown): value is Reply {
  return typeof value === "object" && value !== null && "status" in value && "body" in value;
}

export function renderApp(path: string, options: { user?: User } = {}) {
  if (options.user) setToken("test-token");
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return router;
}

export function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, page_size: 50 };
}

export const ADMIN: User = {
  id: 1,
  email: "admin@example.com",
  role: "admin",
  is_active: true,
  created_at: "2026-10-30T09:00:00Z",
};

export const REVIEWER: User = { ...ADMIN, id: 2, email: "reviewer@example.com", role: "reviewer" };
