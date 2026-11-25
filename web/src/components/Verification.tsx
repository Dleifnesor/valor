// *grabs the verification report* OwO web/src/components/Verification.tsx: a range's VERIFICATION section! ^w^
// What the last verification found: the connectivity and isolation tests, the security baseline on each host, and how
// that maps onto compliance framework requirements. Before the first verification it lists the tests that WILL run
// *wags tail*
import { openTask, when } from "../hooks";
// when (dates), openTask, StateBadge, and the range detail and verification types UwU
import { StateBadge } from "./ui";
import type { Detail } from "../pages/RangeDetail";
import type { VerifyError, VerifySummary } from "../types";

/** A verification in a few words: "passed · 12/12 tests", "failed · 10/12 tests", or "could not run". */
// verifyLabel: for the range list and the summary; a verification that stopped with an error has no counts >w<
export function verifyLabel(result: { ok: boolean; summary?: VerifySummary | null }): string {
  if (!result.summary) return result.ok ? "passed" : "could not run";
  return `${result.ok ? "passed" : "failed"} · ${result.summary.tests_passed}/${result.summary.tests_total} tests`;
}

/** Checks that could not run (a VM's guest agent failing): they count as failed, and this says why. */
// CouldNotRun: one line per VM and check, with the hint when there is one OwO
function CouldNotRun({ errors }: { errors: VerifyError[] }) {
  return (
    <div className="alert error">
      Some checks could not run, so they count as failed:
      <ul>
        {errors.map((error, i) => (
          <li key={i}><b>{error.host}</b> ({error.check}): {error.message}
            {error.hint && <div className="small">Hint: {error.hint}</div>}</li>
        ))}
      </ul>
    </div>
  );
}

// A range's Verification section: what the last `valor verify` found - the connectivity and isolation tests, the
// security baseline on each host, and how that maps onto the compliance frameworks' requirements. Before the
// first verification it lists the tests that will run.

