import { go, ago, useApi } from "../hooks";
import { Card, ErrorBox, Icon, Loading, StateBadge } from "../components/ui";

interface RangeRow {
  name: string;
  has_spec: boolean;
  description?: string;
  version?: string;
  deployed_version?: string;
  segments?: number;
  hosts?: number;
  vms: number;
  running: number;
  error?: string;
  last_apply: null | { ok: boolean; started: string; seconds: number; error?: string };
  last_verify: null | { ok: boolean; finished: string; summary: Record<string, any> };
}

export function Ranges({ canOperate }: { canOperate: boolean }) {
  const { data, error, loading, reload } = useApi<{ ranges: RangeRow[]; cluster_error: string | null }>("/api/ranges", 15000);
  return (
    <Card
      title="Ranges"
      bodyClass=""
      actions={
        <div className="row">
          <button className="btn" onClick={reload}><Icon name="refresh" size={16} /> Refresh</button>
          {canOperate && <button className="btn primary" onClick={() => go("ranges/new")}><Icon name="plus" size={16} /> New range</button>}
        </div>
      }
    >
      {loading && !data && <Loading />}
      <div style={{ padding: error || data?.cluster_error ? "12px 18px 0" : 0 }}>
        <ErrorBox error={error} />
        {data?.cluster_error && <div className="alert warn">Live VM status unavailable: {data.cluster_error}</div>}
      </div>
      {data && !data.ranges.length && (
        <div className="empty">
          No ranges yet. {canOperate ? "Create one with New range (the chat builder arrives in milestone 2)." : ""}
        </div>
      )}
      {data && data.ranges.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Range</th>
                <th>Layout</th>
                <th>VMs</th>
                <th>Last build</th>
                <th>Verification</th>
              </tr>
            </thead>
            <tbody>
              {data.ranges.map((r) => (
                <tr key={r.name} className="clickable" onClick={() => r.has_spec && go(`ranges/${r.name}`)}>
                  <td>
                    <b>{r.name}</b>
                    {!r.has_spec && <span className="badge warn" style={{ marginLeft: 8 }}>no spec</span>}
                    {r.version && r.deployed_version && r.version !== r.deployed_version && (
                      <span className="badge info" style={{ marginLeft: 8 }} title="The spec changed since the last build">changed</span>
                    )}
                    <div className="muted small">{r.error ?? r.description}</div>
                  </td>
                  <td className="nowrap">{r.segments ?? "–"} segments · {r.hosts ?? "–"} hosts</td>
                  <td className="nowrap">{r.vms ? `${r.running}/${r.vms} running` : "not built"}</td>
                  <td className="nowrap">
                    {r.last_apply ? (
                      <>
                        <StateBadge state={r.last_apply.ok ? "ok" : "failed"} label={r.last_apply.ok ? "OK" : "failed"} />{" "}
                        <span className="muted small">{ago(r.last_apply.started)}</span>
                      </>
                    ) : "–"}
                  </td>
                  <td className="nowrap">
                    {r.last_verify ? (
                      <StateBadge state={r.last_verify.ok ? "ok" : "failed"}
                        label={`${r.last_verify.ok ? "PASS" : "FAIL"} · ${r.last_verify.summary?.tests_passed}/${r.last_verify.summary?.tests_total} tests`} />
                    ) : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
