// *grabs the big remote control* OwO web/src/components/RangeActions.tsx: everything you can DO to a range or VM! ^w^
// Power, login, plan, destroy, delete and snapshots. Every change runs as an engine job (shown in the task viewer).
// The title bars and the right-click menus both take their actions from rangeActions / vmActions, so they always
// offer the same things *wags tail*
import { Fragment, useEffect, useState } from "react";
// API calls, hooks, UI pieces, the menu helpers (menus, new tab, dialogs, error dialogs) and the plan dialog UwU
import { ApiError, del, get, post } from "../api";
import { go, openTask, rangesChanged, useApi, when } from "../hooks";
import { ErrorBox, Loading, MenuAction, MenuItems, Modal } from "./ui";
import { Menu, newTab, openDialog, showError } from "./ContextMenu";
import { PlanModal, PlanResponse } from "./PlanModal";

// Actions on a range and its VMs: power, login, plan, destroy, snapshots. Every change runs as an engine job (see
// Jobs). The title bars and the right-click menus take their actions from rangeActions / vmActions.

// The job opens in the task viewer; `started` closes the dialog it was started from.
// runJob(): wait for the "job started" answer, close the dialog it came from (if any), and open the task viewer;
// errors go to onError >w<
export async function runJob(p: Promise<{ job: string }>, onError: (e: ApiError) => void, started?: () => void) {
  try {
    const { job } = await p;
    started?.();
    openTask(job);
  } catch (e) {
    onError(e as ApiError);
  }
}

/** What can be done to a range; `false` for what does not apply (Destroy needs VMs, Delete needs none). Errors go to
 *  `onError` (a dialog by default). */
// rangeActions(): the actions for a range (`false` = doesn't apply: Destroy needs VMs, Delete needs none) OwO
export function rangeActions(range: { name: string; vms: number; running: number }, onError: (e: ApiError) => void = showError) {
  const { name } = range;
  // built = has VMs; off = none running; power() starts a power job for the whole range ^w^
  const built = range.vms > 0, off = !range.running;
  const power = (action: string) => runJob(post(`/api/ranges/${name}/power`, { action }), onError);
  return {
    // Power: start all (disabled when all run), shut down, reboot, force stop (danger) UwU
    start: built && { label: "Start all VMs", icon: "play", disabled: range.running === range.vms, onClick: () => power("start") },
    shutdown: built && { label: "Shut down all VMs", icon: "power", disabled: off, onClick: () => power("shutdown") },
    reboot: built && { label: "Reboot all VMs", icon: "refresh", disabled: off, onClick: () => power("reboot") },
    stop: built && { label: "Force stop all VMs", icon: "stop", danger: true, disabled: off, onClick: () => power("stop") },
    // Login (opens the login dialog), Verify (a job), Plan (asks the engine), Edit spec >w<
    login: built && { label: "Login", icon: "key", onClick: () => openDialog((close) => <LoginModal range={name} onClose={close} />) },
    verify: built && { label: "Verify", icon: "check", onClick: () => runJob(post(`/api/ranges/${name}/verify`), onError) },
    plan: { label: built ? "Plan changes" : "Plan build", icon: "map", onClick: () => openDialog((close) => <PlanDialog name={name} onClose={close} />) },
    edit: { label: "Edit spec", icon: "edit", onClick: () => go(`ranges/${name}/edit`) },
    // Destroy (built ranges) or Delete (ranges without VMs), both confirmed in a dialog OwO
    destroy: built && { label: "Destroy", icon: "trash", danger: true, onClick: () => openDialog((close) => <DestroyModal name={name} onClose={close} />) },
    remove: !built && { label: "Delete range", icon: "trash", danger: true, onClick: () => openDialog((close) => <DeleteModal name={name} onClose={close} />) },
  // `satisfies` checks every entry is a MenuAction or false, while keeping the exact keys for callers ^w^
  } satisfies Record<string, MenuAction | false>;
}

