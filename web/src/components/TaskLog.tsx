// *peeks at the bottom panel* OwO web/src/components/TaskLog.tsx is the panel along the BOTTOM of every page! ^w^
// Like Proxmox: a Tasks tab (the latest jobs) and a Notifications tab (VALOR's notifications, with an unread count)
// *wags tail*
import { post } from "../api";
// post, JobSummary, the hooks, Icon/StateBadge, and the right-click menu helpers UwU
import { JobSummary } from "../types";
import { ago, go, openTask, useApi, when } from "../hooks";
import { Icon, StateBadge } from "./ui";
import { Menu, newTab, showMenu } from "./ContextMenu";

// The panel along the bottom of every page, as in Proxmox: the latest jobs (Tasks) and VALOR's notifications.

// jobTarget(): the range a job is about. who(): a readable "started by" from the job's origin (web user, the
// assistant, or a rerun by VALOR) >w<
export const jobTarget = (target: Record<string, any>) => target.range ?? String(target.spec ?? "").replace(/\.yaml$/, "");
/** Who started a job: web:<user>, assistant:<user>:<chat> (rebuild after an automatic fix), rerun:<origin>. */
export const who = (origin: string): string => origin.startsWith("rerun:") ? `${who(origin.slice(6))}, run again by VALOR`
  : origin.startsWith("assistant:") ? `the assistant (for ${origin.split(":")[1]})` : origin.replace(/^web:/, "");

// KIND: readable names for job kinds OwO
const KIND: Record<string, string> = {
  apply: "Build", verify: "Verify", destroy: "Destroy", power: "Power", snapshot: "Take snapshot",
  rollback: "Reset to snapshot", snapshot_delete: "Delete snapshot", rotate: "New password",
  wg_rotate: "New WireGuard keys",
};

// jobTitle(): e.g. "Build + verify" or "Power (start)" ^w^
export const jobTitle = (job: JobSummary) =>
  `${KIND[job.kind] ?? job.kind}${job.target.verify ? " + verify" : ""}${job.target.action ? ` (${job.target.action})` : ""}`;

// taskMenu(): a job's right-click menu: open, open its range, new tab, copy the id UwU
function taskMenu(job: JobSummary): Menu {
  const range = jobTarget(job.target);
  return {
    title: `${jobTitle(job)}${range ? ` · ${range}` : ""}`,
    items: [
      { label: "Open task", icon: "jobs", onClick: () => openTask(job.id) },
      !!range && { label: `Open range '${range}'`, icon: "ranges", onClick: () => go(`ranges/${range}`) },
      newTab(`jobs/${job.id}`),
      "-",
      { label: "Copy task ID", icon: "copy", onClick: () => navigator.clipboard?.writeText(job.id) },
    ],
  };
}

