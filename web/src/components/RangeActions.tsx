import { Fragment, useEffect, useState } from "react";
import { ApiError, del, get, post } from "../api";
import { go, openTask, useApi, when } from "../hooks";
import { ErrorBox, Loading, MenuAction, MenuItems, Modal } from "./ui";
import { Menu, newTab, openDialog, showError } from "./ContextMenu";
import { PlanModal, PlanResponse } from "./PlanModal";

// Actions on a range and its VMs: power, login, plan, destroy, snapshots. Every change runs as an engine job (see
// Jobs). The title bars and the right-click menus take their actions from rangeActions / vmActions.

// The job opens in the task viewer; `started` closes the dialog it was started from.
export async function runJob(p: Promise<{ job: string }>, onError: (e: ApiError) => void, started?: () => void) {
  try {
    const { job } = await p;
    started?.();
    openTask(job);
  } catch (e) {
    onError(e as ApiError);
  }
}

/** What can be done to a range; `false` for what needs built VMs. Errors go to `onError` (a dialog by default). */
export function rangeActions(r: { name: string; vms: number; running: number }, onError: (e: ApiError) => void = showError) {
  const { name } = r;
  const built = r.vms > 0, off = !r.running;
  const power = (action: string) => runJob(post(`/api/ranges/${name}/power`, { action }), onError);
  return {
    start: built && { label: "Start all VMs", icon: "play", disabled: r.running === r.vms, onClick: () => power("start") },
    shutdown: built && { label: "Shut down all VMs", icon: "power", disabled: off, onClick: () => power("shutdown") },
    reboot: built && { label: "Reboot all VMs", icon: "refresh", disabled: off, onClick: () => power("reboot") },
    stop: built && { label: "Force stop all VMs", icon: "stop", danger: true, disabled: off, onClick: () => power("stop") },
    login: built && { label: "Login", icon: "key", onClick: () => openDialog((close) => <LoginModal range={name} onClose={close} />) },
    verify: built && { label: "Verify", icon: "check", onClick: () => runJob(post(`/api/ranges/${name}/verify`), onError) },
    plan: { label: built ? "Plan changes" : "Plan build", icon: "map", onClick: () => openDialog((close) => <PlanDialog name={name} onClose={close} />) },
    edit: { label: "Edit spec", icon: "edit", onClick: () => go(`ranges/${name}/edit`) },
    destroy: built && { label: "Destroy", icon: "trash", danger: true, onClick: () => openDialog((close) => <DestroyModal name={name} onClose={close} />) },
  } satisfies Record<string, MenuAction | false>;
}

/** The right-click menu of a range in the resource tree and the range list. */
export function rangeMenu(r: { name: string; vms: number; running: number }, canOperate: boolean): Menu {
  const a = rangeActions(r);
  const operate: MenuItems = canOperate ? [a.start, a.shutdown, a.reboot, a.stop, "-", a.login, a.verify, a.plan, a.edit, "-", a.destroy] : [];
  return { title: `Range '${r.name}'`, items: [...operate, "-", newTab(`ranges/${r.name}`)] };
}

/** What can be done to one VM of a range; `false` for everything while it is not built. */
export function vmActions(range: string, host: string, status: string | undefined, onError: (e: ApiError) => void = showError) {
  const built = status !== undefined, off = status !== "running";
  const power = (action: string) => runJob(post(`/api/ranges/${range}/power`, { action, hosts: [host] }), onError);
  return {
    start: built && { label: "Start", icon: "play", disabled: !off, onClick: () => power("start") },
    shutdown: built && { label: "Shut down", icon: "power", disabled: off, onClick: () => power("shutdown") },
    reboot: built && { label: "Reboot", icon: "refresh", disabled: off, onClick: () => power("reboot") },
    stop: built && { label: "Force stop", icon: "stop", danger: true, disabled: off, onClick: () => power("stop") },
    console: built && { label: "Console (noVNC)", icon: "vm", disabled: off, onClick: () => go(`ranges/${range}/vm/${host}/console`) },
    serial: built && { label: "Serial console (xterm.js)", icon: "terminal", disabled: off, onClick: () => go(`ranges/${range}/vm/${host}/console/serial`) },
  } satisfies Record<string, MenuAction | false>;
}

