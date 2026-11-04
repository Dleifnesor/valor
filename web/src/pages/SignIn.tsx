// *waves at the front door* OwO web/src/pages/SignIn.tsx: SIGNING IN, one step at a time! ^w^
// Password, then the authenticator code; or, the very first time, setting up the authenticator and saving the
// recovery codes; and a new password when the account must change it *wags tail*
import { FormEvent, useEffect, useState } from "react";
// post/setCsrf/ApiError, the session type, ErrorBox and the logo UwU
import { ApiError, post, setCsrf } from "../api";
import { Me } from "../types";
import { ErrorBox, Logo } from "../components/ui";

// Signing in, one step at a time: password, then the authenticator code - or, the first time, setting up the
// authenticator and saving the recovery codes - and a new password when the account must change it.

// Step: which screen is showing >w<
type Step = "password" | "mfa" | "enroll" | "codes" | "change";

// SignIn: `me` = a half-finished session (the server says which stage it's at); onSignedIn = we're fully in OwO
export function SignIn({ me, onSignedIn }: { me: Me | null; onSignedIn: (session: Me) => void }) {
  // Start where the session left off ^w^
  const initial: Step = me?.stage === "mfa" ? "mfa" : me?.stage === "enroll" ? "enroll"
    : me?.user.must_change_password ? "change" : "password";
  const [step, setStep] = useState<Step>(initial);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Me | null>(null);

  // run(): run one step's request with a busy flag and error handling UwU
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e : String(e));
    } finally {
      setBusy(false);
    }
  };

  // finish(): the server answered with a full session: first show new recovery codes, then a required password change,
  // otherwise we're done >w<
  const finish = (session: Me) => {
    setCsrf(session.csrf);
    if (session.recovery_codes?.length) {
      setPending(session);
      setStep("codes");
    } else if (session.user.must_change_password) {
      setPending(session);
      setStep("change");
    } else {
      onSignedIn(session);
    }
  };

  // The sign-in card OwO
  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        {/* Logo and tagline */}
        <div className="logo">
          <Logo size={38} />
          <div>
            <b>VALOR</b>
            <div className="muted small">Verified, segmented test environments on Proxmox VE</div>
          </div>
        </div>
        {/* The current step */}
        {step === "password" && <PasswordStep busy={busy} run={run} next={setStep} />}
        {step === "mfa" && <CodeStep busy={busy} run={run} done={finish} back={() => setStep("password")} />}
        {step === "enroll" && <EnrollStep busy={busy} run={run} done={finish} />}
        {step === "codes" && pending && (
          <CodesStep codes={pending.recovery_codes ?? []}
            done={() => (pending.user.must_change_password ? setStep("change") : onSignedIn(pending))} />
        )}
        {step === "change" && <ChangeStep busy={busy} run={run} done={onSignedIn} />}
        {/* Errors, and a hint about trusting VALOR's own CA */}
        <ErrorBox error={error} />
        <div className="muted small">
          Using VALOR's own certificate authority? <a href="/ca.crt" download>Download the CA certificate</a> and trust
          it in your browser or OS to remove the certificate warning.
        </div>
      </div>
    </div>
  );
}

// Run: the type of run() ^w^
type Run = (fn: () => Promise<void>) => Promise<void>;

// PasswordStep: username + password; the answer says the next stage (mfa or enroll) and gives a CSRF token UwU
function PasswordStep({ busy, run, next }: { busy: boolean; run: Run; next: (step: Step) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const result = await post<{ stage: "mfa" | "enroll"; csrf: string }>("/api/auth/login", { username, password });
      setCsrf(result.csrf);
      setPassword("");
      next(result.stage);
    });
  };
  // The form >w<
  return (
    <form className="stack" onSubmit={submit}>
      {/* Title, username, password, Continue */}
      <h2 style={{ margin: 0 }}>Sign in</h2>
      <label className="field">Username
        <input name="username" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label className="field">Password
        <input name="password" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} required />
      </label>
      <button className="btn primary" disabled={busy || !username || !password}>Continue</button>
    </form>
  );
}

// CodeStep: a 6-digit authenticator code, or (switch) a recovery code OwO
function CodeStep({ busy, run, done, back }: { busy: boolean; run: Run; done: (session: Me) => void; back: () => void }) {
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => done(await post<Me>("/api/auth/mfa", { code })));
  };
  // The form ^w^
  return (
    <form className="stack" onSubmit={submit}>
      {/* Title and what to type */}
      <h2 style={{ margin: 0 }}>Two-factor authentication</h2>
      <div className="muted">
        {recovery ? "Enter one of your recovery codes (each works once)."
          : "Enter the 6-digit code from your authenticator app."}
      </div>
      {/* The code field (numeric keyboard for authenticator codes), Verify, and the switches */}
      <input name="code" autoFocus value={code} onChange={(e) => setCode(e.target.value)}
        inputMode={recovery ? "text" : "numeric"} autoComplete="one-time-code"
        placeholder={recovery ? "abcde-12345" : "123 456"} className="mono" maxLength={recovery ? 16 : 7} />
      <button className="btn primary" disabled={busy || code.replace(/\s/g, "").length < 6}>Verify</button>
      <div className="row">
        <button type="button" className="btn ghost small" onClick={() => { setRecovery(!recovery); setCode(""); }}>
          {recovery ? "Use the authenticator app" : "Use a recovery code"}
        </button>
        <span className="grow" />
        <button type="button" className="btn ghost small" onClick={back}>Start over</button>
      </div>
    </form>
  );
}