/** The right-click menu of a range in the resource tree and the range list. */
// rangeMenu(): a range's right-click menu: the actions grouped by separators (operators), then "Open in a new tab" UwU
export function rangeMenu(range: { name: string; vms: number; running: number }, canOperate: boolean): Menu {
  const actions = rangeActions(range);
  const operate: MenuItems = canOperate ? [
    actions.start, actions.shutdown, actions.reboot, actions.stop, "-",
    actions.login, actions.verify, actions.plan, actions.edit, "-",
    actions.destroy, actions.remove,
  ] : [];
  return { title: `Range '${range.name}'`, items: [...operate, "-", newTab(`ranges/${range.name}`)] };
}

/** What can be done to one VM of a range; `false` for everything while it is not built. */
// vmActions(): one VM's actions: power, and the two consoles >w<
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
// vmMenu(): a VM's right-click menu OwO
export function vmMenu(range: string, host: string, vm: { vmid: number; status: string } | undefined, canOperate: boolean): Menu {
  const actions = vmActions(range, host, vm?.status);
  const operate: MenuItems = canOperate ? [actions.start, actions.shutdown, actions.reboot, actions.stop, "-", actions.console, actions.serial] : [];
  return { title: vm ? `VM ${vm.vmid} (${host})` : host, items: [...operate, "-", newTab(`ranges/${range}/vm/${host}`)] };
}

/** A VM's console in a browser window of its own, as Proxmox opens one on a double-click. One window per VM:
 *  opening it again brings that window to the front. */
// openConsoleWindow(): a console in its own popup window, ONE per VM (the window name is per VM, so opening it again
// just focuses it) ^w^
export function openConsoleWindow(range: string, host: string) {
  const popup = window.open(`#/console/${range}/${host}`, `valor-console-${range}-${host}`, "popup,width=1100,height=800");
  popup?.focus();
}

/** "Plan changes": ask the engine for the plan, then show it. */
// PlanDialog: ask for the plan (a spinner meanwhile), then show the PlanModal UwU
function PlanDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const [res, setRes] = useState<PlanResponse | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => { post<PlanResponse>(`/api/ranges/${name}/plan`).then(setRes, setErr); }, [name]);
  if (res) return <PlanModal name={name} res={res} onClose={onClose} />;
  return <Modal title={`Plan for ${name}`} onClose={onClose}>{err ? <ErrorBox error={err} /> : <Loading label="Planning…" />}</Modal>;
}

// DestroyModal: delete every VM of the range; the range's name must be typed to confirm >w<
export function DestroyModal({ name, onClose }: { name: string; onClose: () => void }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  // run(): start the destroy job and open its task OwO
  const run = async () => {
    setBusy(true);
    try {
      const started = await post<{ job: string }>(`/api/ranges/${name}/destroy`, { confirm });
      onClose();
      openTask(started.job);
    } catch (e) {
      setErr(e as ApiError);
      setBusy(false);
    }
  };
  // The dialog; the Destroy button stays disabled until the name matches ^w^
  return (
    <Modal title={`Destroy ${name}?`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger solid" disabled={confirm !== name || busy} onClick={run}>Destroy all VMs</button>
      </>
    }>
      {/* What happens, the name field, and errors */}
      <div>This deletes every VM of the range (its spec stays, so you can build it again). Type <b>{name}</b> to confirm.</div>
      <input name="confirm-name" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoFocus aria-label="Range name" />
      <ErrorBox error={err} />
    </Modal>
  );
}

/** A range without VMs (planned, or destroyed) is deleted outright: its spec, draft, history and journal. */
// DeleteModal: delete a range with no VMs (spec, draft, history, journal), name typed to confirm UwU
function DeleteModal({ name, onClose }: { name: string; onClose: () => void }) {
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  // run(): delete, refresh the range list, and leave the deleted range's pages >w<
  const run = async () => {
    setBusy(true);
    try {
      await del(`/api/ranges/${name}/spec`);
      onClose();
      rangesChanged();
      // leave the deleted range's pages
      if (window.location.hash.split("/").slice(1, 3).join("/") === `ranges/${name}`) go("dashboard");
    } catch (e) {
      setErr(e as ApiError);
      setBusy(false);
    }
  };
  // The dialog OwO
  return (
    <Modal title={`Delete ${name}?`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn danger solid" disabled={confirm !== name || busy} onClick={run}>Delete range</button>
      </>
    }>
      {/* What gets deleted (it can't be undone), the name field, and errors */}
      <div>{name} has no VMs. This deletes the range from VALOR: its spec, draft changes, build history and journal.
        It cannot be undone. Type <b>{name}</b> to confirm.</div>
      <input name="confirm-name" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoFocus aria-label="Range name" />
      <ErrorBox error={err} />
    </Modal>
  );
}

