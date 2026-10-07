import { useState } from "react";
import { ApiError, post } from "../api";
import { Topology } from "../types";
import { ago, go, useApi, when } from "../hooks";
import { ErrorBox, Loading, Modal, StateBadge } from "../components/ui";
import { RangeMapTab } from "../components/RangeEditing";
import { PlanModal, PlanResponse } from "../components/PlanModal";
import { HostPower, LoginModal, PowerMenu, SnapshotsTab } from "../components/RangeActions";
import { AccessTab } from "../components/Access";

interface Detail {
  name: string;
  yaml: string;
  spec: any;
  version: string;
  vms: Record<string, { vmid: number; status: string; converged: boolean; spec: string }>;
  cluster_error: string | null;
  topology: Topology;
  tests: { name: string; from: string; to: string; proto: string; port: number | null; expect: string; origin: string }[];
  history: { kind: string; started: string; ok: boolean; seconds: number; error?: string; message?: string; job?: string }[];
  journal: string | null;
  verify: null | {
    ok: boolean;
    finished?: string;
    summary: Record<string, any>;
    tests: { name: string; from: string; target: string; expect: string; observed: string; detail: string; pass: boolean }[];
    baseline: Record<string, { control: string; title: string; area: string; after: string }[]>;
    compliance?: ComplianceReport | { error: string };
  };
  last_apply: null | { ok: boolean; started: string; seconds: number; error?: string; message?: string };
  logins: boolean;
}


const TABS = ["Map", "Hosts", "Access", "Snapshots", "Tests", "Verification", "Spec", "Journal", "History"] as const;

export function RangeDetail({ name, canOperate }: { name: string; canOperate: boolean }) {
  const { data, error, loading, reload } = useApi<Detail>(`/api/ranges/${encodeURIComponent(name)}`, 20000);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Map");
  const [planning, setPlanning] = useState(false);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [destroying, setDestroying] = useState(false);
  const [showLogin, setShowLogin] = useState(false);

  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;

  const built = Object.keys(data.vms).length > 0;
  const deployed = Object.values(data.vms)[0]?.spec;
  const doPlan = async () => {
    setPlanning(true);
    setActionError(null);
    try {
      setPlan(await post<PlanResponse>(`/api/ranges/${name}/plan`));
    } catch (e) {
      setActionError(e as ApiError);
    } finally {
      setPlanning(false);
    }
  };
  const doVerify = async () => {
    setActionError(null);
    try {
      const r = await post<{ job: string }>(`/api/ranges/${name}/verify`);
      go(`jobs/${r.job}`);
    } catch (e) {
      setActionError(e as ApiError);
    }
  };

  return (
    <>
      <div className="row">
        <div className="grow">
          <div className="muted">{data.spec.description || "No description."}</div>
          <div className="row small" style={{ marginTop: 6, gap: 8 }}>
            <span className="badge">spec {data.version}</span>
            {built ? <span className="badge ok">{Object.values(data.vms).filter((v) => v.status === "running").length}/{Object.keys(data.vms).length} VMs running</span>
              : <span className="badge">not built</span>}
            {built && deployed && deployed !== data.version && <span className="badge info">spec changed since the last build</span>}
            {data.verify && <StateBadge state={data.verify.ok ? "ok" : "failed"}
              label={`verification ${data.verify.ok ? "PASS" : "FAIL"} · ${ago(data.verify.finished)}`} />}
          </div>
        </div>
        {canOperate && (
          <div className="row">
            <button className="btn" onClick={() => go(`ranges/${name}/edit`)}>Edit spec</button>
            {built && data.logins && <button className="btn" onClick={() => setShowLogin(true)}>Login</button>}
            {built && <PowerMenu range={name} onError={setActionError} />}
            {built && <button className="btn" onClick={doVerify}>Verify</button>}
            <button className="btn primary" onClick={doPlan} disabled={planning}>
              {planning ? <><span className="spinner" /> Planning…</> : built ? "Plan changes" : "Plan build"}
            </button>
            {built && <button className="btn danger" onClick={() => setDestroying(true)}>Destroy</button>}
          </div>
        )}
      </div>
      <ErrorBox error={actionError} />
      {data.cluster_error && <div className="alert warn">Live VM status unavailable: {data.cluster_error}</div>}

      <section className="card">
        <div className="tabs" role="tablist" style={{ padding: "0 12px" }}>
          {TABS.map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
          ))}
        </div>
        {tab === "Map" && <RangeMapTab name={name} canOperate={canOperate}
          onConsole={canOperate ? (h) => go(`ranges/${name}/console/${h}/vnc`) : undefined} />}
        {tab === "Hosts" && <Hosts data={data} canOperate={canOperate} onError={setActionError} />}
        {tab === "Access" && <AccessTab range={name} canOperate={canOperate} />}
        {tab === "Snapshots" && <SnapshotsTab range={name} canOperate={canOperate} />}
        {tab === "Tests" && <Tests data={data} />}
        {tab === "Verification" && <Verification data={data} />}
        {tab === "Spec" && <div className="card-body"><pre className="mono">{data.yaml}</pre></div>}
        {tab === "Journal" && (
          <div className="card-body">{data.journal ? <pre className="mono">{data.journal}</pre> : <div className="empty">No journal yet (written after the first build).</div>}</div>
        )}
        {tab === "History" && <History data={data} />}
      </section>

      {plan && <PlanModal name={name} res={plan} onClose={() => setPlan(null)} />}
      {destroying && <DestroyModal name={name} onClose={() => { setDestroying(false); reload(); }} />}
      {showLogin && <LoginModal range={name} onClose={() => setShowLogin(false)} />}
    </>
  );
}

