import { Fragment, useEffect, useRef, useState } from "react";
import { ApiError, del, get, post } from "../api";
import { go, useApi, when } from "../hooks";
import { ErrorBox, Loading, Modal } from "./ui";

// Actions on a built range: its login, power, snapshots. Every change runs as an engine job (see Jobs).

export async function runJob(p: Promise<{ job: string }>, onError: (e: ApiError) => void) {
  try {
    const r = await p;
    go(`jobs/${r.job}`);
  } catch (e) {
    onError(e as ApiError);
  }
}

interface Account { username: string; hosts: string[]; kind: "linux" | "windows" | "domain"; domain?: string }
interface Login { username: string; password: string; version: number; created: string; accounts?: Account[] }

const KIND: Record<Account["kind"], string> = { linux: "Linux", windows: "Windows, local account", domain: "Active Directory" };

export function LoginModal({ range, onClose }: { range: string; onClose: () => void }) {
  const [login, setLogin] = useState<Login | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [shown, setShown] = useState(false);
  const [rotating, setRotating] = useState(false);
  useEffect(() => {
    get<Login>(`/api/ranges/${range}/credentials`).then(setLogin).catch(setErr);
  }, [range]);
  return (
    <Modal title={`Login for ${range}`} onClose={onClose} footer={
      <>
        {login && !rotating && <button className="btn" onClick={() => setRotating(true)}>New password…</button>}
        <button className="btn primary" onClick={onClose}>Done</button>
      </>
    }>
      <ErrorBox error={err} />
      {!login && !err && <Loading />}
      {login && (
        <>
          <div className="muted">
            One password for every VM console of this range (Proxmox console or VALOR's browser console). SSH accepts
            keys only. Viewing this login is recorded in the audit log.
          </div>
          <dl className="kv">
            {(login.accounts ?? [{ username: login.username, hosts: [], kind: "linux" as const }]).map((a) => (
              <Fragment key={a.username}>
                <dt>{a.kind === "linux" ? "Username" : KIND[a.kind]}</dt>
                <dd>
                  <span className="mono">{a.username}</span>
                  {a.hosts.length > 0 && <span className="muted"> on {a.hosts.join(", ")}</span>}
                </dd>
              </Fragment>
            ))}
            <dt>Password</dt>
            <dd className="row" style={{ gap: 8 }}>
              <span className="mono" style={{ fontSize: 15, letterSpacing: "0.04em" }}>{shown ? login.password : "•".repeat(12)}</span>
              <button className="btn small" onClick={() => setShown(!shown)}>{shown ? "Hide" : "Show"}</button>
              <button className="btn small" onClick={() => navigator.clipboard?.writeText(login.password)}>Copy</button>
            </dd>
            <dt>Set</dt><dd>{when(login.created)} (version {login.version})</dd>
          </dl>
          {rotating && (
            <div className="alert warn">
              A new password is set on every VM of the range (a short job; nothing else changes).
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" onClick={() => setRotating(false)}>Cancel</button>
                <button className="btn primary" onClick={() => runJob(post(`/api/ranges/${range}/credentials/rotate`), setErr)}>
                  Set a new password
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

const POWER: { action: string; label: string; danger?: boolean }[] = [
  { action: "start", label: "Start all VMs" },
  { action: "shutdown", label: "Shut down all VMs" },
  { action: "reboot", label: "Reboot all VMs" },
  { action: "stop", label: "Force stop all VMs", danger: true },
];

export function PowerMenu({ range, onError }: { range: string; onError: (e: ApiError) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="btn" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open}>Power ▾</button>
      {open && (
        <div className="popover menu" role="menu" style={{ width: 220 }}>
          {POWER.map((p) => (
            <button key={p.action} role="menuitem" className={`menu-item${p.danger ? " danger" : ""}`}
              onClick={() => { setOpen(false); runJob(post(`/api/ranges/${range}/power`, { action: p.action }), onError); }}>
              {p.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function HostPower({ range, host, status, onError }: {
  range: string; host: string; status?: string; onError: (e: ApiError) => void;
}) {
  const act = (action: string) => runJob(post(`/api/ranges/${range}/power`, { action, hosts: [host] }), onError);
  return (
    <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
      {status === "running" && <button className="btn small primary" onClick={() => go(`ranges/${range}/console/${host}/vnc`)}>Console</button>}
      {status !== "running" && <button className="btn small" onClick={() => act("start")}>Start</button>}
      {status === "running" && <button className="btn small" onClick={() => act("reboot")}>Reboot</button>}
      {status === "running" && <button className="btn small" onClick={() => act("shutdown")}>Shut down</button>}
    </div>
  );
}

interface Snap { name: string; description: string; time: number; hosts: string[]; complete: boolean; automatic: boolean }

export function SnapshotsTab({ range, canOperate }: { range: string; canOperate: boolean }) {
  const { data, error, loading, reload } = useApi<{ snapshots: Snap[] }>(`/api/ranges/${range}/snapshots`);
  const [err, setErr] = useState<ApiError | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [reset, setReset] = useState<Snap | null>(null);
  if (loading && !data) return <Loading />;
  const snaps = data?.snapshots ?? [];
  return (
    <div className="card-body stack">
      <div className="muted small">
        VALOR takes <b>valor-clean</b> automatically after every successful, verified build. A reset rolls every VM
        back to a snapshot, starts it again and re-runs the verification. Snapshots hold disks only (no memory).
      </div>
      <ErrorBox error={err ?? error} />
      {canOperate && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); runJob(post(`/api/ranges/${range}/snapshots`, { name, description: desc }), setErr); }}>
          <input style={{ maxWidth: 200 }} placeholder="snapshot name" value={name} onChange={(e) => setName(e.target.value)}
            pattern="[a-zA-Z][a-zA-Z0-9_\-]{1,39}" title="2-40 letters, digits, - or _, starting with a letter" required />
          <input style={{ maxWidth: 320 }} placeholder="description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <button className="btn primary" disabled={!name}>Take snapshot</button>
          <button type="button" className="btn ghost" onClick={reload}>Refresh</button>
        </form>
      )}
      {!snaps.length ? <div className="empty">No snapshots yet.</div> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Snapshot</th><th>Taken</th><th>VMs</th><th>Description</th><th></th></tr></thead>
            <tbody>
              {snaps.map((s) => (
                <tr key={s.name}>
                  <td><b className="mono">{s.name}</b> {s.automatic && <span className="badge ok">automatic</span>}</td>
                  <td className="nowrap">{when(s.time)}</td>
                  <td>{s.complete ? "all" : <span className="badge warn">{s.hosts.join(", ")} only</span>}</td>
                  <td className="small">{s.description}</td>
                  <td>
                    {canOperate && (
                      <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        <button className="btn small" onClick={() => setReset(s)} disabled={!s.complete}>Reset to this</button>
                        <button className="btn small danger" onClick={() => confirm(`Delete snapshot ${s.name}?`) &&
                          runJob(del(`/api/ranges/${range}/snapshots/${s.name}`), setErr)}>Delete</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {reset && <ResetModal range={range} snap={reset} newer={snaps.filter((x) => x.time > reset.time)} onClose={() => setReset(null)} />}
    </div>
  );
}

function ResetModal({ range, snap, newer, onClose }: { range: string; snap: Snap; newer: Snap[]; onClose: () => void }) {
  const [confirmName, setConfirm] = useState("");
  const [deleteNewer, setDeleteNewer] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  return (
    <Modal title={`Reset ${range} to ${snap.name}?`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger solid" disabled={confirmName !== range || (newer.length > 0 && !deleteNewer)}
          onClick={() => runJob(post(`/api/ranges/${range}/snapshots/${snap.name}/rollback`, { confirm: confirmName, delete_newer: deleteNewer }), setErr)}>
          Reset all VMs
        </button>
      </>
    }>
      <div>Every VM of the range goes back to <b>{snap.name}</b> ({when(snap.time)}). Anything changed since is lost.
        The VMs start again and VALOR re-runs the verification.</div>
      {newer.length > 0 && (
        <label className="checkbox">
          <input type="checkbox" checked={deleteNewer} onChange={(e) => setDeleteNewer(e.target.checked)} />
          Delete the newer snapshots ({newer.map((n) => n.name).join(", ")}) - storages like ZFS can only roll back to the newest one
        </label>
      )}
      <label className="field">Type <b>{range}</b> to confirm<input value={confirmName} onChange={(e) => setConfirm(e.target.value)} autoFocus /></label>
      <ErrorBox error={err} />
    </Modal>
  );
}
