import { Check, JobSummary } from "../types";
import { ago, go, useApi } from "../hooks";
import { Card, ErrorBox, Loading, StateBadge } from "../components/ui";
import { jobTarget } from "./Jobs";

interface RangeRow {
  name: string;
  has_spec: boolean;
  description?: string;
  vms: number;
  running: number;
  last_verify: null | { ok: boolean; finished: string; summary: Record<string, any> };
}

interface Status {
  version: string;
  instance: string;
  hostname: string;
  checks: (Check & Record<string, any>)[];
  jobs: { queued: number; running: number };
  cluster: null | {
    node: string;
    cpu_threads: number;
    memory_total_mib: number;
    memory_free_mib: number;
    storage: { id: string; available_gib: number };
    segment_bridge: { name: string; usable_vlans: string };
    uplink_bridge: { name: string };
    reserved_networks: string[];
    templates: Record<string, { present: boolean; vmid: number | null; description: string }>;
    ranges: Record<string, { vms: number; running: number }>;
    default_os: string;
  };
}

const gib = (mib: number) => `${(mib / 1024).toFixed(0)} GiB`;

export function Dashboard() {
  const { data, error, loading } = useApi<Status>("/api/status", 30000);
  const list = useApi<{ ranges: RangeRow[] }>("/api/ranges", 30000);
  const jobs = useApi<{ jobs: JobSummary[] }>("/api/jobs?limit=8", 10000);
  if (loading && !data) return <Loading label="Checking the cluster…" />;
  if (!data) return <ErrorBox error={error} />;
  const c = data.cluster;
  const problems = data.checks.filter((x) => x.status === "problem");
  const ranges = c ? Object.values(c.ranges) : [];
  return (
    <>
      {problems.length > 0 && (
        <div className="alert error">
          {problems.length === 1 ? "One check needs attention" : `${problems.length} checks need attention`}:{" "}
          {problems.map((p) => p.name).join(", ")}.
        </div>
      )}
      {c && (
        <div className="card stats">
          <div className="stat">
            <div className="label">Range node</div>
            <div className="value">{c.node}</div>
            <div className="sub">{c.cpu_threads} CPU threads</div>
          </div>
          <div className="stat">
            <div className="label">Free memory</div>
            <div className="value">{gib(c.memory_free_mib)}</div>
            <div className="sub">of {gib(c.memory_total_mib)}</div>
          </div>
          <div className="stat">
            <div className="label">Range storage</div>
            <div className="value">{c.storage.available_gib.toFixed(0)} GiB</div>
            <div className="sub">free on {c.storage.id}</div>
          </div>
          <div className="stat">
            <div className="label">Ranges</div>
            <div className="value">{ranges.length}</div>
            <div className="sub">
              {ranges.reduce((a, r) => a + r.running, 0)} of {ranges.reduce((a, r) => a + r.vms, 0)} VMs running ·{" "}
              {data.jobs.running ? "a job is running" : data.jobs.queued ? `${data.jobs.queued} queued` : "idle"}
            </div>
          </div>
        </div>
      )}
      <div className="dash">
        <Card title="Ranges" bodyClass="" actions={<a className="small" href="#/ranges">All ranges</a>}>
          {list.data && !list.data.ranges.length && <div className="empty">No ranges yet.</div>}
          {!!list.data?.ranges.length && (
            <table>
              <tbody>
                {list.data.ranges.map((r) => (
                  <tr key={r.name} className="clickable" onClick={() => r.has_spec && go(`ranges/${r.name}`)}>
                    <td className="fill"><b>{r.name}</b><div className="muted small clip">{r.description}</div></td>
                    <td className="nowrap">{r.vms ? <StateBadge state={r.running === r.vms ? "ok" : r.running ? "warn" : "unknown"}
                      label={`${r.running}/${r.vms} running`} /> : <span className="muted">not built</span>}</td>
                    <td className="nowrap">{r.last_verify ? <StateBadge state={r.last_verify.ok ? "ok" : "failed"}
                      label={`${r.last_verify.ok ? "passed" : "failed"} ${r.last_verify.summary?.tests_passed}/${r.last_verify.summary?.tests_total}`} />
                      : <span className="muted">not verified</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Recent jobs" bodyClass="" actions={<a className="small" href="#/jobs">All jobs</a>}>
          {jobs.data && !jobs.data.jobs.length && <div className="empty">No jobs yet.</div>}
          {!!jobs.data?.jobs.length && (
            <table>
              <tbody>
                {jobs.data.jobs.map((j) => (
                  <tr key={j.id} className="clickable" onClick={() => go(`jobs/${j.id}`)}>
                    <td className="fill"><div className="clip">{j.kind}{j.target.verify ? " + verify" : ""} <span className="muted">· {jobTarget(j.target)}</span></div></td>
                    <td className="nowrap"><StateBadge state={j.state} /></td>
                    <td className="nowrap muted right hide-narrow">{ago(j.created)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Health checks" bodyClass="" actions={problems.length === 0 &&
          <StateBadge state="ok" label={`all ${data.checks.length} pass`} />}>
          <table>
            <tbody>
              {data.checks.map((x) => (
                <tr key={x.name}>
                  <td style={{ width: 24 }}><span className={`dot ${x.status === "ok" ? "ok" : x.status === "problem" ? "bad" : ""}`} /></td>
                  <td className="nowrap"><b>{x.name}</b></td>
                  <td>
                    {x.detail}
                    {x.hint && <div className="muted small">{x.hint}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        {c && (
          <Card title="Cluster resources for VALOR">
            <dl className="kv">
              <dt>Segment bridge</dt><dd className="mono">{c.segment_bridge.name} (VLANs {c.segment_bridge.usable_vlans})</dd>
              <dt>Uplink bridge</dt><dd className="mono">{c.uplink_bridge.name}</dd>
              <dt>Reserved networks</dt><dd className="mono">{c.reserved_networks.join(", ") || "–"}</dd>
              <dt>Default OS</dt><dd>{c.default_os}</dd>
              <dt>Templates</dt>
              <dd>
                <div className="row" style={{ gap: 6 }}>
                  {Object.entries(c.templates).map(([os, t]) => (
                    <StateBadge key={os} state={t.present ? "ok" : "unknown"} label={`${os}${t.present ? "" : " (not built)"}`} />
                  ))}
                </div>
              </dd>
              <dt>VALOR VM</dt><dd>{data.hostname} · version {data.version}</dd>
            </dl>
          </Card>
        )}
      </div>
    </>
  );
}
