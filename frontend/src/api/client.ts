/**
 * The one place that talks to the API. It adds the login token, turns the API's error
 * shape (`{"error": {code, message, details}}`) into an `ApiError`, and tells the auth
 * provider when a token stops working (a 401), so the user lands on the login page.
 * Pages call it inside TanStack Query (`useQuery({queryFn: () => api.get(...)})`).
 */
import { getToken } from "../auth/token";

// Empty = the same address as the page (Vite's dev proxy and nginx pass /api through).
const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export type Query = Record<string, string | number | boolean | null | undefined>;

type FieldError = { loc: (string | number)[]; msg: string };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: FieldError[];

  constructor(status: number, code: string, message: string, details: FieldError[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let onUnauthorized = () => {};

/** The auth provider registers what to do when the API says the token is no good. */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

/** `/api/leads` + `{status: "new", q: ""}` -> `/api/leads?status=new` (empty values dropped). */
export function apiUrl(path: string, query: Query = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
  }
  const search = params.toString();
  return `${API_BASE}${path}${search ? `?${search}` : ""}`;
}

type Options = {
  query?: Query;
  json?: unknown;
  form?: Record<string, string>;
};

async function request<T>(method: string, path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: string | URLSearchParams | undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = new URLSearchParams(options.form); // fetch sets the form content type itself
  }

  const response = await fetch(apiUrl(path, options.query), { method, headers, body });
  if (response.status === 401 && token) onUnauthorized();
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const { error } = await response.json();
    return new ApiError(response.status, error.code, error.message, error.details ?? []);
  } catch {
    // Not our error shape (e.g. a proxy's HTML page when the API is down).
    return new ApiError(response.status, "error", `the server answered ${response.status}`);
  }
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, { query }),
  post: <T>(path: string, json?: unknown) => request<T>("POST", path, { json }),
  patch: <T>(path: string, json: unknown) => request<T>("PATCH", path, { json }),
  delete: (path: string) => request<void>("DELETE", path),
  /** The login endpoint takes an OAuth2 password form, not JSON. */
  postForm: <T>(path: string, form: Record<string, string>) => request<T>("POST", path, { form }),
};

/** A message for people: the API's message, plus which fields were wrong on a 422. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.details.length === 0) return error.message;
    const fields = error.details.map((detail) => {
      // loc is like ["body", "source_options", "radius_km"]: drop the "body"/"query" part.
      const where = detail.loc.slice(1).join(".");
      return where ? `${where}: ${detail.msg}` : detail.msg;
    });
    return `${error.message}: ${fields.join("; ")}`;
  }
  if (error instanceof TypeError) return "can't reach the API (is it running?)";
  return error instanceof Error ? error.message : String(error);
}