/** Jobs as a Proxmox task list; a row opens the task viewer, a right-click its menu. */
// TaskTable: jobs as a Proxmox-style task list; click opens the task viewer, right-click the menu >w<
export function TaskTable({ jobs }: { jobs: JobSummary[] }) {
  if (!jobs.length) return <div className="empty">No jobs yet.</div>;
  // The table OwO
  return (
    <table>
      {/* Header: start, end, range, user, description, status */}
      <thead>
        <tr><th>Start time</th><th>End time</th><th>Range</th><th>User</th><th>Description</th><th>Status</th></tr>
      </thead>
      <tbody>
        {/* One row per job (running and failed rows get their own look) */}
        {jobs.map((job) => (
          <tr key={job.id} className={`clickable${job.state === "running" ? " running" : ""}${job.state === "failed" ? " failed" : ""}`}
            onClick={() => openTask(job.id)} onContextMenu={(e) => showMenu(e, taskMenu(job))}>
            {/* Times, range, who, what, and OK / failed (with the error) / a state badge */}
            <td className="nowrap">{when(job.created)}</td>
            <td className="nowrap">{job.finished ? when(job.finished) : ""}</td>
            <td>{jobTarget(job.target)}</td>
            <td>{job.origin ? who(job.origin) : ""}</td>
            <td className="fill"><span className="clip">{jobTitle(job)}</span></td>
            <td className="nowrap">
              {job.state === "succeeded" ? "OK" : job.state === "failed" ? <span className="error-text">{job.error ? `failed: ${job.error}` : "failed"}</span>
                : <StateBadge state={job.state} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Note: one notification. LEVEL: its badge class ^w^
interface Note { id: number; ts: number; level: "info" | "success" | "warning" | "error"; title: string; body: string; link: string; read: boolean }
const LEVEL: Record<Note["level"], string> = { info: "info", success: "ok", warning: "warn", error: "bad" };

// TaskLog: the panel; open/onOpen = shown or folded; tab/onTab = which tab UwU
export function TaskLog({ open, onOpen, tab, onTab }: {
  open: boolean; onOpen: (open: boolean) => void; tab: "tasks" | "notes"; onTab: (t: "tasks" | "notes") => void;
}) {
  // Jobs refresh every 5 s while open (15 s folded); notifications every 30 s >w<
  const jobs = useApi<{ jobs: JobSummary[] }>("/api/jobs?limit=50", open ? 5000 : 15000);
  const notes = useApi<{ items: Note[]; unread: number }>("/api/notifications", 30000);
  const unread = notes.data?.unread ?? 0;
  // pick(): switch tab and open; markAll(): mark every notification read OwO
  const pick = (t: "tasks" | "notes") => { onTab(t); onOpen(true); };
  const markAll = async () => { await post("/api/notifications/read", { all: true }); notes.reload(); };

  // The panel ^w^
  return (
    <section className={`tasklog${open ? "" : " closed"}`} aria-label="Tasks and notifications">
      {/* The tab bar: Tasks, Notifications (with the unread count), Mark all read, and the fold button */}
      <div className="tasklog-head" role="tablist">
        <button role="tab" aria-selected={open && tab === "tasks"} className={open && tab === "tasks" ? "active" : ""} onClick={() => pick("tasks")}>
          Tasks
        </button>
        <button role="tab" aria-selected={open && tab === "notes"} className={open && tab === "notes" ? "active" : ""} onClick={() => pick("notes")}>
          Notifications{unread > 0 && <span className="count">{unread > 99 ? "99+" : unread}</span>}
        </button>
        <span className="grow" />
        {open && tab === "notes" && unread > 0 && <button className="btn small" onClick={markAll}>Mark all read</button>}
        <button className="btn ghost small" onClick={() => onOpen(!open)} aria-label={open ? "Hide the task list" : "Show the task list"}
          title={open ? "Hide" : "Show"} aria-expanded={open}>
          <span className={`flip${open ? "" : " up"}`}><Icon name="caret" size={14} /></span>
        </button>
      </div>
      {/* The body (only when open): the task table, or the notification list */}
      {open && (
        <div className="tasklog-body">
          {tab === "tasks" ? <TaskTable jobs={jobs.data?.jobs ?? []} /> : !notes.data?.items.length ? <div className="empty">Nothing yet.</div> : (
            <table>
              <thead><tr><th>Time</th><th>Message</th><th>Details</th><th /></tr></thead>
              <tbody>
                {/* One row per notification: how long ago, a level badge with the title, the body, and a link when there is one */}
                {notes.data.items.map((note) => (
                  <tr key={note.id} className={note.read ? "" : "unread"}>
                    <td className="nowrap">{ago(note.ts)}</td>
                    <td className="nowrap"><StateBadge state={LEVEL[note.level]} label={note.title} /></td>
                    <td className="fill"><span className="clip" title={note.body}>{note.body}</span></td>
                    <td className="nowrap">{note.link && note.link.includes("#/") && <a href={note.link.slice(note.link.indexOf("#/"))}>Open</a>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
