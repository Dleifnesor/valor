// Thin client for the VALOR API: JSON in and out, the session cookie is sent automatically,
// the CSRF token goes in a header on every state-changing request.

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

let csrf = "";
let onUnauthenticated: () => void = () => {};

export function setCsrf(token: string) {
  csrf = token;
}

export function csrfToken(): string {
  return csrf;
}

export function setUnauthenticatedHandler(fn: () => void) {
  onUnauthenticated = fn;
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "VALOR could not be reached. Check your connection.", null);
  }
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401 && data?.error === "unauthenticated" && !path.startsWith("/api/auth/")) onUnauthenticated();
    throw new ApiError(res.status, data?.error ?? "http_error", data?.message ?? res.statusText, data);
  }
  return data as T;
}

export const get = <T = any>(path: string) => api<T>("GET", path);
export const post = <T = any>(path: string, body?: unknown) => api<T>("POST", path, body ?? {});
export const put = <T = any>(path: string, body: unknown) => api<T>("PUT", path, body);
export const patch = <T = any>(path: string, body: unknown) => api<T>("PATCH", path, body);
export const del = <T = any>(path: string) => api<T>("DELETE", path);
