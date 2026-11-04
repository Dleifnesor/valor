// *makes a stack of identical labs* OwO web/src/pages/Blueprints.tsx is the BLUEPRINTS page! ^w^
// Keep a range spec as a blueprint and deploy copies of it: numbered, or one per student. Each copy is an ordinary
// range with its own VLANs and networks; deploying runs exactly what the preview showed *wags tail*
import { useMemo, useState } from "react";
// API calls, hooks, the map types, UI pieces and the map UwU
import { ApiError, del, post, put } from "../api";
import { go, useApi, when } from "../hooks";
import { Topology } from "../types";
import { ErrorBox, Loading, Modal } from "../components/ui";
import { TopologyMap } from "../components/Topology";

// Blueprints: keep a range spec and deploy copies of it - numbered, or one per student. Each copy is an ordinary
// range with its own VLANs and networks; deploying runs exactly what the preview showed.

// Blueprint: one entry in the list (invalid ones carry their error) >w<
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

// CopyPreview: one previewed copy: name, student, its VLANs/networks, YAML, problems and plan summary OwO
interface CopyPreview {
  name: string;
  student: string | null;
  allocation: Record<string, { vlan?: number; cidr?: string; network?: string; peers?: string[] }>;
  yaml: string;
  ok: boolean;
  errors: { location: string; message: string; hint?: string }[];
  plan_summary: Record<string, number> | null;
}

// Preview: all copies, a cluster error, the memory check, whether everything's OK, and the hash deploy must send
// back ^w^
interface Preview {
  copies: CopyPreview[];
  cluster_error: string | null;
  capacity: { additional_memory_mib: number; memory_total_mib: number; within_limit: boolean } | null;
  ok: boolean;
  deploy_hash: string;
}

// NAME_RE: valid range names (2-15 chars) UwU
const NAME_RE = /^[a-z][a-z0-9-]{0,13}[a-z0-9]$/;