/** The right-click menu of a VM in the resource tree and the range's VM list. */
export function vmMenu(range: string, host: string, vm: { vmid: number; status: string } | undefined, canOperate: boolean): Menu {
  const a = vmActions(range, host, vm?.status);
  const operate: MenuItems = canOperate ? [a.start, a.shutdown, a.reboot, a.stop, "-", a.console, a.serial] : [];
  return { title: vm ? `VM ${vm.vmid} (${host})` : host, items: [...operate, "-", newTab(`ranges/${range}/vm/${host}`)] };
}

/** A VM's console in a browser window of its own, as Proxmox opens one on a double-click. One window per VM:
 *  opening it again brings that window to the front. */
export function openConsoleWindow(range: string, host: string) {
  const w = window.open(`#/console/${range}/${host}`, `valor-console-${range}-${host}`, "popup,width=1100,height=800");
  w?.focus();
}

/** "Plan changes": ask the engine for the plan, then show it. */
function PlanDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const [res, setRes] = useState<PlanResponse | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => { post<PlanResponse>(`/api/ranges/${name}/plan`).then(setRes, setErr); }, [name]);
  if (res) return <PlanModal name={name} res={res} onClose={onClose} />;
  return <Modal title={`Plan for ${name}`} onClose={onClose}>{err ? <ErrorBox error={err} /> : <Loading label="Planning…" />}</Modal>;
}

export function DestroyModal({ name, onClose }: { name: string; onClose: () => void }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const run = async () => {
    setBusy(true);
    try {
      const r = await post<{ job: string }>(`/api/ranges/${name}/destroy`, { confirm });
      onClose();
      openTask(r.job);
    } catch (e) {
      setErr(e as ApiError);
      setBusy(false);
    }
  };
  return (
    <Modal title={`Destroy ${name}?`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger solid" disabled={confirm !== name || busy} onClick={run}>Destroy all VMs</button>
      </>
    }>
      <div>This deletes every VM of the range (its spec stays, so you can build it again). Type <b>{name}</b> to confirm.</div>
      <input name="confirm-name" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoFocus aria-label="Range name" />
      <ErrorBox error={err} />
    </Modal>
  );
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
                <button className="btn primary" onClick={() => runJob(post(`/api/ranges/${range}/credentials/rotate`), setErr, onClose)}>
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

export function HostPower({ range, host, status, onError }: {
  range: string; host: string; status?: string; onError: (e: ApiError) => void;
}) {
  const act = (action: string) => runJob(post(`/api/ranges/${range}/power`, { action, hosts: [host] }), onError);
  return (
    <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
      {status === "running" && <button className="btn small primary" onClick={() => go(`ranges/${range}/vm/${host}/console`)}>Console</button>}
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
          <input name="snapshot-name" style={{ maxWidth: 200 }} placeholder="snapshot name" value={name} onChange={(e) => setName(e.target.value)}
            pattern="[a-zA-Z][a-zA-Z0-9_\-]{1,39}" title="2-40 letters, digits, - or _, starting with a letter" required />
          <input name="snapshot-description" style={{ maxWidth: 320 }} placeholder="description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
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
          onClick={() => runJob(post(`/api/ranges/${range}/snapshots/${snap.name}/rollback`, { confirm: confirmName, delete_newer: deleteNewer }), setErr, onClose)}>
          Reset all VMs
        </button>
      </>
    }>
      <div>Every VM of the range goes back to <b>{snap.name}</b> ({when(snap.time)}). Anything changed since is lost.
        The VMs start again and VALOR re-runs the verification.</div>
      {newer.length > 0 && (
        <label className="checkbox">
          <input name="delete-newer" type="checkbox" checked={deleteNewer} onChange={(e) => setDeleteNewer(e.target.checked)} />
          Delete the newer snapshots ({newer.map((n) => n.name).join(", ")}) - storages like ZFS can only roll back to the newest one
        </label>
      )}
      <label className="field">Type <b>{range}</b> to confirm<input name="confirm-name" value={confirmName} onChange={(e) => setConfirm(e.target.value)} autoFocus /></label>
      <ErrorBox error={err} />
    </Modal>
  );
}
