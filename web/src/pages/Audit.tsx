// *opens the big logbook* OwO web/src/pages/Audit.tsx is the AUDIT LOG page (admins only)! ^w^
// Every sign-in, change and build, with who did it and from where, newest first, searchable, loaded 100 at a time
// *wags tail*
import { useEffect, useState } from "react";
// get + ApiError (talking to the API), when (formats a timestamp), Card/ErrorBox (shared UI pieces) UwU
import { ApiError, get } from "../api";
import { when } from "../hooks";
import { Card, ErrorBox } from "../components/ui";

// The audit log (admins): every sign-in, change and build with who did it and from where, newest first, searchable
// and loaded 100 entries at a time.

// Entry: one audit row as the API sends it (ts = unix seconds) >w<
interface Entry { id: number; ts: number; username: string; ip: string; action: string; target: string; outcome: string; detail: string }

// Audit: the page component OwO
export function Audit() {
  // State: the search text, the rows loaded so far, the id to load older rows from (null = no more), and the last
  // error ^w^
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Entry[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  // load(): fetch one page. `before` = continue below that id (null = start from the newest); `query` = the search UwU
  const load = async (before: number | null, query: string) => {
    try {
      const page = await get<{ entries: Entry[]; next: number | null }>(
        `/api/audit?limit=100${before ? `&before=${before}` : ""}${query ? `&q=${encodeURIComponent(query)}` : ""}`);
      // Older pages are appended; a fresh search replaces the list >w<
      setRows((old) => (before ? [...old, ...page.entries] : page.entries));
      setNext(page.next);
      setErr(null);
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  // Search as you type, but wait 250 ms after the last key (a "debounce"), so we don't ask the server on every
  // keystroke; the cleanup cancels the pending timer when the text changes again OwO
  useEffect(() => {
    const timer = window.setTimeout(() => load(null, query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  // The page: a card titled "Audit log" with the search box in its header ^w^
  return (
    <Card title="Audit log" bodyClass="" actions={
      <input name="search" placeholder="Search action, user, target…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ maxWidth: 280 }} aria-label="Search" />
    }>
      {/* Any error, then the table */}
      <div style={{ padding: err ? "12px 18px 0" : 0 }}><ErrorBox error={err} /></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th><th>Result</th><th>From</th><th>Details</th></tr></thead>
          <tbody>
            {/* One row per entry: time, user, action, target, ok/bad badge, address, details ("–" for empty fields) */}
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="nowrap">{when(row.ts)}</td>
                <td>{row.username || "–"}</td>
                <td className="mono">{row.action}</td>
                <td>{row.target || "–"}</td>
                <td><span className={`badge ${row.outcome === "ok" ? "ok" : "bad"}`}>{row.outcome}</span></td>
                <td className="mono small">{row.ip || "–"}</td>
                <td className="small" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>{row.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* "No entries." when the list is empty, and "Load older" while there are more pages */}
      {!rows.length && <div className="empty">No entries.</div>}
      {next && <div style={{ padding: 12, textAlign: "center" }}><button className="btn" onClick={() => load(next, query)}>Load older</button></div>}
    </Card>
  );
}