// Account / Login: what GET .../credentials returns (the password and where it signs in). KIND: account kind labels ^w^
interface Account { username: string; hosts: string[]; kind: "linux" | "windows" | "domain"; domain?: string }
interface Login { username: string; password: string; version: number; created: string; accounts?: Account[] }

const KIND: Record<Account["kind"], string> = { linux: "Linux", windows: "Windows, local account", domain: "Active Directory" };

// LoginModal: the range login (each view is audited by the server), hidden until "Show", with Copy and New password UwU
export function LoginModal({ range, onClose }: { range: string; onClose: () => void }) {
  const [login, setLogin] = useState<Login | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [shown, setShown] = useState(false);
  const [rotating, setRotating] = useState(false);
  // Load it when the dialog opens >w<
  useEffect(() => {
    get<Login>(`/api/ranges/${range}/credentials`).then(setLogin).catch(setErr);
  }, [range]);
  // The dialog; footer: New password and Done OwO
  return (
    <Modal title={`Login for ${range}`} onClose={onClose} footer={
      <>
        {login && !rotating && <button className="btn" onClick={() => setRotating(true)}>New password…</button>}
        <button className="btn primary" onClick={onClose}>Done</button>
      </>
    }>
      {/* Errors and loading, then the login */}
      <ErrorBox error={err} />
      {!login && !err && <Loading />}
      {login && (
        <>
          {/* What the password is for (consoles; SSH is key-only) and that viewing is audited */}
          <div className="muted">
            One password for every VM console of this range (Proxmox console or VALOR's browser console). SSH accepts
            keys only. Viewing this login is recorded in the audit log.
          </div>
          {/* One entry per account (username and which hosts), then the password with Show/Hide and Copy, then when it was set */}
          <dl className="kv">
            {(login.accounts ?? [{ username: login.username, hosts: [], kind: "linux" as const }]).map((account) => (
              <Fragment key={account.username}>
                {/* The account kind and its username on which hosts */}
                <dt>{account.kind === "linux" ? "Username" : KIND[account.kind]}</dt>
                <dd>
                  <span className="mono">{account.username}</span>
                  {account.hosts.length > 0 && <span className="muted"> on {account.hosts.join(", ")}</span>}
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
          {/* New password: confirm first (a short job sets it on every VM) */}
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

// HostPower: small power buttons for one VM in a list: Console, Start, Reboot, Shut down (whichever fit its state) ^w^
export function HostPower({ range, host, status, onError }: {
  range: string; host: string; status?: string; onError: (e: ApiError) => void;
}) {
  const act = (action: string) => runJob(post(`/api/ranges/${range}/power`, { action, hosts: [host] }), onError);
  return (
    <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
      {/* Only the buttons that make sense right now */}
      {status === "running" && <button className="btn small primary" onClick={() => go(`ranges/${range}/vm/${host}/console`)}>Console</button>}
      {status !== "running" && <button className="btn small" onClick={() => act("start")}>Start</button>}
      {status === "running" && <button className="btn small" onClick={() => act("reboot")}>Reboot</button>}
      {status === "running" && <button className="btn small" onClick={() => act("shutdown")}>Shut down</button>}
    </div>
  );
}

// Snap: one snapshot (across the range's VMs) UwU
interface Snap { name: string; description: string; time: number; hosts: string[]; complete: boolean; automatic: boolean }

// SnapshotsTab: a range's snapshots: take one, reset to one, delete one >w<
export function SnapshotsTab({ range, canOperate }: { range: string; canOperate: boolean }) {
  const { data, error, loading, reload } = useApi<{ snapshots: Snap[] }>(`/api/ranges/${range}/snapshots`);
  const [err, setErr] = useState<ApiError | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [reset, setReset] = useState<Snap | null>(null);
  // The page OwO
  if (loading && !data) return <Loading />;
  const snaps = data?.snapshots ?? [];
  return (
    <div className="card-body stack">
      {/* How snapshots work (valor-clean is automatic; resets re-verify; disks only) */}
      <div className="muted small">
        VALOR takes <b>valor-clean</b> automatically after every successful, verified build. A reset rolls every VM
        back to a snapshot, starts it again and re-runs the verification. Snapshots hold disks only (no memory).
      </div>
      <ErrorBox error={err ?? error} />
      {/* Take a snapshot (operators): a name (checked by a pattern), a description, Take and Refresh */}
      {canOperate && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); runJob(post(`/api/ranges/${range}/snapshots`, { name, description: desc }), setErr); }}>
          {/* Name and description fields, then the buttons */}
          <input name="snapshot-name" style={{ maxWidth: 200 }} placeholder="snapshot name" value={name} onChange={(e) => setName(e.target.value)}
            pattern="[a-zA-Z][a-zA-Z0-9_\-]{1,39}" title="2-40 letters, digits, - or _, starting with a letter" required />
          <input name="snapshot-description" style={{ maxWidth: 320 }} placeholder="description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <button className="btn primary" disabled={!name}>Take snapshot</button>
          <button type="button" className="btn ghost" onClick={reload}>Refresh</button>
        </form>
      )}
      {/* The list, or "No snapshots yet" */}
      {!snaps.length ? <div className="empty">No snapshots yet.</div> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Snapshot</th><th>Taken</th><th>VMs</th><th>Description</th><th></th></tr></thead>
            <tbody>
              {/* One row per snapshot: name (+automatic), when, which VMs have it, description, and Reset/Delete (operators) */}
              {snaps.map((snap) => (
                <tr key={snap.name}>
                  <td><b className="mono">{snap.name}</b> {snap.automatic && <span className="badge ok">automatic</span>}</td>
                  <td className="nowrap">{when(snap.time)}</td>
                  <td>{snap.complete ? "all" : <span className="badge warn">{snap.hosts.join(", ")} only</span>}</td>
                  <td className="small">{snap.description}</td>
                  {/* Reset only for snapshots every VM has; Delete asks first */}
                  <td>
                    {canOperate && (
                      <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        <button className="btn small" onClick={() => setReset(snap)} disabled={!snap.complete}>Reset to this</button>
                        <button className="btn small danger" onClick={() => confirm(`Delete snapshot ${snap.name}?`) &&
                          runJob(del(`/api/ranges/${range}/snapshots/${snap.name}`), setErr)}>Delete</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* The reset dialog */}
      {reset && <ResetModal range={range} snap={reset} newer={snaps.filter((other) => other.time > reset.time)} onClose={() => setReset(null)} />}
    </div>
  );
}

// ResetModal: roll every VM back to a snapshot, name typed to confirm. Storages like ZFS can only roll back to the
// newest snapshot, so newer ones must be deleted (a checkbox says so) ^w^
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
      {/* What a reset does, the "delete newer snapshots" checkbox when needed, the name field, and errors UwU */}
      <div>Every VM of the range goes back to <b>{snap.name}</b> ({when(snap.time)}). Anything changed since is lost.
        The VMs start again and VALOR re-runs the verification.</div>
      {newer.length > 0 && (
        <label className="checkbox">
          <input name="delete-newer" type="checkbox" checked={deleteNewer} onChange={(e) => setDeleteNewer(e.target.checked)} />
          Delete the newer snapshots ({newer.map((other) => other.name).join(", ")}) - storages like ZFS can only roll back to the newest one
        </label>
      )}
      <label className="field">Type <b>{range}</b> to confirm<input name="confirm-name" value={confirmName} onChange={(e) => setConfirm(e.target.value)} autoFocus /></label>
      <ErrorBox error={err} />
    </Modal>
  );
}
