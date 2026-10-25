import { useState } from "react";
import { ApiError, post } from "../api";
import { ago, go, openTask, useApi, when } from "../hooks";
import { Topology } from "../types";
import { Card, DropButton, ErrorBox, Icon, Loading, MenuEntry, Modal, StateBadge, View } from "../components/ui";
import { RangeMapTab } from "../components/RangeEditing";
import { Markdown } from "../components/Markdown";
import { TopologyMap } from "../components/Topology";
import { PlanModal, PlanResponse } from "../components/PlanModal";
import { HostPower, LoginModal, SnapshotsTab, runJob } from "../components/RangeActions";
import { AccessTab } from "../components/Access";
import { RangeEditor } from "./RangeEditor";

export interface Detail {
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

const SECTIONS: Omit<MenuEntry, "href">[] = [
  { id: "summary", label: "Summary", icon: "info" },
  { id: "map", label: "Map", icon: "map" },
  { id: "verification", label: "Verification", icon: "check" },
  { id: "snapshots", label: "Snapshots", icon: "camera" },
  { id: "access", label: "Access", icon: "key" },
  { id: "spec", label: "Spec", icon: "file" },
  { id: "journal", label: "Journal", icon: "book" },
  { id: "history", label: "History", icon: "clock" },
];
// older links and the routes that open inside a section
const ALIAS: Record<string, string> = { hosts: "summary", tests: "verification", edit: "spec", chat: "map" };

/** A range, as Proxmox shows a node: its actions in the title bar, its sections on the left. */
export function RangeView({ name, section, canOperate, chatId }: { name: string; section?: string; canOperate: boolean; chatId?: number }) {
  const { data, error, loading, reload } = useApi<Detail>(`/api/ranges/${encodeURIComponent(name)}`, 20000);
  const [planning, setPlanning] = useState(false);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [destroying, setDestroying] = useState(false);
  const [showLogin, setShowLogin] = useState(false);
  const active = SECTIONS.some((x) => x.id === section) ? section! : ALIAS[section ?? ""] ?? "summary";
  const menu = SECTIONS.map((x) => ({ ...x, href: `ranges/${name}${x.id === "summary" ? "" : `/${x.id}`}` }));
  const title = `Range '${name}'`;
  if (!data) return <View title={title} menu={menu} active={active}>{loading ? <Loading /> : <ErrorBox error={error} />}</View>;

  const vmCount = Object.keys(data.vms).length;
  const running = Object.values(data.vms).filter((v) => v.status === "running").length;
  const built = vmCount > 0;
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
  const power = (action: string) => runJob(post(`/api/ranges/${name}/power`, { action }), setActionError);
  const tools = canOperate && (
    <>
      {built && <button className="btn" onClick={() => power("start")} disabled={running === vmCount}><Icon name="play" size={14} /> Start</button>}
      {built && (
        <DropButton label="Shutdown" icon="power" disabled={!running} items={[
          { label: "Shut down all VMs", icon: "power", onClick: () => power("shutdown") },
          { label: "Reboot all VMs", icon: "refresh", onClick: () => power("reboot") },
          { label: "Force stop all VMs", icon: "stop", danger: true, onClick: () => power("stop") },
        ]} />
      )}
      {built && data.logins && <button className="btn" onClick={() => setShowLogin(true)}><Icon name="key" size={14} /> Login</button>}
      {built && <button className="btn" onClick={() => runJob(post(`/api/ranges/${name}/verify`), setActionError)}><Icon name="check" size={14} /> Verify</button>}
      <button className="btn primary" onClick={doPlan} disabled={planning}>
        {planning ? <><span className="spinner" /> Planning…</> : built ? "Plan changes" : "Plan build"}
      </button>
      <DropButton label="More" items={[
        { label: "Edit spec", icon: "edit", onClick: () => go(`ranges/${name}/edit`) },
        ...(built ? [{ label: "Destroy", icon: "trash" as const, danger: true, onClick: () => setDestroying(true) }] : []),
      ]} />
    </>
  );

  return (
    <View title={title} tools={tools} menu={menu} active={active} fill={active === "map"}>
      <ErrorBox error={actionError} />
      {data.cluster_error && <div className="alert warn">Live VM status unavailable: {data.cluster_error}</div>}
      {active === "summary" && <Summary data={data} canOperate={canOperate} onError={setActionError} />}
      {active === "map" && (
        <div className="map-page">
          <RangeMapTab name={name} canOperate={canOperate} chatId={chatId}
            onConsole={canOperate ? (h) => go(`ranges/${name}/vm/${h}/console`) : undefined} />
        </div>
      )}
      {active === "verification" && <section className="card"><Verification data={data} /></section>}
      {active === "snapshots" && <section className="card"><SnapshotsTab range={name} canOperate={canOperate} /></section>}
      {active === "access" && <section className="card"><AccessTab range={name} canOperate={canOperate} /></section>}
      {active === "spec" && (section === "edit" ? <RangeEditor name={name} canEdit={canOperate} /> : (
        <Card title={`Spec, version ${data.version}`} actions={canOperate &&
          <button className="btn small" onClick={() => go(`ranges/${name}/edit`)}><Icon name="edit" size={14} /> Edit</button>}>
          <pre className="mono">{data.yaml}</pre>
        </Card>
      ))}
      {active === "journal" && <Journal data={data} />}
      {active === "history" && <section className="card"><History data={data} /></section>}

      {plan && <PlanModal name={name} res={plan} onClose={() => setPlan(null)} />}
      {destroying && <DestroyModal name={name} onClose={() => { setDestroying(false); reload(); }} />}
      {showLogin && <LoginModal range={name} onClose={() => setShowLogin(false)} />}
    </View>
  );
}

/** The router and the hosts as rows: the spec's view of each VM next to the cluster's. */
export function machinesOf(data: Detail) {
  return [{ name: "rtr", segment: "all", address: data.spec.segments.map((s: any) => s.cidr).join(", "), os: data.spec.router.os,
    cores: data.spec.router.cores, memory: data.spec.router.memory, disk: data.spec.router.disk, roles: [{ name: "router" }] },
    ...data.spec.hosts] as { name: string; segment: string; address: string; os: string; cores: number; memory: number; disk: number; roles?: { name: string }[] }[];
}

function Summary({ data, canOperate, onError }: { data: Detail; canOperate: boolean; onError: (e: ApiError) => void }) {
  const vmCount = Object.keys(data.vms).length;
  const running = Object.values(data.vms).filter((v) => v.status === "running").length;
  const deployed = Object.values(data.vms)[0]?.spec;
  return (
    <>
      <div className="summary-row two">
        <Card title="Status">
          <dl className="kv">
            <dt>Status</dt>
            <dd>{vmCount ? <StateBadge state={running === vmCount ? "ok" : running ? "warn" : "unknown"} label={`${running} of ${vmCount} VMs running`} />
              : "not built"}</dd>
            <dt>Spec version</dt>
            <dd><span className="mono">{data.version}</span>{vmCount > 0 && deployed && deployed !== data.version &&
              <> · <StateBadge state="warn" label="changed since the last build" /></>}</dd>
            <dt>Layout</dt><dd>{data.spec.segments.length} segments · {data.spec.hosts.length} hosts and the router</dd>
            <dt>Last build</dt>
            <dd>{data.last_apply ? <><StateBadge state={data.last_apply.ok ? "ok" : "failed"} label={data.last_apply.ok ? "succeeded" : "failed"} />
              {" "}· {ago(data.last_apply.started)}</> : "–"}</dd>
            <dt>Verification</dt>
            <dd>{data.verify ? <><StateBadge state={data.verify.ok ? "ok" : "failed"}
              label={`${data.verify.ok ? "passed" : "failed"} ${data.verify.summary?.tests_passed ?? ""}/${data.verify.summary?.tests_total ?? ""} tests`} />
              {" "}· {ago(data.verify.finished)}</> : "–"}</dd>
          </dl>
        </Card>
        <Card title="Notes">
          <div className="notes">{data.spec.description || <span className="muted">No description.</span>}</div>
        </Card>
      </div>
      <Card title="Virtual machines" bodyClass="">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>VMID</th><th>Status</th><th>Segment</th><th>Address</th><th>OS</th><th>Size</th><th>Services</th><th /></tr></thead>
            <tbody>
              {machinesOf(data).map((h) => {
                const vm = data.vms[h.name];
                return (
                  <tr key={h.name} className="clickable" onClick={() => go(`ranges/${data.name}/vm/${h.name}`)}>
                    <td className="nowrap"><b>{h.name}</b></td>
                    <td>{vm ? vm.vmid : "–"}</td>
                    <td className="nowrap">{vm ? <StateBadge state={vm.status === "running" ? "ok" : "unknown"} label={vm.status} /> : <span className="muted">not built</span>}</td>
                    <td>{h.segment}</td>
                    <td className="mono">{h.address}</td>
                    <td>{h.os}</td>
                    <td className="nowrap">{h.cores} vCPU · {h.memory} MiB · {h.disk} GiB</td>
                    <td>{(h.roles || []).map((r) => r.name).join(", ") || "–"}</td>
                    <td onClick={(e) => e.stopPropagation()}>{vm && canOperate && <HostPower range={data.name} host={h.name} status={vm.status} onError={onError} />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
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
  if (!v) {
    return (
      <div className="card-body stack">
        <div className="muted">Not verified yet. These are the tests the verification runs after a build:</div>
        <Tests data={data} />
      </div>
    );
  }
  const hosts = Object.keys(v.baseline || {});
  const controls: { control: string; title: string; area: string }[] = [];
  for (const rows of Object.values(v.baseline || {})) for (const r of rows) if (!controls.find((c) => c.control === r.control)) controls.push(r);
  return (
    <div className="card-body stack">
      <div className="row">
        <StateBadge state={v.ok ? "ok" : "failed"} label={v.ok ? "passed" : "failed"} />
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
      <details className="check-notes">
        <summary>All {data.tests.length} tests with where they come from (the spec or automatic)</summary>
        <Tests data={data} />
      </details>
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
        <details key={id} className="framework" open={Object.keys(report.frameworks).length === 1}>
          <summary>
            <b>{f.title}</b>
            <span className="badge ok">{f.passed} pass</span>
            {f.partial > 0 && <span className="badge warn">{f.partial} partial</span>}
            {f.failed > 0 && <span className="badge bad">{f.failed} fail</span>}
            {f.not_covered.length > 0 && <span className="badge">{f.not_covered.length} not covered</span>}
          </summary>
          <div className="muted small">{f.about}</div>
          <div className="table-wrap">
            <table className="compliance">
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

/** The build journal (journals/<range>.md) rendered; its Mermaid topology is shown as the range map. */
function Journal({ data }: { data: Detail }) {
  if (!data.journal) return <Card title="Journal"><div className="empty">No journal yet (written after the first build).</div></Card>;
  const text = data.journal;
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    a.download = `${data.name}.md`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const fence = (lang: string, code: string) => lang === "mermaid" ? (
    <>
      <div className="card md-map"><TopologyMap topology={data.topology} /></div>
      <details className="check-notes"><summary>Mermaid source (as in the file)</summary><pre className="md-code">{code}</pre></details>
    </>
  ) : undefined;
  return (
    <Card title="Journal" actions={<button className="btn small" onClick={download}><Icon name="download" size={14} /> Download .md</button>}>
      <Markdown text={text} fence={fence} />
    </Card>
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
            <tr key={i} className={h.job ? "clickable" : ""} onClick={() => h.job && openTask(h.job)}>
              <td className="nowrap">{when(h.started)}</td><td>{h.kind}</td>
              <td><StateBadge state={h.ok ? "ok" : "failed"} label={h.ok ? "succeeded" : "failed"} /></td>
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
      onClose();
      openTask(r.job);
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
      <input name="confirm-name" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoFocus aria-label="Range name" />
      <ErrorBox error={err} />
    </Modal>
  );
}
