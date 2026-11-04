import { useState } from "react";
import { ApiError, post } from "../api";
import { Change, Plan, Topology } from "../types";
import { openTask } from "../hooks";
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
  scripts?: { host: string; os?: string; script: string; ports?: string }[];
}

export function PlanModal({ name, res, onClose, draft }: { name: string; res: PlanResponse; onClose: () => void; draft?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [ack, setAck] = useState(false);
  const scripts = res.ok ? res.scripts ?? [] : [];
  const approve = async () => {
    setBusy(true);
    setErr(null);
    try {
      const started = await post<{ job: string }>(`/api/ranges/${name}/apply`, { plan_hash: res.plan_hash, draft: !!draft });
      onClose();
      openTask(started.job);
    } catch (e) {
      setErr(e as ApiError);
      setBusy(false);
    }
  };
  const plan = res.plan;
  const changeOf: Record<string, Change | undefined> = {};
  for (const node of res.topology.nodes) {
    if (node.id === "rtr") changeOf.rtr = node.change;
    else if (node.id.startsWith("host:")) changeOf[node.id.slice(5)] = node.change;
  }
  return (
    <Modal title={draft ? `Plan for the changes to ${name}` : `Plan for ${name}`} onClose={onClose} wide footer={
      <>
        <button className="btn" onClick={onClose}>Close</button>
        {res.ok && plan?.changes && (
          <button className="btn primary" onClick={approve} disabled={busy || (scripts.length > 0 && !ack)}>
            {busy ? <><span className="spinner" /> Starting…</> : "Approve and build"}
          </button>
        )}
      </>
    }>
      {!res.ok && (
        <div className="alert error">
          The spec can't be built on this cluster yet:
          <ul>{res.errors?.map((error, i) => <li key={i}>{error.location}: {error.message}{error.hint ? ` (${error.hint})` : ""}</li>)}</ul>
        </div>
      )}
      {res.warnings?.map((warning, i) => <div key={i} className="alert warn">{warning.message}</div>)}
      {plan && !plan.changes && <div className="alert ok">Nothing to do: the range already matches its spec.</div>}
      {plan && plan.changes && (
        <div className="row small">
          {Object.entries(plan.summary).map(([change, count]) => <span key={change} className="badge">{change} {count}</span>)}
          <span className="muted">Approving runs exactly this plan; if anything changes before it starts, VALOR asks again.</span>
        </div>
      )}
      <div className="card" style={{ overflow: "hidden" }}><TopologyMap topology={res.topology} /></div>
      {plan && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>VM</th><th>Change</th><th>Why</th></tr></thead>
            <tbody>
              {plan.actions.map((action) => (
                <tr key={action.host}>
                  <td><b>{action.host}</b> <span className="muted small">{action.vmid ? `#${action.vmid}` : ""}</span></td>
                  <td>
                    <span className="badge" style={{ color: `var(--c-${changeOf[action.host] ?? "keep"})` }}>{action.action}</span>{" "}
                    <span className="muted small">{CHANGE_LABEL[changeOf[action.host] ?? "keep"]}</span>
                  </td>
                  <td className="small">{action.reasons.join("; ") || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {scripts.length > 0 && plan?.changes && (
        <div className="card scripts-review">
          <h3>Custom scripts this plan runs</h3>
          <p className="small muted">
            These run as root (Linux) or SYSTEM (Windows) on the hosts below while the range has internet access for the
            build. Read them before approving.
          </p>
          {scripts.map((entry, i) => (
            <div key={i}>
              <div className="small"><b>{entry.host}</b> <span className="muted">{entry.os ?? ""}{entry.ports ? ` · ports ${entry.ports}` : ""}</span></div>
              <pre className="script">{entry.script}</pre>
            </div>
          ))}
          <label className="row small"><input name="acknowledge" type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I have read the custom scripts and want to run them</label>
        </div>
      )}
      <ErrorBox error={err} />
    </Modal>
  );
}