// Blueprints: the list page; which dialog is open (new / view / deploy) >w<
export function Blueprints({ canOperate }: { canOperate: boolean }) {
  const { data, error, loading, reload } = useApi<{ blueprints: Blueprint[] }>("/api/blueprints", 30000);
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [deploying, setDeploying] = useState<Blueprint | null>(null);
  if (loading && !data) return <Loading />;
  return (
    <>
      {/* An explanation, and New blueprint for operators */}
      <div className="row">
        <div className="grow muted">
          A blueprint is a range spec you can deploy many times: numbered copies, or one per student. Every copy gets
          its own VLANs and networks (hosts keep their addresses' last part) and, with WireGuard access, a peer for
          its student.
        </div>
        {canOperate && <button className="btn primary" onClick={() => setCreating(true)}>New blueprint</button>}
      </div>
      {/* Errors, then the table (or an empty message) */}
      <ErrorBox error={error} />
      <section className="card">
        {!data?.blueprints.length ? (
          <div className="empty">No blueprints yet. Save a range as a blueprint, or paste a spec.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Blueprint</th><th>Contents</th><th>Copies</th><th>Updated</th><th></th></tr></thead>
              <tbody>
                {/* One row per blueprint: id (opens it) and description, contents, its copies (links), updated, and Deploy */}
                {data.blueprints.map((blueprint) => (
                  <tr key={blueprint.id}>
                    {/* Id and description (or why it's invalid) */}
                    <td>
                      <button className="link mono" onClick={() => setViewing(blueprint.id)}>{blueprint.id}</button>
                      <div className="muted small">{blueprint.valid ? blueprint.description : `invalid: ${blueprint.error}`}</div>
                    </td>
                    {/* Hosts, segments, OSes, and a WireGuard badge */}
                    <td className="small">
                      {blueprint.valid && <>{blueprint.hosts} hosts · {blueprint.segments} segments · {blueprint.os?.join(", ")}
                        {blueprint.wireguard && <span className="badge ok" style={{ marginLeft: 6 }}>WireGuard</span>}</>}
                    </td>
                    {/* Ranges made from it, as links */}
                    <td className="small">
                      {blueprint.copies?.length ? blueprint.copies.map((copy) => (
                        <button key={copy} className="link mono" style={{ marginRight: 6 }} onClick={() => go(`ranges/${copy}`)}>{copy}</button>
                      )) : <span className="muted">none</span>}
                    </td>
                    {/* Updated, and Deploy copies… for operators */}
                    <td className="small">{when(blueprint.updated)}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {canOperate && blueprint.valid && <button className="btn small primary" onClick={() => setDeploying(blueprint)}>Deploy copies…</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {/* The three dialogs; closing one reloads the list */}
      {creating && <CreateModal onClose={() => { setCreating(false); reload(); }} />}
      {viewing && <ViewModal id={viewing} canOperate={canOperate} onClose={() => { setViewing(null); reload(); }} />}
      {deploying && <DeployModal bp={deploying} onClose={() => { setDeploying(null); reload(); }} />}
    </>
  );
}

// CreateModal: a new blueprint, from an existing range or from pasted YAML OwO
function CreateModal({ onClose }: { onClose: () => void }) {
  const ranges = useApi<{ ranges: { name: string }[] }>("/api/ranges");
  const [mode, setMode] = useState<"range" | "yaml">("range");
  const [id, setId] = useState("");
  const [range, setRange] = useState("");
  const [yaml, setYaml] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // save(): from-range or PUT the YAML, then close ^w^
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
  // The dialog; Save needs a valid id plus a range or some YAML UwU
  return (
    <Modal title="New blueprint" onClose={onClose} wide footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !/^[a-z][a-z0-9-]{0,30}[a-z0-9]$/.test(id) || (mode === "range" ? !range : !yaml.trim())}
          onClick={save}>{busy ? "Saving…" : "Save blueprint"}</button>
      </>
    }>
      {/* Two tabs: From a range / Paste a spec */}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === "range"} className={mode === "range" ? "active" : ""} onClick={() => setMode("range")}>From a range</button>
        <button role="tab" aria-selected={mode === "yaml"} className={mode === "yaml" ? "active" : ""} onClick={() => setMode("yaml")}>Paste a spec</button>
      </div>
      {/* The id, then a range picker or a YAML box, then errors */}
      <div className="stack" style={{ marginTop: 12 }}>
        <label>Blueprint id
          <input name="blueprint-id" value={id} onChange={(e) => setId(e.target.value.toLowerCase())} placeholder="web-lab" autoFocus />
        </label>
        {mode === "range" ? (
          <label>Range
            <select name="range" value={range} onChange={(e) => setRange(e.target.value)}>
              <option value="">Choose a range…</option>
              {ranges.data?.ranges.map((range) => <option key={range.name} value={range.name}>{range.name}</option>)}
            </select>
          </label>
        ) : (
          <label>Range spec (YAML)
            <textarea name="yaml" className="mono" rows={16} value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} />
          </label>
        )}
        <ErrorBox error={err} />
      </div>
    </Modal>
  );
}

// ViewModal: one blueprint: description, map and YAML, with Delete (its copies stay) >w<
function ViewModal({ id, canOperate, onClose }: { id: string; canOperate: boolean; onClose: () => void }) {
  const { data, error } = useApi<{ id: string; yaml: string; description: string; copies: string[]; topology: Topology }>(`/api/blueprints/${id}`);
  const [err, setErr] = useState<ApiError | null>(null);
  const [confirm, setConfirm] = useState(false);
  // remove(): delete it and close OwO
  const remove = async () => {
    try {
      await del(`/api/blueprints/${id}`);
      onClose();
    } catch (e) {
      setErr(e as ApiError);
    }
  };
  // The dialog; Delete asks once more ^w^
  return (
    <Modal title={`Blueprint ${id}`} onClose={onClose} wide footer={
      <>
        {canOperate && !confirm && <button className="btn danger" onClick={() => setConfirm(true)}>Delete…</button>}
        {confirm && <button className="btn danger" onClick={remove}>Delete the blueprint (its copies stay)</button>}
        <button className="btn primary" onClick={onClose}>Done</button>
      </>
    }>
      {/* Errors, then the description, the map and the YAML */}
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

// DeployModal: deploy copies UwU
function DeployModal({ bp, onClose }: { bp: Blueprint; onClose: () => void }) {
  // Settings (one per student or numbered), the prefix, count, students, build now?, the preview, the result, errors,
  // and busy >w<
  const [mode, setMode] = useState<"numbered" | "students">("students");
  const [prefix, setPrefix] = useState(bp.id.slice(0, 8).replace(/-+$/, ""));
  const [count, setCount] = useState(2);
  const [students, setStudents] = useState("");
  const [build, setBuild] = useState(true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<{ ranges: string[]; jobs: { range: string; job: string }[] } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  // copies: the planned names: "<prefix>-01".. or "<prefix>-<student>", at most 50 OwO
  const copies = useMemo(() => {
    if (mode === "numbered") {
      return Array.from({ length: Math.max(0, Math.min(count, 50)) }, (_, i) => ({ name: `${prefix}-${String(i + 1).padStart(2, "0")}`, student: null }));
    }
    return students.split(/[\s,]+/).map((student) => student.trim().toLowerCase()).filter(Boolean)
      .map((student) => ({ name: `${prefix}-${student}`, student }));
  }, [mode, prefix, count, students]);
  // Names that are too long or invalid ^w^
  const invalid = copies.filter((copy) => !NAME_RE.test(copy.name));

  // doPreview(): ask the server to plan every copy UwU
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
  // doDeploy(): deploy what was previewed (with its hash) >w<
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

  // Deployed: list the new ranges with links to their build jobs OwO
  if (result) {
    return (
      <Modal title={`Deployed ${result.ranges.length} ranges`} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>Done</button>}>
        {/* What happens next, then one link per range (and its build job) */}
        <div className="muted">{build ? "Builds are queued and run one after another." : "The specs are saved; plan and build each range when you are ready."}</div>
        <ul>
          {result.ranges.map((range) => {
            // This range's build job, if one was started ^w^
            const job = result.jobs.find((entry) => entry.range === range);
            return (
              <li key={range}>
                <button className="link mono" onClick={() => go(`ranges/${range}`)}>{range}</button>
                {job && <> · <button className="link" onClick={() => go(`jobs/${job.job}`)}>build job</button></>}
              </li>
            );
          })}
        </ul>
      </Modal>
    );
  }
  // The deploy form; footer: Cancel, Preview, and Create (and build) UwU
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
      {/* Two tabs: one per student / numbered copies (changing anything clears the preview) */}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === "students"} className={mode === "students" ? "active" : ""} onClick={() => { setMode("students"); setPreview(null); }}>One per student</button>
        <button role="tab" aria-selected={mode === "numbered"} className={mode === "numbered" ? "active" : ""} onClick={() => { setMode("numbered"); setPreview(null); }}>Numbered copies</button>
      </div>
      {/* Prefix, then count or students, then Build now */}
      <div className="row" style={{ gap: 12, marginTop: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label>Name prefix
          <input name="prefix" value={prefix} onChange={(e) => { setPrefix(e.target.value.toLowerCase()); setPreview(null); }} style={{ width: 140 }} />
        </label>
        {mode === "numbered" ? (
          <label>Copies
            <input name="count" type="number" min={1} max={50} value={count} onChange={(e) => { setCount(Number(e.target.value)); setPreview(null); }} style={{ width: 90 }} />
          </label>
        ) : (
          <label className="grow">Students (one per line or comma separated; lowercase)
            <textarea name="students" rows={3} value={students} onChange={(e) => { setStudents(e.target.value); setPreview(null); }} placeholder={"alice\nbob"} />
          </label>
        )}
        <label className="row" style={{ gap: 6 }}>
          <input name="build" type="checkbox" checked={build} onChange={(e) => setBuild(e.target.checked)} /> Build now
        </label>
      </div>
      {/* The names that will be made (and which are invalid) */}
      <div className="small muted" style={{ marginTop: 6 }}>
        Ranges: {copies.map((copy) => copy.name).join(", ") || "—"}
        {invalid.length > 0 && <span className="error-text"> · too long or invalid: {invalid.map((copy) => copy.name).join(", ")} (2-15 characters)</span>}
      </div>
      {/* Errors, then the preview */}
      <ErrorBox error={err} />
      {preview && (
        <>
          {/* Cluster unreachable, or not enough memory for all copies */}
          {preview.cluster_error && <div className="alert warn">Cluster not reachable ({preview.cluster_error}): copies can be saved but not built.</div>}
          {preview.capacity && !preview.capacity.within_limit &&
            <div className="alert error">Together the copies need {preview.capacity.additional_memory_mib} MiB more memory than the node may use.</div>}
          {/* One row per copy: name (and student), its networks, and its plan or problems */}
          <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Range</th><th>Networks</th><th>Plan</th></tr></thead>
              <tbody>
                {preview.copies.map((copy) => (
                  <tr key={copy.name}>
                    <td className="mono">{copy.name}{copy.student && <div className="muted small">for {copy.student}</div>}</td>
                    <td className="small mono">
                      {Object.entries(copy.allocation).map(([segment, allocation]) => (
                        <div key={segment}>{segment}: {allocation.cidr ?? allocation.network}{allocation.vlan ? ` · VLAN ${allocation.vlan}` : ""}{allocation.peers ? ` · peers ${allocation.peers.join(", ")}` : ""}</div>
                      ))}
                    </td>
                    <td className="small">
                      {copy.ok ? (copy.plan_summary ? Object.entries(copy.plan_summary).map(([change, count]) => `${count} ${change}`).join(", ") : "—")
                        : copy.errors.map((error, i) => <div key={i} className="error-text">{error.message}</div>)}
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
