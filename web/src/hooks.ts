import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";

// Hash routing (#/ranges/web2tier): deep links work without any server-side configuration.
export function useRoute(): string[] {
  const parse = () => (window.location.hash.replace(/^#\/?/, "") || "dashboard").split("/").map(decodeURIComponent);
  const [route, setRoute] = useState<string[]>(parse);
  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function go(path: string) {
  window.location.hash = "/" + path.replace(/^\//, "");
}

// A started job opens in the task viewer over the current page (the shell listens), as Proxmox does with its tasks.
export function openTask(id: string) {
  window.dispatchEvent(new CustomEvent("valor-task", { detail: id }));
}

// The range list (tree, Datacenter summary) reloads at once, e.g. after a range is deleted.
export function rangesChanged() {
  window.dispatchEvent(new Event("valor-ranges"));
}

export function useStored(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState(() => {
    try { const stored = localStorage.getItem(key); return stored === null ? initial : stored === "1"; } catch { return initial; }
  });
  const set = (v: boolean) => {
    setValue(v);
    try { localStorage.setItem(key, v ? "1" : "0"); } catch { /* not kept */ }
  };
  return [value, set];
}

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

// GET a resource; optionally refresh it every `intervalMs` (paused while the tab is hidden).
export function useApi<T = any>(path: string | null, intervalMs?: number): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const seq = useRef(0);

  const load = useCallback(() => {
    if (!path) return;
    const mine = ++seq.current;
    get<T>(path)
      .then((body) => {
        if (mine === seq.current) {
          setData(body);
          setError(null);
        }
      })
      .catch((e: ApiError) => mine === seq.current && setError(e))
      .finally(() => mine === seq.current && setLoading(false));
  }, [path]);

  useEffect(() => {
    setLoading(!!path);
    setData(null);
    load();
    if (!intervalMs) return;
    const timer = window.setInterval(() => document.visibilityState === "visible" && load(), intervalMs);
    return () => window.clearInterval(timer);
  }, [load, intervalMs, path]);

  return { data, error, loading, reload: load };
}

export function when(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "–";
  const date = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(date.getTime())) return String(ts);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ago(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "–";
  const date = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (isNaN(seconds)) return String(ts);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return when(ts);
}
