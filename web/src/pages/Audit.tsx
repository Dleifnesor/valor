import { useEffect, useState } from "react";
import { ApiError, get } from "../api";
import { when } from "../hooks";
import { Card, ErrorBox } from "../components/ui";

// The audit log (admins): every sign-in, change and build with who did it and from where, newest first, searchable
// and loaded 100 entries at a time.

interface Entry { id: number; ts: number; username: string; ip: string; action: string; target: string; outcome: string; detail: string }

export function Audit() {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<Entry[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const load = async (before: number | null, query: string) => {
    try {
      const page = await get<{ entries: Entry[]; next: number | null }>(
        `/api/audit?limit=100${before ? `&before=${before}` : ""}${query ? `&q=${encodeURIComponent(query)}` : ""}`);
      setRows((old) => (before ? [...old, ...page.entries] : page.entries));
      setNext(page.next);
      setErr(null);
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => load(null, query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  return (
    <Card title="Audit log" bodyClass="" actions={
      <input name="search" placeholder="Search action, user, target…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ maxWidth: 280 }} aria-label="Search" />
    }>
      <div style={{ padding: err ? "12px 18px 0" : 0 }}><ErrorBox error={err} /></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th><th>Result</th><th>From</th><th>Details</th></tr></thead>
          <tbody>
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
      {!rows.length && <div className="empty">No entries.</div>}
      {next && <div style={{ padding: 12, textAlign: "center" }}><button className="btn" onClick={() => load(next, query)}>Load older</button></div>}
    </Card>
  );
}
