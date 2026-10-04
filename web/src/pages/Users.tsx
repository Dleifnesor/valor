import { FormEvent, useState } from "react";
import { ApiError, del, patch, post } from "../api";
import { Me, Role, User } from "../types";
import { ago, useApi } from "../hooks";
import { Card, ErrorBox, Icon, Loading, Modal } from "../components/ui";

export function Users({ me }: { me: Me }) {
  const { data, error, loading, reload } = useApi<User[]>("/api/users");
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<{ user: string; password: string } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  const act = async (fn: () => Promise<any>) => {
    setErr(null);
    try {
      const r = await fn();
      if (r?.temporary_password) setSecret({ user: r.username, password: r.temporary_password });
      reload();
    } catch (e) {
      setErr(e as ApiError);
    }
  };

  if (loading && !data) return <Loading />;
  return (
    <>
      <Card title="Accounts" bodyClass="" actions={
        <button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} /> Add user</button>
      }>
        <div style={{ padding: err || error ? "12px 18px 0" : 0 }}><ErrorBox error={err ?? error} /></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Sign-in</th><th>MFA</th><th>Last sign-in</th><th></th></tr></thead>
            <tbody>
              {data?.map((u) => (
                <tr key={u.id}>
                  <td>
                    <b>{u.username}</b>{u.id === me.user.id && <span className="muted"> (you)</span>}
                    <div className="muted small">{u.display_name}{u.email ? ` · ${u.email}` : ""}</div>
                  </td>
                  <td>
                    <select value={u.role} disabled={u.source === "ldap"} style={{ width: 120 }}
                      onChange={(e) => act(() => patch(`/api/users/${u.id}`, { role: e.target.value as Role }))}
                      aria-label={`Role of ${u.username}`}>
                      <option value="viewer">viewer</option>
                      <option value="operator">operator</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>
                    {u.source === "ldap" ? <span className="badge info">directory</span> : <span className="badge">local</span>}{" "}
                    {u.disabled && <span className="badge bad">disabled</span>}{" "}
                    {u.locked && <span className="badge warn">locked</span>}
                  </td>
                  <td>{u.mfa ? <span className="badge ok">on</span> : <span className="badge warn">at next sign-in</span>}</td>
                  <td className="nowrap">{ago(u.last_login_at)}</td>
                  <td>
                    <div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                      {u.locked && <button className="btn small" onClick={() => act(() => post(`/api/users/${u.id}/unlock`))}>Unlock</button>}
                      {u.mfa && <button className="btn small" onClick={() => confirm(`Reset MFA for ${u.username}? They set it up again at the next sign-in.`) && act(() => post(`/api/users/${u.id}/reset-mfa`))}>Reset MFA</button>}
                      {u.source === "local" && <button className="btn small" onClick={() => confirm(`Give ${u.username} a new temporary password?`) && act(() => post(`/api/users/${u.id}/reset-password`, {}))}>Reset password</button>}
                      {u.id !== me.user.id && <button className="btn small" onClick={() => act(() => patch(`/api/users/${u.id}`, { disabled: !u.disabled }))}>{u.disabled ? "Enable" : "Disable"}</button>}
                      {u.id !== me.user.id && <button className="btn small danger" onClick={() => confirm(`Delete ${u.username}?`) && act(() => del(`/api/users/${u.id}`))}>Delete</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="muted small">
        Roles: <b>viewer</b> sees everything except administration; <b>operator</b> also writes specs and approves
        builds and teardowns; <b>admin</b> also manages accounts and settings. Everyone must use two-factor
        authentication.
      </div>
      {adding && <AddUser onClose={() => setAdding(false)} onCreated={(u) => { setAdding(false); reload(); if (u.temporary_password) setSecret({ user: u.username, password: u.temporary_password }); }} />}
      {secret && (
        <Modal title="Temporary password" onClose={() => setSecret(null)} footer={<button className="btn primary" onClick={() => setSecret(null)}>Done</button>}>
          <div>Give this to <b>{secret.user}</b> over a safe channel. It is shown only once; they choose their own password and set up MFA at their first sign-in.</div>
          <div className="codes" style={{ gridTemplateColumns: "1fr" }}>{secret.password}</div>
        </Modal>
      )}
    </>
  );
}

function AddUser({ onClose, onCreated }: { onClose: () => void; onCreated: (u: User) => void }) {
  const [form, setForm] = useState({ username: "", display_name: "", email: "", role: "viewer" as Role });
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
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
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  return (
    <Modal title="Add user" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">Username<input value={form.username} onChange={set("username")} required autoFocus /></label>
        <label className="field">Display name<input value={form.display_name} onChange={set("display_name")} /></label>
        <label className="field">Email<input type="email" value={form.email} onChange={set("email")} /></label>
        <label className="field">Role
          <select value={form.role} onChange={set("role")}>
            <option value="viewer">viewer</option><option value="operator">operator</option><option value="admin">admin</option>
          </select>
        </label>
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