function Hosts({ data, canOperate, onError }: { data: Detail; canOperate: boolean; onError: (e: ApiError) => void }) {
  const rows = [{ name: "rtr", segment: "all", address: data.spec.segments.map((s: any) => s.cidr).join(", "), os: data.spec.router.os,
    cores: data.spec.router.cores, memory: data.spec.router.memory, disk: data.spec.router.disk, roles: [{ name: "router" }] },
    ...data.spec.hosts];
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Host</th><th>Segment</th><th>Address</th><th>OS</th><th>Size</th><th>Roles</th><th>VM</th><th></th></tr></thead>
        <tbody>
          {rows.map((h: any) => {
            const vm = data.vms[h.name];
            return (
              <tr key={h.name}>
                <td><b>{h.name}</b></td>
                <td>{h.segment}</td>
                <td className="mono">{h.address}</td>
                <td>{h.os}</td>
                <td className="nowrap">{h.cores} vCPU · {h.memory} MiB · {h.disk} GiB</td>
                <td>{(h.roles || []).map((r: any) => r.name).join(", ") || "–"}</td>
                <td className="nowrap">{vm ? <><StateBadge state={vm.status === "running" ? "ok" : "unknown"} label={vm.status} /> <span className="muted small">#{vm.vmid}</span></> : "–"}</td>
                <td>{vm && canOperate && <HostPower range={data.name} host={h.name} status={vm.status} onError={onError} />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Tests({ data }: { data: Detail }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Test</th><th>From</th><th>To</th><th>Check</th><th>Expect</th><th>Source</th></tr></thead>
        <tbody>
          {data.tests.map((t) => (
            <tr key={t.name}>
              <td>{t.name}</td><td>{t.from}</td><td>{t.to}</td>
              <td className="mono">{t.proto}{t.port ? `/${t.port}` : ""}</td>
              <td><span className={`badge ${t.expect === "open" ? "ok" : "bad"}`}>{t.expect}</span></td>
              <td className="muted">{t.origin}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Verification({ data }: { data: Detail }) {
  const v = data.verify;
  if (!v) return <div className="empty">Not verified yet.</div>;
  const hosts = Object.keys(v.baseline || {});
  const controls: { control: string; title: string; area: string }[] = [];
  for (const rows of Object.values(v.baseline || {})) for (const r of rows) if (!controls.find((c) => c.control === r.control)) controls.push(r);
  return (
    <div className="card-body stack">
      <div className="row">
        <StateBadge state={v.ok ? "ok" : "failed"} label={v.ok ? "PASS" : "FAIL"} />
        <span>tests {v.summary.tests_passed}/{v.summary.tests_total}</span>
        <span>· isolation {v.summary.isolation_passed}/{v.summary.isolation_total}</span>
        <span>· baseline {v.summary.baseline_passed}/{v.summary.baseline_total}</span>
        <span className="muted">· {when(v.finished)}</span>
      </div>
      {v.summary.baseline_claim && <div className="muted small">Baseline results are {v.summary.baseline_claim}.</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Test</th><th>From</th><th>Target</th><th>Expect</th><th>Observed</th><th></th></tr></thead>
          <tbody>
            {v.tests.map((t) => (
              <tr key={t.name}>
                <td>{t.name}</td><td>{t.from}</td><td className="mono">{t.target}</td><td>{t.expect}</td>
                <td>{t.observed} <span className="muted small">({t.detail})</span></td>
                <td>{t.pass ? <span className="badge ok">pass</span> : <span className="badge bad">fail</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hosts.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Baseline control</th><th>Area</th>{hosts.map((h) => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {controls.map((c) => (
                <tr key={c.control}>
                  <td>{c.title}</td><td className="muted">{c.area}</td>
                  {hosts.map((h) => {
                    const r = v.baseline[h].find((x) => x.control === c.control);
                    return <td key={h}>{!r ? "–" : r.after === "pass" ? <span className="badge ok">pass</span> : <span className="badge bad">{r.after}</span>}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {v.compliance && <Compliance report={v.compliance} />}
    </div>
  );
}

interface Evidence { kind: "control" | "design"; title: string; pass: boolean; host?: string; control?: string; check?: string; detail?: string }
interface Requirement { id: string; title: string; status: "pass" | "partial" | "fail"; evidence: Evidence[]; no_evidence_on?: string[] }
interface ComplianceReport {
  claim: string;
  frameworks: Record<string, { title: string; about: string; requirements: Requirement[]; passed: number; partial: number;
    failed: number; not_covered: { id: string; title: string }[] }>;
}

const STATUS_BADGE = { pass: "ok", partial: "warn", fail: "bad" } as const;

/** Verification evidence per framework requirement (baseline controls on each host + design checks). */
function Compliance({ report }: { report: ComplianceReport | { error: string } }) {
  if ("error" in report) return <div className="alert warn small">Compliance report unavailable: {report.error}</div>;
  return (
    <div className="stack">
      <h3 style={{ margin: "8px 0 0" }}>Compliance frameworks</h3>
      <div className="muted small">Results are {report.claim}.</div>
      {Object.entries(report.frameworks).map(([id, f]) => (
        <details key={id} className="framework" open>
          <summary>
            <b>{f.title}</b>
            <span className="badge ok">{f.passed} pass</span>
            {f.partial > 0 && <span className="badge warn">{f.partial} partial</span>}
            {f.failed > 0 && <span className="badge bad">{f.failed} fail</span>}
            {f.not_covered.length > 0 && <span className="badge">{f.not_covered.length} not covered</span>}
          </summary>
          <div className="muted small">{f.about}</div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Requirement</th><th>Status</th><th>Evidence</th></tr></thead>
              <tbody>
                {f.requirements.map((r) => (
                  <tr key={r.id}>
                    <td><span className="mono">{r.id}</span> <span className="muted">{r.title}</span></td>
                    <td><span className={`badge ${STATUS_BADGE[r.status]}`}>{r.status}</span></td>
                    <td className="small">
                      <Evidences items={r.evidence} />
                      {!!r.no_evidence_on?.length && <div className="muted">No evidence on {r.no_evidence_on.join(", ")}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {f.not_covered.length > 0 && (
            <details className="check-notes">
              <summary>Not covered by VALOR ({f.not_covered.length}) - organizational or outside a lab build</summary>
              <ul>{f.not_covered.map((n) => <li key={n.id}><span className="mono">{n.id}</span> {n.title}</li>)}</ul>
            </details>
          )}
        </details>
      ))}
    </div>
  );
}

function Evidences({ items }: { items: Evidence[] }) {
  // one line per control (hosts grouped) or design check
  const groups = new Map<string, { title: string; pass: boolean; hosts: string[]; detail?: string }>();
  for (const e of items) {
    const key = e.kind === "control" ? `c:${e.control}` : `d:${e.check}`;
    const g = groups.get(key) ?? { title: e.title, pass: true, hosts: [], detail: e.detail };
    g.pass = g.pass && e.pass;
    if (e.host) g.hosts.push(e.pass ? e.host : `${e.host} (fail)`);
    groups.set(key, g);
  }
  return (
    <ul className="evidence">
      {[...groups.values()].map((g, i) => (
        <li key={i} className={g.pass ? "" : "bad"}>
          {g.pass ? "✓" : "✗"} {g.title}
          {g.hosts.length > 0 && <span className="muted"> - {g.hosts.join(", ")}</span>}
          {g.detail && <span className="muted"> - {g.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

function History({ data }: { data: Detail }) {
  if (!data.history.length) return <div className="empty">No builds recorded yet.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>When</th><th>Action</th><th>Result</th><th>Duration</th><th>Details</th></tr></thead>
        <tbody>
          {[...data.history].reverse().map((h, i) => (
            <tr key={i} className={h.job ? "clickable" : ""} onClick={() => h.job && go(`jobs/${h.job}`)}>
              <td className="nowrap">{when(h.started)}</td><td>{h.kind}</td>
              <td><StateBadge state={h.ok ? "ok" : "failed"} label={h.ok ? "OK" : "failed"} /></td>
              <td>{h.seconds ? `${h.seconds} s` : "–"}</td>
              <td className="small">{h.error ? `${h.error}: ${h.message ?? ""}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DestroyModal({ name, onClose }: { name: string; onClose: () => void }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const run = async () => {
    setBusy(true);
    try {
      const r = await post<{ job: string }>(`/api/ranges/${name}/destroy`, { confirm });
      go(`jobs/${r.job}`);
    } catch (e) {
      setErr(e as ApiError);
      setBusy(false);
    }
  };
  return (
    <Modal title={`Destroy ${name}?`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger solid" disabled={confirm !== name || busy} onClick={run}>Destroy all VMs</button>
      </>
    }>
      <div>This deletes every VM of the range (its spec stays, so you can build it again). Type <b>{name}</b> to confirm.</div>
      <input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoFocus aria-label="Range name" />
      <ErrorBox error={err} />
    </Modal>
  );
}
