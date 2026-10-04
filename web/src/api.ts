// *picks up the tiny phone to the server* OwO web/src/api.ts is the web UI's API client! ^w^
// Every page talks to the server through api() and its shortcuts (get, post, put, patch, del): JSON in and out, the
// session cookie travels automatically, and the CSRF token goes in a header on every request that changes things.
// Errors become ApiError objects with the server's code and message *wags tail*
// Thin client for the VALOR API: JSON in and out, the session cookie is sent automatically,
// the CSRF token goes in a header on every state-changing request.

// ApiError: a failed request: HTTP status (0 = network), the server's error code, its message, and the full body UwU
export class ApiError extends Error {
  status: number;
  code: string;
  data: any;
  constructor(status: number, code: string, message: string, data: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

// csrf: the anti-forgery token (from sign-in). onUnauthenticated: what to do when the session expired (App sets
// it to show the sign-in page) >w<
let csrf = "";
let onUnauthenticated: () => void = () => {};

// setCsrf / csrfToken / setUnauthenticatedHandler: set and read the values above OwO
export function setCsrf(token: string) {
  csrf = token;
}

export function csrfToken(): string {
  return csrf;
}

export function setUnauthenticatedHandler(fn: () => void) {
  onUnauthenticated = fn;
}

// api(): one request. T is the type the caller expects back ^w^
export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  // JSON body -> Content-Type header; anything but GET carries the CSRF token UwU
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  let res: Response;
  try {
    // Send it; credentials "same-origin" sends the session cookie. A network failure becomes a friendly ApiError >w<
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "VALOR could not be reached. Check your connection.", null);
  }
  // Read the JSON body (some answers have none) OwO
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  // Errors: an expired session (401 unauthenticated, outside sign-in itself) calls the handler, then throw an
  // ApiError with the server's code and message ^w^
  if (!res.ok) {
    if (res.status === 401 && data?.error === "unauthenticated" && !path.startsWith("/api/auth/")) onUnauthenticated();
    throw new ApiError(res.status, data?.error ?? "http_error", data?.message ?? res.statusText, data);
  }
  return data as T;
}

// Shortcuts for each HTTP method (post sends {} when there's no body, so it's always JSON) UwU
export const get = <T = any>(path: string) => api<T>("GET", path);
export const post = <T = any>(path: string, body?: unknown) => api<T>("POST", path, body ?? {});
export const put = <T = any>(path: string, body: unknown) => api<T>("PUT", path, body);
export const patch = <T = any>(path: string, body: unknown) => api<T>("PATCH", path, body);
export const del = <T = any>(path: string) => api<T>("DELETE", path);