// EnrollStep: set up the authenticator: show the QR code (and the key for typing by hand), then confirm with a code UwU
function EnrollStep({ busy, run, done }: { busy: boolean; run: Run; done: (session: Me) => void }) {
  const [data, setData] = useState<{ secret: string; qr_svg: string; issuer: string } | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  // Ask the server for a new secret when this step opens >w<
  useEffect(() => {
    post<{ secret: string; qr_svg: string; issuer: string }>("/api/auth/enroll/start")
      .then(setData)
      .catch((e) => setErr(e));
  }, []);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => done(await post<Me>("/api/auth/enroll/confirm", { code })));
  };
  // The QR code is an SVG generated by the server; shown as an image, never injected as HTML.
  const src = data ? "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data.qr_svg) : "";
  // The form OwO
  return (
    <form className="stack" onSubmit={submit}>
      {/* Title and which apps work */}
      <h2 style={{ margin: 0 }}>Set up two-factor authentication</h2>
      <div className="muted">
        VALOR requires an authenticator app (for example Aegis, Google Authenticator, Microsoft Authenticator or
        1Password). Scan the code, then enter the 6 digits it shows.
      </div>
      {/* Errors, then the QR code, the key by hand (folded), the code field and Turn on */}
      <ErrorBox error={err} />
      {data && (
        <>
          <div className="qr"><img src={src} alt="QR code for your authenticator app" /></div>
          <details>
            <summary className="small">Can't scan? Enter the key by hand</summary>
            <div className="mono" style={{ wordBreak: "break-all", marginTop: 6 }}>{data.secret.replace(/(.{4})/g, "$1 ")}</div>
            <div className="muted small">Account: {data.issuer} · time-based · 6 digits · 30 seconds</div>
          </details>
          <input name="code" autoFocus value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric"
            autoComplete="one-time-code" placeholder="123 456" className="mono" maxLength={7} />
          <button className="btn primary" disabled={busy || code.replace(/\s/g, "").length !== 6}>Turn on</button>
        </>
      )}
    </form>
  );
}

// CodesStep: show the recovery codes ONCE, with Download and Copy; Continue only after "I saved them" ^w^
function CodesStep({ codes, done }: { codes: string[]; done: () => void }) {
  const [saved, setSaved] = useState(false);
  // download(): save the codes as a text file UwU
  const download = () => {
    const blob = new Blob([`VALOR recovery codes (each works once)\n\n${codes.join("\n")}\n`], { type: "text/plain" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "valor-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(link.href);
  };
  // The step >w<
  return (
    <div className="stack">
      {/* Title, why they matter, the codes, the buttons, the checkbox and Continue */}
      <h2 style={{ margin: 0 }}>Save your recovery codes</h2>
      <div className="muted">
        If you lose your phone, each of these codes lets you sign in once. They are shown only now.
      </div>
      <div className="codes">{codes.map((code) => <span key={code}>{code}</span>)}</div>
      <div className="row">
        <button type="button" className="btn" onClick={download}>Download</button>
        <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>Copy</button>
      </div>
      <label className="checkbox"><input name="saved" type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        I saved them somewhere safe</label>
      <button className="btn primary" disabled={!saved} onClick={done}>Continue</button>
    </div>
  );
}

// ChangeStep: replace a temporary password (12+ characters, typed twice) OwO
function ChangeStep({ busy, run, done }: { busy: boolean; run: Run; done: (session: Me) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => done(await post<Me>("/api/auth/password", { current, new: next })));
  };
  // The form ^w^
  return (
    <form className="stack" onSubmit={submit}>
      {/* Title, note, the three password fields, a mismatch warning, and Save */}
      <h2 style={{ margin: 0 }}>Choose a new password</h2>
      <div className="muted">Your administrator gave you a temporary password. Pick your own (12+ characters).</div>
      <label className="field">Temporary password
        <input name="current-password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      </label>
      <label className="field">New password
        <input name="new-password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} />
      </label>
      <label className="field">New password again
        <input name="confirm-password" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
      </label>
      {again && next !== again && <div className="alert warn">The passwords don't match.</div>}
      <button className="btn primary" disabled={busy || next.length < 12 || next !== again}>Save and continue</button>
    </form>
  );
}
