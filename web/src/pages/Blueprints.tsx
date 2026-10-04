import { useMemo, useState } from "react";
import { ApiError, del, post, put } from "../api";
import { go, useApi, when } from "../hooks";
import { Topology } from "../types";
import { ErrorBox, Loading, Modal } from "../components/ui";
import { TopologyMap } from "../components/Topology";

// Blueprints: keep a range spec and deploy copies of it - numbered, or one per student. Each copy is an ordinary
// range with its own VLANs and networks; deploying runs exactly what the preview showed.

interface Blueprint {
  id: string;
  valid: boolean;
  error?: string;
  description?: string;
  segments?: number;
  hosts?: number;
  os?: string[];
  wireguard?: boolean;
  updated?: string;
  copies?: string[];
}

interface CopyPreview {
  name: string;
  student: string | null;
  allocation: Record<string, { vlan?: number; cidr?: string; network?: string; peers?: string[] }>;
  yaml: string;
  ok: boolean;
  errors: { location: string; message: string; hint?: string }[];
  plan_summary: Record<string, number> | null;
}

interface Preview {
  copies: CopyPreview[];
  cluster_error: string | null;
  capacity: { additional_memory_mib: number; memory_total_mib: number; within_limit: boolean } | null;
  ok: boolean;
  deploy_hash: string;
}

const NAME_RE = /^[a-z][a-z0-9-]{0,13}[a-z0-9]$/;

