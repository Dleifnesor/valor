import { post } from "../api";
import { JobSummary } from "../types";
import { ago, go, openTask, useApi, when } from "../hooks";
import { Icon, StateBadge } from "./ui";
import { Menu, newTab, showMenu } from "./ContextMenu";

// The panel along the bottom of every page, as in Proxmox: the latest jobs (Tasks) and VALOR's notifications.

export const jobTarget = (target: Record<string, any>) => target.range ?? String(target.spec ?? "").replace(/\.yaml$/, "");
/** Who started a job: web:<user>, assistant:<user>:<chat> (rebuild after an automatic fix), rerun:<origin>. */
export const who = (origin: string): string => origin.startsWith("rerun:") ? `${who(origin.slice(6))}, run again by VALOR`
  : origin.startsWith("assistant:") ? `the assistant (for ${origin.split(":")[1]})` : origin.replace(/^web:/, "");

const KIND: Record<string, string> = {
  apply: "Build", verify: "Verify", destroy: "Destroy", power: "Power", snapshot: "Take snapshot",
  rollback: "Reset to snapshot", snapshot_delete: "Delete snapshot", rotate: "New password",
  wg_rotate: "New WireGuard keys",
};

export const jobTitle = (job: JobSummary) =>
  `${KIND[job.kind] ?? job.kind}${job.target.verify ? " + verify" : ""}${job.target.action ? ` (${job.target.action})` : ""}`;

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
export function TaskTable({ jobs }: { jobs: JobSummary[] }) {
  if (!jobs.length) return <div className="empty">No jobs yet.</div>;
  return (
    <table>
      <thead>
        <tr><th>Start time</th><th>End time</th><th>Range</th><th>User</th><th>Description</th><th>Status</th></tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id} className={`clickable${job.state === "running" ? " running" : ""}${job.state === "failed" ? " failed" : ""}`}
            onClick={() => openTask(job.id)} onContextMenu={(e) => showMenu(e, taskMenu(job))}>
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

interface Note { id: number; ts: number; level: "info" | "success" | "warning" | "error"; title: string; body: string; link: string; read: boolean }
const LEVEL: Record<Note["level"], string> = { info: "info", success: "ok", warning: "warn", error: "bad" };

export function TaskLog({ open, onOpen, tab, onTab }: {
  open: boolean; onOpen: (open: boolean) => void; tab: "tasks" | "notes"; onTab: (t: "tasks" | "notes") => void;
}) {
  const jobs = useApi<{ jobs: JobSummary[] }>("/api/jobs?limit=50", open ? 5000 : 15000);
  const notes = useApi<{ items: Note[]; unread: number }>("/api/notifications", 30000);
  const unread = notes.data?.unread ?? 0;
  const pick = (t: "tasks" | "notes") => { onTab(t); onOpen(true); };
  const markAll = async () => { await post("/api/notifications/read", { all: true }); notes.reload(); };

  return (
    <section className={`tasklog${open ? "" : " closed"}`} aria-label="Tasks and notifications">
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
      {open && (
        <div className="tasklog-body">
          {tab === "tasks" ? <TaskTable jobs={jobs.data?.jobs ?? []} /> : !notes.data?.items.length ? <div className="empty">Nothing yet.</div> : (
            <table>
              <thead><tr><th>Time</th><th>Message</th><th>Details</th><th /></tr></thead>
              <tbody>
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
