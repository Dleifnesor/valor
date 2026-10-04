import { FormEvent, useState } from "react";
import { ApiError, post } from "../api";
import { Me } from "../types";
import { when } from "../hooks";
import { Card, ErrorBox } from "../components/ui";

export function Account({ me, onChanged }: { me: Me; onChanged: (m: Me) => void }) {
  const u = me.user;
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [pw2, setPw2] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);

  const change = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null); setMsg(null);
    try {
      onChanged(await post<Me>("/api/auth/password", { current: cur, new: next }));
      setCur(""); setNext("");
      setMsg("Password changed. Other sessions were signed out.");
    } catch (x) { setErr(x as ApiError); }
  };
  const regen = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      setCodes((await post<{ recovery_codes: string[] }>("/api/auth/recovery-codes", { password: pw2 })).recovery_codes);
      setPw2("");
    } catch (x) { setErr(x as ApiError); }
  };

  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <Card title="Profile">
        <dl className="kv">
          <dt>Username</dt><dd>{u.username}</dd>
          <dt>Name</dt><dd>{u.display_name || "–"}</dd>
          <dt>Email</dt><dd>{u.email || "–"}</dd>
          <dt>Role</dt><dd>{u.role}</dd>
          <dt>Account</dt><dd>{u.source === "ldap" ? "directory (LDAP / AD)" : "local"}</dd>
          <dt>Two-factor</dt><dd>{u.mfa ? "on (authenticator app)" : "not set up"}</dd>
          <dt>Last sign-in</dt><dd>{when(u.last_login_at)}</dd>
        </dl>
      </Card>
      <div className="stack">
        {u.source === "local" && (
          <Card title="Change password">
            <form className="stack" onSubmit={change}>
              <label className="field">Current password<input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></label>
              <label className="field">New password <span className="hint">12 characters or more</span>
                <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} />
              </label>
              <div className="row end"><button className="btn primary" disabled={!cur || next.length < 12}>Change password</button></div>
            </form>
          </Card>
        )}
        <Card title="Recovery codes">
          {codes ? (
            <div className="stack">
              <div className="muted">New codes - the old ones no longer work. Save them now; they are not shown again.</div>
              <div className="codes">{codes.map((c) => <span key={c}>{c}</span>)}</div>
            </div>
          ) : (
            <form className="stack" onSubmit={regen}>
              <div className="muted small">Lost your codes or used most of them? Make new ones (confirm with your password).</div>
              <label className="field">Password<input type="password" autoComplete="current-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required /></label>
              <div className="row end"><button className="btn" disabled={!pw2}>Make new codes</button></div>
            </form>
          )}
        </Card>
        {msg && <div className="alert ok">{msg}</div>}
        <ErrorBox error={err} />
      </div>
    </div>
  );
}
