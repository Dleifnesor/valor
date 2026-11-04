// *sets little fishing hooks* OwO web/src/hooks.ts: small helpers shared by every page! ^w^
// Routing (which page), navigation, app-wide events, remembered switches, loading data from the API (with
// auto-refresh), and date formatting *wags tail*
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get } from "./api";

// Hash routing (#/ranges/web2tier): deep links work without any server-side configuration.
// useRoute: the current route as parts ("#/ranges/web" -> ["ranges", "web"]), re-rendering on every change; the
// default page is the dashboard UwU
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

// go: navigate by changing the hash >w<
export function go(path: string) {
  window.location.hash = "/" + path.replace(/^\//, "");
}

// A started job opens in the task viewer over the current page (the shell listens), as Proxmox does with its tasks.
// openTask / rangesChanged: app-wide browser events the shell listens to OwO
export function openTask(id: string) {
  window.dispatchEvent(new CustomEvent("valor-task", { detail: id }));
}

// The range list (tree, Datacenter summary) reloads at once, e.g. after a range is deleted.
export function rangesChanged() {
  window.dispatchEvent(new Event("valor-ranges"));
}

// useStored: a true/false setting remembered in localStorage (as "1"/"0"), with a fallback when storage is
// blocked ^w^
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

// Loaded: what useApi returns: the data, the error, loading or not, and reload() UwU
export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

// GET a resource; optionally refresh it every `intervalMs` (paused while the tab is hidden).
// useApi: GET a resource, optionally refreshing every intervalMs >w<
export function useApi<T = any>(path: string | null, intervalMs?: number): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  // seq counts requests: only the NEWEST request's answer is used (an older slow one can't overwrite newer data) OwO
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

  // Load when the path changes; with an interval, refresh, but only while the tab is visible ^w^
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

// when: a timestamp (unix seconds or ISO text) as a local date and time, or "–" UwU
export function when(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "–";
  const date = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(date.getTime())) return String(ts);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// ago: "just now", "5 min ago", "3 h ago", or the full date after a day >w< *checks the clock*
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
