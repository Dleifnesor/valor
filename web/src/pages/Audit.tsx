import { useEffect, useState } from "react";
import { ApiError, get } from "../api";
import { when } from "../hooks";
import { Card, ErrorBox } from "../components/ui";

interface Entry { id: number; ts: number; username: string; ip: string; action: string; target: string; outcome: string; detail: string }

export function Audit() {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Entry[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const load = async (before: number | null, query: string) => {
    try {
      const r = await get<{ entries: Entry[]; next: number | null }>(
        `/api/audit?limit=100${before ? `&before=${before}` : ""}${query ? `&q=${encodeURIComponent(query)}` : ""}`);
      setRows((old) => (before ? [...old, ...r.entries] : r.entries));
      setNext(r.next);
      setErr(null);
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  useEffect(() => {
    const t = window.setTimeout(() => load(null, q), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  return (
    <Card title="Audit log" bodyClass="" actions={
      <input placeholder="Search action, user, target…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} aria-label="Search" />
    }>
      <div style={{ padding: err ? "12px 18px 0" : 0 }}><ErrorBox error={err} /></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th><th>Result</th><th>From</th><th>Details</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="nowrap">{when(r.ts)}</td>
                <td>{r.username || "–"}</td>
                <td className="mono">{r.action}</td>
                <td>{r.target || "–"}</td>
                <td><span className={`badge ${r.outcome === "ok" ? "ok" : "bad"}`}>{r.outcome}</span></td>
                <td className="mono small">{r.ip || "–"}</td>
                <td className="small" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <div className="empty">No entries.</div>}
      {next && <div style={{ padding: 12, textAlign: "center" }}><button className="btn" onClick={() => load(next, q)}>Load older</button></div>}
    </Card>
  );
}
