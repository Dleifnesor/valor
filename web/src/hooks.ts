import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";

// Hash routing (#/ranges/web2tier): deep links work without any server-side configuration.
export function useRoute(): string[] {
  const parse = () => (window.location.hash.replace(/^#\/?/, "") || "dashboard").split("/").map(decodeURIComponent);
  const [route, setRoute] = useState<string[]>(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(path: string) {
  window.location.hash = "/" + path.replace(/^\//, "");
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
      .then((d) => {
        if (mine === seq.current) {
          setData(d);
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
    const t = window.setInterval(() => document.visibilityState === "visible" && load(), intervalMs);
    return () => window.clearInterval(t);
  }, [load, intervalMs, path]);

  return { data, error, loading, reload: load };
}

export function when(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "–";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function ago(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "–";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (isNaN(s)) return String(ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return when(ts);
}
