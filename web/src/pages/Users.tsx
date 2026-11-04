// *manages the guest list* OwO web/src/pages/Users.tsx is USER MANAGEMENT (admins only)! ^w^
// Roles, locks, MFA resets, password resets, and new local accounts with a temporary password. Directory (LDAP)
// accounts get their role from their groups, so it can't be changed here *wags tail*
import { FormEvent, useState } from "react";
// API calls, the user types, hooks, and UI pieces UwU
import { ApiError, del, patch, post } from "../api";
import { Me, Role, User } from "../types";
import { ago, useApi } from "../hooks";
import { Card, ErrorBox, Icon, Loading, Modal } from "../components/ui";

// User management (admins): roles, locks, MFA and password resets, and new local accounts with a temporary password.
// Directory (LDAP) accounts get their role from their groups, so it can't be changed here.

// Users: the page; `me` = the signed-in admin (they can't disable or delete themselves) >w<
export function Users({ me }: { me: Me }) {
  // The accounts, the "add user" dialog flag, a temporary password to show once, and the last error OwO
  const { data, error, loading, reload } = useApi<User[]>("/api/users");
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<{ user: string; password: string } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  // act(): run one admin action; show a temporary password if the server made one; reload the list ^w^
  const act = async (fn: () => Promise<any>) => {
    setErr(null);
    try {
      const result = await fn();
      if (result?.temporary_password) setSecret({ user: result.username, password: result.temporary_password });
      reload();
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  if (loading && !data) return <Loading />;
  // The accounts card, the role explanation, and the dialogs UwU
  return (
    <>
      {/* The card with an "Add user" button in its header */}
      <Card title="Accounts" bodyClass="" actions={
        <button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> Add user</button>
      }>
        {/* Errors, then the table */}
        <div style={{ padding: err || error ? "12px 18px 0" : 0 }}><ErrorBox error={err ?? error} /></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Sign-in</th><th>MFA</th><th>Last sign-in</th><th></th></tr></thead>
            <tbody>
              {/* One row per account */}
              {data?.map((user) => (
                <tr key={user.id}>
                  {/* Name (and "(you)"), display name and email */}
                  <td>
                    <b>{user.username}</b>{user.id === me.user.id && <span className="muted"> (you)</span>}
                    <div className="muted small">{user.display_name}{user.email ? ` · ${user.email}` : ""}</div>
                  </td>
                  {/* Role (locked for directory accounts) */}
                  <td>
                    <select name="role" value={user.role} disabled={user.source === "ldap"} style={{ width: 120 }}
                      onChange={(e) => act(() => patch(`/api/users/${user.id}`, { role: e.target.value as Role }))}
                      aria-label={`Role of ${user.username}`}>
                      <option value="viewer">viewer</option>
                      <option value="operator">operator</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  {/* Account source and any disabled/locked badges */}
                  <td>
                    {user.source === "ldap" ? <span className="badge info">directory</span> : <span className="badge">local</span>}{" "}
                    {user.disabled && <span className="badge bad">disabled</span>}{" "}
                    {user.locked && <span className="badge warn">locked</span>}
                  </td>
                  {/* MFA state and last sign-in */}
                  <td>{user.mfa ? <span className="badge ok">on</span> : <span className="badge warn">at next sign-in</span>}</td>
                  <td className="nowrap">{ago(user.last_login_at)}</td>
                  {/* Actions: unlock, reset MFA, reset password (local), enable/disable and delete (not yourself); risky ones ask first */}
                  <td>
                    <div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                      {user.locked && <button className="btn small" onClick={() => act(() => post(`/api/users/${user.id}/unlock`))}>Unlock</button>}
                      {user.mfa && <button className="btn small" onClick={() => confirm(`Reset MFA for ${user.username}? They set it up again at the next sign-in.`) && act(() => post(`/api/users/${user.id}/reset-mfa`))}>Reset MFA</button>}
                      {user.source === "local" && <button className="btn small" onClick={() => confirm(`Give ${user.username} a new temporary password?`) && act(() => post(`/api/users/${user.id}/reset-password`, {}))}>Reset password</button>}
                      {user.id !== me.user.id && <button className="btn small" onClick={() => act(() => patch(`/api/users/${user.id}`, { disabled: !user.disabled }))}>{user.disabled ? "Enable" : "Disable"}</button>}
                      {user.id !== me.user.id && <button className="btn small danger" onClick={() => confirm(`Delete ${user.username}?`) && act(() => del(`/api/users/${user.id}`))}>Delete</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {/* What each role may do */}
      <div className="muted small">
        Roles: <b>viewer</b> sees everything except administration; <b>operator</b> also writes specs and approves
        builds and teardowns; <b>admin</b> also manages accounts and settings. Everyone must use two-factor
        authentication.
      </div>
      {/* The add-user dialog, and the one-time temporary password dialog */}
      {adding && <AddUser onClose={() => setAdding(false)} onCreated={(created) => { setAdding(false); reload(); if (created.temporary_password) setSecret({ user: created.username, password: created.temporary_password }); }} />}
      {secret && (
        <Modal title="Temporary password" onClose={() => setSecret(null)} footer={<button className="btn primary" onClick={() => setSecret(null)}>Done</button>}>
          <div>Give this to <b>{secret.user}</b> over a safe channel. It is shown only once; they choose their own password and set up MFA at their first sign-in.</div>
          <div className="codes" style={{ gridTemplateColumns: "1fr" }}>{secret.password}</div>
        </Modal>
      )}
    </>
  );
}

// AddUser: the new-account form; the server makes a temporary password and it's shown once >w<
function AddUser({ onClose, onCreated }: { onClose: () => void; onCreated: (user: User) => void }) {
  const [form, setForm] = useState({ username: "", display_name: "", email: "", role: "viewer" as Role });
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  // submit(): create the account and hand it back OwO
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onCreated(await post<User>("/api/users", form));
    } catch (x) {
      setErr(x as ApiError);
      setBusy(false);
    }
  };
  // set(field): an onChange handler that updates one form field ^w^
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  return (
    <Modal title="Add user" onClose={onClose}>
      {/* Username, display name, email and role */}
      <form className="stack" onSubmit={submit}>
        <label className="field">Username<input name="new-username" value={form.username} onChange={set("username")} required autoFocus /></label>
        <label className="field">Display name<input name="display-name" value={form.display_name} onChange={set("display_name")} /></label>
        <label className="field">Email<input name="email" type="email" value={form.email} onChange={set("email")} /></label>
        <label className="field">Role
          <select name="role" value={form.role} onChange={set("role")}>
            <option value="viewer">viewer</option><option value="operator">operator</option><option value="admin">admin</option>
          </select>
        </label>
        {/* A note, errors, and the buttons UwU */}
        <div className="muted small">VALOR creates a temporary password and shows it once.</div>
        <ErrorBox error={err} />
        <div className="row end">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !form.username}>Create</button>
        </div>
      </form>
    </Modal>
  );
}
