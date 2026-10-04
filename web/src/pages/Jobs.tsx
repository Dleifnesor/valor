import { JobSummary } from "../types";
import { ago, go, useApi, when } from "../hooks";
import { Card, ErrorBox, Loading, StateBadge } from "../components/ui";

const target = (t: Record<string, any>) => t.range ?? String(t.spec ?? "").replace(/\.yaml$/, "");

export function Jobs() {
  const { data, error, loading } = useApi<{ jobs: JobSummary[] }>("/api/jobs?limit=100", 5000);
  if (loading && !data) return <Loading />;
  return (
    <Card title="Engine jobs" bodyClass="">
      <ErrorBox error={error} />
      {data && !data.jobs.length && <div className="empty">No jobs yet.</div>}
      {data && data.jobs.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Job</th><th>Action</th><th>Range</th><th>State</th><th>Started</th><th>Finished</th></tr></thead>
            <tbody>
              {data.jobs.map((j) => (
                <tr key={j.id} className="clickable" onClick={() => go(`jobs/${j.id}`)}>
                  <td className="mono">{j.id}</td>
                  <td>{j.kind}{j.target.verify ? " + verify" : ""}</td>
                  <td>{target(j.target)}</td>
                  <td><StateBadge state={j.state} /></td>
                  <td className="nowrap">{ago(j.created)}</td>
                  <td className="nowrap">{j.finished ? when(j.finished) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

interface JobDetailData extends JobSummary {
  origin: string;
  progress: { steps_done: number; current: string | null };
  recent_events: { t: string; event: string; step?: string; host?: string; name?: string; passed?: boolean;
    seconds?: number; error?: string; message?: string; total?: number }[];
  result?: Record<string, any>;
}

function eventLine(e: JobDetailData["recent_events"][number]): [string, string] {
  switch (e.event) {
    case "step_start": return ["…", `${e.step}${e.host ? ` [${e.host}]` : ""}`];
    case "step_done": return ["✓", `${e.step}${e.host ? ` [${e.host}]` : ""}${e.seconds !== undefined ? ` (${e.seconds} s)` : ""}`];
    case "step_failed": return ["✗", `${e.step}${e.host ? ` [${e.host}]` : ""}: ${e.error ?? ""} ${e.message ?? ""}`];
    case "spec": return ["•", `spec ${(e as any).range} version ${(e as any).version}`];
    case "test": return [e.passed ? "✓" : "✗", `test: ${e.name}`];
    case "baseline": return ["•", `baseline ${e.host}: ${e.passed}/${e.total} controls pass`];
    default: return ["•", `${e.event} ${JSON.stringify({ ...e, t: undefined, event: undefined })}`];
  }
}

// A step's "started" line is noise once the step has finished: show only steps still running.
function visible(events: JobDetailData["recent_events"]) {
  const finished = new Set(events.filter((e) => e.event === "step_done" || e.event === "step_failed")
    .map((e) => `${e.step}|${e.host ?? ""}`));
  return events.filter((e) => e.event !== "step_start" || !finished.has(`${e.step}|${e.host ?? ""}`));
}

export function JobDetail({ id }: { id: string }) {
  const { data, error } = useApi<JobDetailData>(`/api/jobs/${id}?tail=400`, 2500);
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const r = data.result;
  const name = target(data.target);
  return (
    <>
      <div className="row">
        <StateBadge state={data.state} />
        <b>{data.kind}{data.target.verify ? " + verify" : ""} · {name}</b>
        <span className="muted">by {String(data.origin).replace(/^web:/, "")} · {when(data.created)}</span>
        <span className="grow" />
        {name && <a className="btn" href={`#/ranges/${name}`}>Open range</a>}
      </div>
      {r && !r.ok && (
        <div className="alert error">
          <b>{r.error}</b>: {r.message}
          {r.step && <div className="small">Step: {r.step}{r.host ? ` on ${r.host}` : ""}</div>}
          {r.hint && <div className="small">Hint: {r.hint}</div>}
        </div>
      )}
      {r && r.ok && (
        <div className="alert ok">
          Finished{r.seconds ? ` in ${r.seconds} s` : ""}.
          {r.verify && ` Verification ${r.verify.ok ? "passed" : "failed"}: tests ${r.verify.summary.tests_passed}/${r.verify.summary.tests_total}, baseline ${r.verify.summary.baseline_passed}/${r.verify.summary.baseline_total}.`}
          {r.summary && !r.verify && ` Tests ${r.summary.tests_passed}/${r.summary.tests_total}, baseline ${r.summary.baseline_passed}/${r.summary.baseline_total}.`}
          {r.changed === false && " No changes were needed."}
        </div>
      )}
      <Card title={data.state === "running" ? `Running: ${data.progress.current ?? "starting"}` : "Events"}>
        {!data.recent_events.length ? <div className="muted">Waiting for the worker…</div> : (
          <div className="events">
            {visible(data.recent_events).map((e, i) => {
              const [mark, text] = eventLine(e);
              const cls = mark === "✓" ? "var(--ok)" : mark === "✗" ? "var(--bad)" : "var(--muted)";
              return (
                <div key={i} className="ev">
                  <span className="muted">{e.t}</span>
                  <span style={{ color: cls }}>{mark}</span>
                  <span>{text}</span>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </>
  );
}
