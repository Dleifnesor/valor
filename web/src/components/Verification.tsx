import { when } from "../hooks";
import { StateBadge } from "./ui";
import type { Detail } from "../pages/RangeDetail";

// A range's Verification section: what the last `valor verify` found - the connectivity and isolation tests, the
// security baseline on each host, and how that maps onto the compliance frameworks' requirements. Before the
// first verification it lists the tests that will run.

/** The tests a verification runs (from the spec, plus the automatic isolation tests). */
function Tests({ data }: { data: Detail }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Test</th><th>From</th><th>To</th><th>Check</th><th>Expect</th><th>Source</th></tr></thead>
        <tbody>
          {data.tests.map((test) => (
            <tr key={test.name}>
              <td>{test.name}</td><td>{test.from}</td><td>{test.to}</td>
              <td className="mono">{test.proto}{test.port ? `/${test.port}` : ""}</td>
              <td><span className={`badge ${test.expect === "open" ? "ok" : "bad"}`}>{test.expect}</span></td>
              <td className="muted">{test.origin}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The last verification: connectivity tests, the security baseline per host, and the compliance mapping. */
export function Verification({ data }: { data: Detail }) {
  const result = data.verify;
  if (!result) {
    return (
      <div className="card-body stack">
        <div className="muted">Not verified yet. These are the tests the verification runs after a build:</div>
        <Tests data={data} />
      </div>
    );
  }
  // the baseline table: one row per control (in first-seen order), one column per host
  const hosts = Object.keys(result.baseline || {});
  const controls: { control: string; title: string; area: string }[] = [];
  for (const rows of Object.values(result.baseline || {})) {
    for (const row of rows) if (!controls.find((known) => known.control === row.control)) controls.push(row);
  }
  return (
    <div className="card-body stack">
      <div className="row">
        <StateBadge state={result.ok ? "ok" : "failed"} label={result.ok ? "passed" : "failed"} />
        <span>tests {result.summary.tests_passed}/{result.summary.tests_total}</span>
        <span>· isolation {result.summary.isolation_passed}/{result.summary.isolation_total}</span>
        <span>· baseline {result.summary.baseline_passed}/{result.summary.baseline_total}</span>
        <span className="muted">· {when(result.finished)}</span>
      </div>
      {result.summary.baseline_claim && <div className="muted small">Baseline results are {result.summary.baseline_claim}.</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Test</th><th>From</th><th>Target</th><th>Expect</th><th>Observed</th><th></th></tr></thead>
          <tbody>
            {result.tests.map((test) => (
              <tr key={test.name}>
                <td>{test.name}</td><td>{test.from}</td><td className="mono">{test.target}</td><td>{test.expect}</td>
                <td>{test.observed} <span className="muted small">({test.detail})</span></td>
                <td>{test.pass ? <span className="badge ok">pass</span> : <span className="badge bad">fail</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hosts.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Baseline control</th><th>Area</th>{hosts.map((host) => <th key={host}>{host}</th>)}</tr></thead>
            <tbody>
              {controls.map((control) => (
                <tr key={control.control}>
                  <td>{control.title}</td><td className="muted">{control.area}</td>
                  {hosts.map((host) => {
                    const row = result.baseline[host].find((x) => x.control === control.control);
                    return <td key={host}>{!row ? "–" : row.after === "pass" ? <span className="badge ok">pass</span> : <span className="badge bad">{row.after}</span>}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result.compliance && <Compliance report={result.compliance} />}
      <details className="check-notes">
        <summary>All {data.tests.length} tests with where they come from (the spec or automatic)</summary>
        <Tests data={data} />
      </details>
    </div>
  );
}

interface Evidence { kind: "control" | "design"; title: string; pass: boolean; host?: string; control?: string; check?: string; detail?: string }
interface Requirement { id: string; title: string; status: "pass" | "partial" | "fail"; evidence: Evidence[]; no_evidence_on?: string[] }
export interface ComplianceReport {
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
      {Object.entries(report.frameworks).map(([id, framework]) => (
        <details key={id} className="framework" open={Object.keys(report.frameworks).length === 1}>
          <summary>
            <b>{framework.title}</b>
            <span className="badge ok">{framework.passed} pass</span>
            {framework.partial > 0 && <span className="badge warn">{framework.partial} partial</span>}
            {framework.failed > 0 && <span className="badge bad">{framework.failed} fail</span>}
            {framework.not_covered.length > 0 && <span className="badge">{framework.not_covered.length} not covered</span>}
          </summary>
          <div className="muted small">{framework.about}</div>
          <div className="table-wrap">
            <table className="compliance">
              <thead><tr><th>Requirement</th><th>Status</th><th>Evidence</th></tr></thead>
              <tbody>
                {framework.requirements.map((requirement) => (
                  <tr key={requirement.id}>
                    <td><span className="mono">{requirement.id}</span> <span className="muted">{requirement.title}</span></td>
                    <td><span className={`badge ${STATUS_BADGE[requirement.status]}`}>{requirement.status}</span></td>
                    <td className="small">
                      <Evidences items={requirement.evidence} />
                      {!!requirement.no_evidence_on?.length && <div className="muted">No evidence on {requirement.no_evidence_on.join(", ")}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {framework.not_covered.length > 0 && (
            <details className="check-notes">
              <summary>Not covered by VALOR ({framework.not_covered.length}) - organizational or outside a lab build</summary>
              <ul>{framework.not_covered.map((item) => <li key={item.id}><span className="mono">{item.id}</span> {item.title}</li>)}</ul>
            </details>
          )}
        </details>
      ))}
    </div>
  );
}

/** A requirement's evidence, one line per control (its hosts grouped) or design check. */
function Evidences({ items }: { items: Evidence[] }) {
  const groups = new Map<string, { title: string; pass: boolean; hosts: string[]; detail?: string }>();
  for (const item of items) {
    const key = item.kind === "control" ? `c:${item.control}` : `d:${item.check}`;
    const group = groups.get(key) ?? { title: item.title, pass: true, hosts: [], detail: item.detail };
    group.pass = group.pass && item.pass;
    if (item.host) group.hosts.push(item.pass ? item.host : `${item.host} (fail)`);
    groups.set(key, group);
  }
  return (
    <ul className="evidence">
      {[...groups.values()].map((group, i) => (
        <li key={i} className={group.pass ? "" : "bad"}>
          {group.pass ? "✓" : "✗"} {group.title}
          {group.hosts.length > 0 && <span className="muted"> - {group.hosts.join(", ")}</span>}
          {group.detail && <span className="muted"> - {group.detail}</span>}
        </li>
      ))}
    </ul>
  );
}