export function Blueprints({ canOperate }: { canOperate: boolean }) {
  const { data, error, loading, reload } = useApi<{ blueprints: Blueprint[] }>("/api/blueprints", 30000);
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [deploying, setDeploying] = useState<Blueprint | null>(null);
  if (loading && !data) return <Loading />;
  return (
    <>
      <div className="row">
        <div className="grow muted">
          A blueprint is a range spec you can deploy many times: numbered copies, or one per student. Every copy gets
          its own VLANs and networks (hosts keep their addresses' last part) and, with WireGuard access, a peer for
          its student.
        </div>
        {canOperate && <button className="btn primary" onClick={() => setCreating(true)}>New blueprint</button>}
      </div>
      <ErrorBox error={error} />
      <section className="card">
        {!data?.blueprints.length ? (
          <div className="empty">No blueprints yet. Save a range as a blueprint, or paste a spec.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Blueprint</th><th>Contents</th><th>Copies</th><th>Updated</th><th></th></tr></thead>
              <tbody>
                {data.blueprints.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <button className="link mono" onClick={() => setViewing(b.id)}>{b.id}</button>
                      <div className="muted small">{b.valid ? b.description : `invalid: ${b.error}`}</div>
                    </td>
                    <td className="small">
                      {b.valid && <>{b.hosts} hosts · {b.segments} segments · {b.os?.join(", ")}
                        {b.wireguard && <span className="badge ok" style={{ marginLeft: 6 }}>WireGuard</span>}</>}
                    </td>
                    <td className="small">
                      {b.copies?.length ? b.copies.map((c) => (
                        <button key={c} className="link mono" style={{ marginRight: 6 }} onClick={() => go(`ranges/${c}`)}>{c}</button>
                      )) : <span className="muted">none</span>}
                    </td>
                    <td className="small">{when(b.updated)}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {canOperate && b.valid && <button className="btn small primary" onClick={() => setDeploying(b)}>Deploy copies…</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {creating && <CreateModal onClose={() => { setCreating(false); reload(); }} />}
      {viewing && <ViewModal id={viewing} canOperate={canOperate} onClose={() => { setViewing(null); reload(); }} />}
      {deploying && <DeployModal bp={deploying} onClose={() => { setDeploying(null); reload(); }} />}
    </>
  );
}

function CreateModal({ onClose }: { onClose: () => void }) {
  const ranges = useApi<{ ranges: { name: string }[] }>("/api/ranges");
  const [mode, setMode] = useState<"range" | "yaml">("range");
  const [id, setId] = useState("");
  const [range, setRange] = useState("");
  const [yaml, setYaml] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (mode === "range") await post("/api/blueprints/from-range", { range, id });
      else await put(`/api/blueprints/${id}`, { yaml });
      onClose();
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="New blueprint" onClose={onClose} wide footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !/^[a-z][a-z0-9-]{0,30}[a-z0-9]$/.test(id) || (mode === "range" ? !range : !yaml.trim())}
          onClick={save}>{busy ? "Saving…" : "Save blueprint"}</button>
      </>
    }>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === "range"} className={mode === "range" ? "active" : ""} onClick={() => setMode("range")}>From a range</button>
        <button role="tab" aria-selected={mode === "yaml"} className={mode === "yaml" ? "active" : ""} onClick={() => setMode("yaml")}>Paste a spec</button>
      </div>
      <div className="stack" style={{ marginTop: 12 }}>
        <label>Blueprint id
          <input value={id} onChange={(e) => setId(e.target.value.toLowerCase())} placeholder="web-lab" autoFocus />
        </label>
        {mode === "range" ? (
          <label>Range
            <select value={range} onChange={(e) => setRange(e.target.value)}>
              <option value="">Choose a range…</option>
              {ranges.data?.ranges.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
            </select>
          </label>
        ) : (
          <label>Range spec (YAML)
            <textarea className="mono" rows={16} value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
          </label>
        )}
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}

function ViewModal({ id, canOperate, onClose }: { id: string; canOperate: boolean; onClose: () => void }) {
  const { data, error } = useApi<{ id: string; yaml: string; description: string; copies: string[]; topology: Topology }>(`/api/blueprints/${id}`);
  const [err, setErr] = useState<ApiError | null>(null);
  const [confirm, setConfirm] = useState(false);
  const remove = async () => {
    try {
      await del(`/api/blueprints/${id}`);
      onClose();
    } catch (e) {
      setErr(e as ApiError);
    }
  };
  return (
    <Modal title={`Blueprint ${id}`} onClose={onClose} wide footer={
      <>
        {canOperate && !confirm && <button className="btn danger" onClick={() => setConfirm(true)}>Delete…</button>}
        {confirm && <button className="btn danger" onClick={remove}>Delete the blueprint (its copies stay)</button>}
        <button className="btn primary" onClick={onClose}>Done</button>
      </>
    }>
      <ErrorBox error={error || err} />
      {!data ? <Loading /> : (
        <>
          <div className="muted">{data.description}</div>
          <TopologyMap topology={data.topology} />
          <pre className="mono" style={{ maxHeight: 280, overflow: "auto" }}>{data.yaml}</pre>
        </>
      )}
    </Modal>
  );
}

function DeployModal({ bp, onClose }: { bp: Blueprint; onClose: () => void }) {
  const [mode, setMode] = useState<"numbered" | "students">("students");
  const [prefix, setPrefix] = useState(bp.id.slice(0, 8).replace(/-+$/, ""));
  const [count, setCount] = useState(2);
  const [students, setStudents] = useState("");
  const [build, setBuild] = useState(true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<{ ranges: string[]; jobs: { range: string; job: string }[] } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const copies = useMemo(() => {
    if (mode === "numbered") {
      return Array.from({ length: Math.max(0, Math.min(count, 50)) }, (_, i) => ({ name: `${prefix}-${String(i + 1).padStart(2, "0")}`, student: null }));
    }
    return students.split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter(Boolean)
      .map((s) => ({ name: `${prefix}-${s}`, student: s }));
  }, [mode, prefix, count, students]);
  const invalid = copies.filter((c) => !NAME_RE.test(c.name));

  const doPreview = async () => {
    setBusy(true);
    setErr(null);
    setPreview(null);
    try {
      setPreview(await post<Preview>(`/api/blueprints/${bp.id}/preview`, { copies }));
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      setBusy(false);
    }
  };
  const doDeploy = async () => {
    if (!preview) return;
    setBusy(true);
    setErr(null);
    try {
      setResult(await post(`/api/blueprints/${bp.id}/deploy`, { copies, deploy_hash: preview.deploy_hash, build }));
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <Modal title={`Deployed ${result.ranges.length} ranges`} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>Done</button>}>
        <div className="muted">{build ? "Builds are queued and run one after another." : "The specs are saved; plan and build each range when you are ready."}</div>
        <ul>
          {result.ranges.map((r) => {
            const j = result.jobs.find((x) => x.range === r);
            return (
              <li key={r}>
                <button className="link mono" onClick={() => go(`ranges/${r}`)}>{r}</button>
                {j && <> · <button className="link" onClick={() => go(`jobs/${j.job}`)}>build job</button></>}
              </li>
            );
          })}
        </ul>
      </Modal>
    );
  }
  return (
    <Modal title={`Deploy copies of ${bp.id}`} onClose={onClose} wide footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn" disabled={busy || !copies.length || invalid.length > 0} onClick={doPreview}>
          {busy && !preview ? "Checking…" : "Preview"}
        </button>
        <button className="btn primary" disabled={busy || !preview || (build && !preview.ok)} onClick={doDeploy}>
          {build ? `Create and build ${copies.length} ranges` : `Create ${copies.length} range specs`}
        </button>
      </>
    }>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === "students"} className={mode === "students" ? "active" : ""} onClick={() => { setMode("students"); setPreview(null); }}>One per student</button>
        <button role="tab" aria-selected={mode === "numbered"} className={mode === "numbered" ? "active" : ""} onClick={() => { setMode("numbered"); setPreview(null); }}>Numbered copies</button>
      </div>
      <div className="row" style={{ gap: 12, marginTop: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label>Name prefix
          <input value={prefix} onChange={(e) => { setPrefix(e.target.value.toLowerCase()); setPreview(null); }} style={{ width: 140 }} />
        </label>
        {mode === "numbered" ? (
          <label>Copies
            <input type="number" min={1} max={50} value={count} onChange={(e) => { setCount(Number(e.target.value)); setPreview(null); }} style={{ width: 90 }} />
          </label>
        ) : (
          <label className="grow">Students (one per line or comma separated; lowercase)
            <textarea rows={3} value={students} onChange={(e) => { setStudents(e.target.value); setPreview(null); }} placeholder={"alice\nbob"} />
          </label>
        )}
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={build} onChange={(e) => setBuild(e.target.checked)} /> Build now
        </label>
      </div>
      <div className="small muted" style={{ marginTop: 6 }}>
        Ranges: {copies.map((c) => c.name).join(", ") || "—"}
        {invalid.length > 0 && <span className="error-text"> · too long or invalid: {invalid.map((c) => c.name).join(", ")} (2-15 characters)</span>}
      </div>
      <ErrorBox error={err} />
      {preview && (
        <>
          {preview.cluster_error && <div className="alert warn">Cluster not reachable ({preview.cluster_error}): copies can be saved but not built.</div>}
          {preview.capacity && !preview.capacity.within_limit &&
            <div className="alert error">Together the copies need {preview.capacity.additional_memory_mib} MiB more memory than the node may use.</div>}
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Range</th><th>Networks</th><th>Plan</th></tr></thead>
              <tbody>
                {preview.copies.map((c) => (
                  <tr key={c.name}>
                    <td className="mono">{c.name}{c.student && <div className="muted small">for {c.student}</div>}</td>
                    <td className="small mono">
                      {Object.entries(c.allocation).map(([seg, a]) => (
                        <div key={seg}>{seg}: {a.cidr ?? a.network}{a.vlan ? ` · VLAN ${a.vlan}` : ""}{a.peers ? ` · peers ${a.peers.join(", ")}` : ""}</div>
                      ))}
                    </td>
                    <td className="small">
                      {c.ok ? (c.plan_summary ? Object.entries(c.plan_summary).map(([k, v]) => `${v} ${k}`).join(", ") : "—")
                        : c.errors.map((e, i) => <div key={i} className="error-text">{e.message}</div>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