/** The tests a verification runs (from the spec, plus the automatic isolation tests). */
// Tests: the planned tests: name, from, to, protocol/port, expected result, and where the test comes from >w<
function Tests({ data }: { data: Detail }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Test</th><th>From</th><th>To</th><th>Check</th><th>Expect</th><th>Source</th></tr></thead>
        <tbody>
          {/* One row per test */}
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
// Verification: the section OwO
export function Verification({ data }: { data: Detail }) {
  const result = data.verify;
  // Never verified: the planned tests ^w^
  if (!result) {
    return (
      <div className="card-body stack">
        {/* A note, then the list */}
        <div className="muted">Not verified yet. These are the tests the verification runs after a build:</div>
        <Tests data={data} />
      </div>
    );
  }
  // Could not run at all (e.g. a VM was off): why, and the tests the next verification runs ^w^
  if (!result.summary) {
    const at = result.finished ?? result.started;
    return (
      <div className="card-body stack">
        <div className="alert error">
          The last verification could not run{at ? ` (${when(at)})` : ""}: {result.message ?? result.error ?? "unknown error"}
          {result.hint && <div className="small">Hint: {result.hint}</div>}
          {result.job && <div className="small"><button className="link" onClick={() => openTask(result.job!)}>Open the task</button></div>}
        </div>
        <div className="muted">These are the tests the verification runs:</div>
        <Tests data={data} />
      </div>
    );
  }
  const summary = result.summary;
  const baseline = result.baseline ?? {};
  // the baseline table: one row per control (in first-seen order), one column per host
  // Collect the controls (rows) and hosts (columns) for the baseline table UwU
  const hosts = Object.keys(baseline);
  const controls: { control: string; title: string; area: string }[] = [];
  for (const rows of Object.values(baseline)) {
    for (const row of rows) if (!controls.find((known) => known.control === row.control)) controls.push(row);
  }
  // The results >w<
  return (
    <div className="card-body stack">
      {/* Summary: passed/failed, tests, isolation, baseline, and when */}
      <div className="row">
        <StateBadge state={result.ok ? "ok" : "failed"} label={result.ok ? "passed" : "failed"} />
        <span>tests {summary.tests_passed}/{summary.tests_total}</span>
        <span>· isolation {summary.isolation_passed}/{summary.isolation_total}</span>
        <span>· baseline {summary.baseline_passed}/{summary.baseline_total}</span>
        {result.finished && <span className="muted">· {when(result.finished)}</span>}
      </div>
      {/* Checks that could not run, then the baseline claim (e.g. "aligned with... not a certification") */}
      {result.errors && result.errors.length > 0 && <CouldNotRun errors={result.errors} />}
      {summary.baseline_claim && <div className="muted small">Baseline results are {summary.baseline_claim}.</div>}
      {/* Every test: name, from, target, expected, observed (with detail), pass/fail */}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Test</th><th>From</th><th>Target</th><th>Expect</th><th>Observed</th><th></th></tr></thead>
          <tbody>
            {(result.tests ?? []).map((test) => (
              <tr key={test.name}>
                <td>{test.name}</td><td>{test.from}</td><td className="mono">{test.target}</td><td>{test.expect}</td>
                <td>{test.observed} <span className="muted small">({test.detail})</span></td>
                <td>{test.pass ? <span className="badge ok">pass</span> : <span className="badge bad">fail</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* The baseline table: one row per control, one column per host (– = doesn't apply) */}
      {hosts.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Baseline control</th><th>Area</th>{hosts.map((host) => <th key={host}>{host}</th>)}</tr></thead>
            <tbody>
              {controls.map((control) => (
                <tr key={control.control}>
                  <td>{control.title}</td><td className="muted">{control.area}</td>
                  {hosts.map((host) => {
                    const row = baseline[host].find((x) => x.control === control.control);
                    return <td key={host}>{!row ? "–" : row.after === "pass" ? <span className="badge ok">pass</span> : <span className="badge bad">{row.after}</span>}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* The compliance report, then all tests with their source (folded) */}
      {result.compliance && <Compliance report={result.compliance} />}
      <details className="check-notes">
        <summary>All {data.tests.length} tests with where they come from (the spec or automatic)</summary>
        <Tests data={data} />
      </details>
    </div>
  );
}

// Evidence / Requirement / ComplianceReport: the compliance report's shape OwO
interface Evidence { kind: "control" | "design"; title: string; pass: boolean; host?: string; control?: string; check?: string; detail?: string }
interface Requirement { id: string; title: string; status: "pass" | "partial" | "fail"; evidence: Evidence[]; no_evidence_on?: string[] }
export interface ComplianceReport {
  claim: string;
  frameworks: Record<string, { title: string; about: string; requirements: Requirement[]; passed: number; partial: number;
    failed: number; not_covered: { id: string; title: string }[] }>;
}

// STATUS_BADGE: badge colors for requirement statuses ^w^
const STATUS_BADGE = { pass: "ok", partial: "warn", fail: "bad" } as const;

/** Verification evidence per framework requirement (baseline controls on each host + design checks). */
// Compliance: per framework (folded unless there's only one): pass/partial/fail/not-covered counts, then each
// requirement with its evidence, then what VALOR doesn't cover UwU
function Compliance({ report }: { report: ComplianceReport | { error: string } }) {
  if ("error" in report) return <div className="alert warn small">Compliance report unavailable: {report.error}</div>;
  return (
    <div className="stack">
      {/* Heading and the claim */}
      <h3 style={{ margin: "8px 0 0" }}>Compliance frameworks</h3>
      <div className="muted small">Results are {report.claim}.</div>
      {/* One fold per framework */}
      {Object.entries(report.frameworks).map(([id, framework]) => (
        <details key={id} className="framework" open={Object.keys(report.frameworks).length === 1}>
          {/* The fold's summary: title and counts */}
          <summary>
            <b>{framework.title}</b>
            <span className="badge ok">{framework.passed} pass</span>
            {framework.partial > 0 && <span className="badge warn">{framework.partial} partial</span>}
            {framework.failed > 0 && <span className="badge bad">{framework.failed} fail</span>}
            {framework.not_covered.length > 0 && <span className="badge">{framework.not_covered.length} not covered</span>}
          </summary>
          {/* About it, then the requirement table */}
          <div className="muted small">{framework.about}</div>
          <div className="table-wrap">
            <table className="compliance">
              <thead><tr><th>Requirement</th><th>Status</th><th>Evidence</th></tr></thead>
              <tbody>
                {/* One row per requirement: id and title, status, evidence (and hosts without evidence) */}
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
          {/* What VALOR doesn't cover (organizational, or outside a lab) */}
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
// Evidences: a requirement's evidence, one line per control (its hosts grouped, failing ones marked) or design check >w<
function Evidences({ items }: { items: Evidence[] }) {
  const groups = new Map<string, { title: string; pass: boolean; hosts: string[]; detail?: string }>();
  for (const item of items) {
    const key = item.kind === "control" ? `c:${item.control}` : `d:${item.check}`;
    const group = groups.get(key) ?? { title: item.title, pass: true, hosts: [], detail: item.detail };
    group.pass = group.pass && item.pass;
    if (item.host) group.hosts.push(item.pass ? item.host : `${item.host} (fail)`);
    groups.set(key, group);
  }
  // The list OwO
  return (
    <ul className="evidence">
      {/* ✓/✗, the control's title, the hosts and any detail */}
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
