import { useState } from "react";
import { ApiError, post } from "../api";
import { Change, Plan, Topology } from "../types";
import { go } from "../hooks";
import { ErrorBox, Modal } from "./ui";
import { CHANGE_LABEL, TopologyMap } from "./Topology";

// The plan the engine would run, on the map and as a table. Approving starts exactly this plan (the server
// recomputes it and compares hashes). With `draft`, the plan is for the range's draft and approving makes the
// draft the range's spec.

export interface PlanResponse {
  ok: boolean;
  errors?: { location: string; message: string; hint?: string }[];
  warnings?: { location: string; message: string }[];
  plan?: Plan;
  plan_hash?: string;
  topology: Topology;
}

export function PlanModal({ name, res, onClose, draft }: { name: string; res: PlanResponse; onClose: () => void; draft?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const approve = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ job: string }>(`/api/ranges/${name}/apply`, { plan_hash: res.plan_hash, draft: !!draft });
      go(`jobs/${r.job}`);
    } catch (e) {
      setErr(e as ApiError);
      setBusy(false);
    }
  };
  const p = res.plan;
  const changeOf: Record<string, Change | undefined> = {};
  for (const n of res.topology.nodes) {
    if (n.id === "rtr") changeOf.rtr = n.change;
    else if (n.id.startsWith("host:")) changeOf[n.id.slice(5)] = n.change;
  }
  return (
    <Modal title={draft ? `Plan for the changes to ${name}` : `Plan for ${name}`} onClose={onClose} wide footer={
      <>
        <button className="btn" onClick={onClose}>Close</button>
        {res.ok && p?.changes && (
          <button className="btn primary" onClick={approve} disabled={busy}>
            {busy ? <><span className="spinner" /> Starting…</> : "Approve and build"}
          </button>
        )}
      </>
    }>
      {!res.ok && (
        <div className="alert error">
          The spec can't be built on this cluster yet:
          <ul>{res.errors?.map((e, i) => <li key={i}>{e.location}: {e.message}{e.hint ? ` (${e.hint})` : ""}</li>)}</ul>
        </div>
      )}
      {res.warnings?.map((w, i) => <div key={i} className="alert warn">{w.message}</div>)}
      {p && !p.changes && <div className="alert ok">Nothing to do: the range already matches its spec.</div>}
      {p && p.changes && (
        <div className="row small">
          {Object.entries(p.summary).map(([k, n]) => <span key={k} className="badge">{k} {n}</span>)}
          <span className="muted">Approving runs exactly this plan; if anything changes before it starts, VALOR asks again.</span>
        </div>
      )}
      <div className="card" style={{ overflow: "hidden" }}><TopologyMap topology={res.topology} /></div>
      {p && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>VM</th><th>Change</th><th>Why</th></tr></thead>
            <tbody>
              {p.actions.map((a) => (
                <tr key={a.host}>
                  <td><b>{a.host}</b> <span className="muted small">{a.vmid ? `#${a.vmid}` : ""}</span></td>
                  <td>
                    <span className="badge" style={{ color: `var(--c-${changeOf[a.host] ?? "keep"})` }}>{a.action}</span>{" "}
                    <span className="muted small">{CHANGE_LABEL[changeOf[a.host] ?? "keep"]}</span>
                  </td>
                  <td className="small">{a.reasons.join("; ") || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ErrorBox error={err} />
    </Modal>
  );
}

