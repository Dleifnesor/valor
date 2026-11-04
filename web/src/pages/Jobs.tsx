// *checks the job board* OwO web/src/pages/Jobs.tsx: the JOBS list and one job's details! ^w^
// Jobs = the last 100 engine jobs. JobDetail = one job's live progress, its result, and (for failed builds) the
// "fix it with the assistant" panel *wags tail*
import { useState } from "react";
// post/ApiError, JobSummary, hooks, UI pieces, and the task table helpers UwU
import { ApiError, post } from "../api";
import { JobSummary } from "../types";
import { go, useApi, when } from "../hooks";
import { Card, ErrorBox, Loading, StateBadge } from "../components/ui";
import { TaskTable, jobTarget, jobTitle, who } from "../components/TaskLog";

/** Datacenter -> Jobs: the last 100 engine jobs. */
// Jobs: the list, refreshed every 5 s >w<
export function Jobs() {
  const { data, error, loading } = useApi<{ jobs: JobSummary[] }>("/api/jobs?limit=100", 5000);
  if (loading && !data) return <Loading />;
  return (
    <Card title="Engine jobs" bodyClass="">
      {/* Errors, then the task table */}
      <ErrorBox error={error} />
      {data && <div className="table-wrap"><TaskTable jobs={data.jobs} /></div>}
    </Card>
  );
}

// JobDetailData: a job with its progress, newest events and result OwO
interface JobDetailData extends JobSummary {
  origin: string;
  progress: { steps_done: number; current: string | null };
  recent_events: { t: string; event: string; step?: string; host?: string; name?: string; passed?: boolean;
    seconds?: number; error?: string; message?: string; total?: number }[];
  result?: Record<string, any>;
}

// eventLine(): one event as a [mark, text] pair: … started, ✓ done, ✗ failed, • info; tests get ✓/✗ ^w^
function eventLine(entry: JobDetailData["recent_events"][number]): [string, string] {
  switch (entry.event) {
    case "step_start": return ["…", `${entry.step}${entry.host ? ` [${entry.host}]` : ""}`];
    case "step_done": return ["✓", `${entry.step}${entry.host ? ` [${entry.host}]` : ""}${entry.seconds !== undefined ? ` (${entry.seconds} s)` : ""}`];
    case "step_failed": return ["✗", `${entry.step}${entry.host ? ` [${entry.host}]` : ""}: ${entry.error ?? ""} ${entry.message ?? ""}`];
    case "spec": return ["•", `spec ${(entry as any).range} version ${(entry as any).version}`];
    case "test": return [entry.passed ? "✓" : "✗", `test: ${entry.name}`];
    case "baseline": return ["•", `baseline ${entry.host}: ${entry.passed}/${entry.total} controls pass`];
    default: return ["•", `${entry.event} ${JSON.stringify({ ...entry, t: undefined, event: undefined })}`];
  }
}

// A step's "started" line is noise once the step has finished: show only steps still running.
function visible(events: JobDetailData["recent_events"]) {
  const finished = new Set(events.filter((e) => e.event === "step_done" || e.event === "step_failed")
    .map((e) => `${e.step}|${e.host ?? ""}`));
  return events.filter((e) => e.event !== "step_start" || !finished.has(`${e.step}|${e.host ?? ""}`));
}

// JobDetail: one job, refreshed every 2.5 s UwU
export function JobDetail({ id }: { id: string }) {
  const { data, error } = useApi<JobDetailData>(`/api/jobs/${id}?tail=400`, 2500);
  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  // The result and the range it's about >w<
  const result = data.result;
  const name = jobTarget(data.target);
  // The detail view OwO
  return (
    <>
      {/* Header: state, what and which range, who and when, and a link to the range */}
      <div className="row">
        <StateBadge state={data.state} />
        <b>{jobTitle(data)} · {name}</b>
        <span className="muted">by {who(String(data.origin))} · {when(data.created)}</span>
        <span className="grow" />
        {name && <a className="btn" href={`#/ranges/${name}`}>Open range</a>}
      </div>
      {/* Failed: the error, step, host output and hint */}
      {result && !result.ok && (
        <div className="alert error">
          <b>{result.error}</b>: {result.message}
          {result.step && <div className="small">Step: {result.step}{result.host ? ` on ${result.host}` : ""}</div>}
          {result.details && <pre className="mono small job-details">{String(result.details).slice(-2000)}</pre>}
          {result.hint && <div className="small">Hint: {result.hint}</div>}
        </div>
      )}
      {/* A failed build can go to the assistant */}
      {data.kind === "apply" && data.state === "failed" && name && <FixWithAssistant id={id} range={name} />}
      {/* Succeeded: duration and verification scores */}
      {result && result.ok && (
        <div className="alert ok">
          Finished{result.seconds ? ` in ${result.seconds} s` : ""}.
          {result.verify && ` Verification ${result.verify.ok ? "passed" : "failed"}: tests ${result.verify.summary.tests_passed}/${result.verify.summary.tests_total}, baseline ${result.verify.summary.baseline_passed}/${result.verify.summary.baseline_total}.`}
          {result.summary && !result.verify && ` Tests ${result.summary.tests_passed}/${result.summary.tests_total}, baseline ${result.summary.baseline_passed}/${result.summary.baseline_total}.`}
          {result.changed === false && " No changes were needed."}
        </div>
      )}
      {/* The live event list (steps still running, finished steps, tests...) */}
      <Card title={data.state === "running" ? `Running: ${data.progress.current ?? "starting"}` : "Events"}>
        {!data.recent_events.length ? <div className="muted">Waiting for the worker…</div> : (
          <div className="events">
            {visible(data.recent_events).map((entry, i) => {
              // Each event's mark gets a color: green ✓, red ✗, grey otherwise ^w^
              const [mark, text] = eventLine(entry);
              const cls = mark === "✓" ? "var(--ok)" : mark === "✗" ? "var(--bad)" : "var(--muted)";
              return (
                <div key={i} className="ev">
                  <span className="muted">{entry.t}</span>
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

/** A failed build goes to the assistant (VALOR starts it by itself when the setting is on): open its chat or ask. */
// FixWithAssistant: open the chat(s) where the assistant works on this failed build, or ask it to start UwU
function FixWithAssistant({ id, range }: { id: string; range: string }) {
  const { data } = useApi<{ chats: { id: number; owner: string; title: string }[] }>(`/api/jobs/${id}/fix`, 10000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const open = (chat: number) => go(`ranges/${range}/chat/${chat}`);
  // ask(): hand the build to the assistant and open its chat >w<
  const ask = async () => {
    setBusy(true); setErr(null);
    try { open((await post<{ chat: number }>(`/api/jobs/${id}/fix`)).chat); }
    catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  };
  const chats = data?.chats ?? [];
  // The panel OwO
  return (
    <div className="alert info row" style={{ gap: 10, flexWrap: "wrap" }}>
      {/* What the assistant does (or can do) */}
      <span className="grow">
        {chats.length ? "The assistant is working on this failure in the range's chat - it looks inside the VMs, "
          + "fixes the cause and (if set so) rebuilds the range." : "The assistant can find the cause and fix it."}
      </span>
      {/* One button per chat already working on it, or "Ask the assistant to fix it" */}
      {chats.map((chat) => (
        <button key={chat.id} className="btn small primary" onClick={() => open(chat.id)}>
          Open the chat{chats.length > 1 ? ` (${chat.owner})` : ""}
        </button>
      ))}
      {!chats.length && <button className="btn small primary" disabled={busy} onClick={ask}>Ask the assistant to fix it</button>}
      <ErrorBox error={err} />
    </div>
  );
}
