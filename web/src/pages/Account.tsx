// *looks in the mirror* OwO web/src/pages/Account.tsx is YOUR account page! ^w^
// It shows your details, lets local accounts change their password, and lets anyone make fresh MFA recovery codes
// (after typing their password again) *wags tail*
import { FormEvent, useState } from "react";
// post + ApiError (the API), Me (who's signed in), when (dates), Card/ErrorBox (UI pieces) UwU
import { ApiError, post } from "../api";
import { Me } from "../types";
import { when } from "../hooks";
import { Card, ErrorBox } from "../components/ui";

// The signed-in person's own account: their details, a new password (local accounts) and new recovery codes.

// Account: `me` = the signed-in session; onChanged = tell the app about the updated session (after a password
// change) >w<
export function Account({ me, onChanged }: { me: Me; onChanged: (session: Me) => void }) {
  const user = me.user;
  // State: current + new password, a success message, the last error, the password for new codes, and the new codes OwO
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [pw2, setPw2] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);

  // change(): change the password; the server signs out your OTHER sessions ^w^
  const change = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null); setMsg(null);
    try {
      onChanged(await post<Me>("/api/auth/password", { current: cur, new: next }));
      setCur(""); setNext("");
      setMsg("Password changed. Other sessions were signed out.");
    } catch (x) { setErr(x as ApiError); }
  };
  // regen(): make new recovery codes (the old ones stop working) UwU
  const regen = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      setCodes((await post<{ recovery_codes: string[] }>("/api/auth/recovery-codes", { password: pw2 })).recovery_codes);
      setPw2("");
    } catch (x) { setErr(x as ApiError); }
  };

  // Two columns: the profile on the left, the forms on the right >w<
  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      {/* The profile: a definition list of your account's facts OwO */}
      <Card title="Profile">
        <dl className="kv">
          <dt>Username</dt><dd>{user.username}</dd>
          <dt>Name</dt><dd>{user.display_name || "–"}</dd>
          <dt>Email</dt><dd>{user.email || "–"}</dd>
          <dt>Role</dt><dd>{user.role}</dd>
          <dt>Account</dt><dd>{user.source === "ldap" ? "directory (LDAP / AD)" : "local"}</dd>
          <dt>Two-factor</dt><dd>{user.mfa ? "on (authenticator app)" : "not set up"}</dd>
          <dt>Last sign-in</dt><dd>{when(user.last_login_at)}</dd>
        </dl>
      </Card>
      {/* The right column ^w^ */}
      <div className="stack">
        {/* Only local accounts change their password here (directory accounts do it in the directory) UwU */}
        {user.source === "local" && (
          <Card title="Change password">
            {/* The form: current password, new password (12+ chars), and the button (disabled until both look OK) >w< */}
            <form className="stack" onSubmit={change}>
              <label className="field">Current password<input name="current-password" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></label>
              <label className="field">New password <span className="hint">12 characters or more</span>
                <input name="new-password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} />
              </label>
              <div className="row end"><button className="btn primary" disabled={!cur || next.length < 12}>Change password</button></div>
            </form>
          </Card>
        )}
        {/* Recovery codes: freshly made codes are shown ONCE; otherwise the form to make new ones OwO */}
        <Card title="Recovery codes">
          {codes ? (
            <div className="stack">
              <div className="muted">New codes - the old ones no longer work. Save them now; they are not shown again.</div>
              <div className="codes">{codes.map((code) => <span key={code}>{code}</span>)}</div>
            </div>
          ) : (
            <form className="stack" onSubmit={regen}>
              <div className="muted small">Lost your codes or used most of them? Make new ones (confirm with your password).</div>
              <label className="field">Password<input name="password" type="password" autoComplete="current-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required /></label>
              <div className="row end"><button className="btn" disabled={!pw2}>Make new codes</button></div>
            </form>
          )}
        </Card>
        {/* The success message and any error ^w^ *wags tail* */}
        {msg && <div className="alert ok">{msg}</div>}
        <ErrorBox error={err} />
      </div>
    </div>
  );
}
