import { Check, RangeRow } from "../types";
import { ago, go, useApi } from "../hooks";
import { Card, ErrorBox, Icon, Loading, StateBadge } from "../components/ui";
import { showMenu } from "../components/ContextMenu";
import { rangeMenu } from "../components/RangeActions";

// Datacenter -> Summary: VALOR's health checks, the range node's resources, every range with its build and
// verification state, and the cluster settings VALOR builds with.

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

/** Datacenter -> Summary: health, VMs and resources at a glance, then every range. */
export function Dashboard({ ranges, canOperate }: { ranges: RangeRow[] | null; canOperate: boolean }) {
  const { data, error, loading } = useApi<Status>("/api/status", 30000);
  if (loading && !data) return <Loading label="Checking the cluster…" />;
  if (!data) return <ErrorBox error={error} />;
  const cluster = data.cluster;
  const problems = data.checks.filter((check) => check.status === "problem");
  const vms = (ranges ?? []).reduce((sum, range) => sum + range.vms, 0);
  const running = (ranges ?? []).reduce((sum, range) => sum + range.running, 0);
  return (
    <>
      <div className="summary-row">
        <Card title="Health">
          <div className="health">
            <span className={`health-mark ${problems.length ? "bad" : "ok"}`}><Icon name={problems.length ? "info" : "check"} size={30} /></span>
            <div>
              <b>{problems.length ? `${problems.length} of ${data.checks.length} checks need attention` : `All ${data.checks.length} checks pass`}</b>
              {problems.length > 0 && <div className="error-text small">{problems.map((check) => check.name).join(", ")}</div>}
              <div className="muted small">
                Jobs: {data.jobs.running ? `${data.jobs.running} running` : "idle"}{data.jobs.queued ? `, ${data.jobs.queued} queued` : ""}
              </div>
            </div>
          </div>
        </Card>
        <Card title="Guests">
          <dl className="kv">
            <dt>Ranges</dt><dd>{ranges?.length ?? "–"} ({(ranges ?? []).filter((range) => range.vms).length} built)</dd>
            <dt>Running VMs</dt><dd><StateBadge state={running ? "ok" : "unknown"} label={String(running)} /></dd>
            <dt>Stopped VMs</dt><dd><StateBadge state="unknown" label={String(vms - running)} /></dd>
          </dl>
        </Card>
        <Card title="Resources">
          {cluster ? (
            <dl className="kv">
              <dt>Range node</dt><dd>{cluster.node} · {cluster.cpu_threads} CPU threads</dd>
              <dt>Memory</dt><dd>{gib(cluster.memory_free_mib)} free of {gib(cluster.memory_total_mib)}</dd>
              <dt>Storage</dt><dd>{cluster.storage.available_gib.toFixed(0)} GiB free on {cluster.storage.id}</dd>
            </dl>
          ) : <div className="muted">The cluster did not answer.</div>}
        </Card>
      </div>

      <Card title="Ranges" bodyClass="">
        {!ranges ? <Loading /> : !ranges.length ? (
          <div className="empty">No ranges yet.{canOperate && <> <button className="link" onClick={() => go("ranges/new")}>Create range</button> or
            use the <button className="link" onClick={() => go("builder")}>chat builder</button>.</>}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Description</th><th>Layout</th><th>VMs</th><th>Last build</th><th>Verification</th></tr></thead>
              <tbody>
                {ranges.map((range) => (
                  <tr key={range.name} className={range.has_spec ? "clickable" : ""} onClick={() => range.has_spec && go(`ranges/${range.name}`)}
                    onContextMenu={(e) => range.has_spec && showMenu(e, rangeMenu(range, canOperate))}>
                    <td className="nowrap">
                      <b>{range.name}</b>
                      {!range.has_spec && <span className="badge warn" style={{ marginLeft: 8 }}>no spec</span>}
                      {range.version && range.deployed_version && range.version !== range.deployed_version && (
                        <span className="badge info" style={{ marginLeft: 8 }} title="The spec changed since the last build">changed</span>
                      )}
                    </td>
                    <td className="fill"><span className="clip">{range.error ?? range.description}</span></td>
                    <td className="nowrap">{range.segments ?? "–"} segments · {range.hosts ?? "–"} hosts</td>
                    <td className="nowrap">{range.vms ? <StateBadge state={range.running === range.vms ? "ok" : range.running ? "warn" : "unknown"}
                      label={`${range.running}/${range.vms} running`} /> : <span className="muted">not built</span>}</td>
                    <td className="nowrap">{range.last_apply ? <><StateBadge state={range.last_apply.ok ? "ok" : "failed"}
                      label={range.last_apply.ok ? "succeeded" : "failed"} /> <span className="muted small">· {ago(range.last_apply.started)}</span></> : "–"}</td>
                    <td className="nowrap">{range.last_verify ? <StateBadge state={range.last_verify.ok ? "ok" : "failed"}
                      label={`${range.last_verify.ok ? "passed" : "failed"} · ${range.last_verify.summary?.tests_passed}/${range.last_verify.summary?.tests_total} tests`} /> : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="dash">
        <Card title="Health checks" bodyClass="">
          <table>
            <tbody>
              {data.checks.map((check) => (
                <tr key={check.name}>
                  <td className="nowrap"><StateBadge state={check.status === "ok" ? "ok" : check.status === "problem" ? "failed" : "unknown"} label={check.name} /></td>
                  <td className="fill">
                    {check.detail}
                    {check.hint && <div className="muted small">{check.hint}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        {cluster && (
          <Card title="What VALOR uses on the cluster">
            <dl className="kv">
              <dt>Segment bridge</dt><dd className="mono">{cluster.segment_bridge.name} (VLANs {cluster.segment_bridge.usable_vlans})</dd>
              <dt>Uplink bridge</dt><dd className="mono">{cluster.uplink_bridge.name}</dd>
              <dt>Reserved networks</dt><dd className="mono">{cluster.reserved_networks.join(", ") || "–"}</dd>
              <dt>Default OS</dt><dd>{cluster.default_os}</dd>
              <dt>Templates</dt>
              <dd>
                <div className="row" style={{ gap: 4, columnGap: 12 }}>
                  {Object.entries(cluster.templates).map(([os, template]) => (
                    <StateBadge key={os} state={template.present ? "ok" : "unknown"} label={`${os}${template.present ? "" : " (not built)"}`} />
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
