import { FormEvent, useEffect, useState } from "react";
import { ApiError, post, setCsrf } from "../api";
import { Me } from "../types";
import { ErrorBox, Logo } from "../components/ui";

type Step = "password" | "mfa" | "enroll" | "codes" | "change";

export function SignIn({ me, onSignedIn }: { me: Me | null; onSignedIn: (m: Me) => void }) {
  const initial: Step = me?.stage === "mfa" ? "mfa" : me?.stage === "enroll" ? "enroll"
    : me?.user.must_change_password ? "change" : "password";
  const [step, setStep] = useState<Step>(initial);
  const [error, setError] = useState<ApiError | string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Me | null>(null);

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

  const finish = (m: Me) => {
    setCsrf(m.csrf);
    if (m.recovery_codes?.length) {
      setPending(m);
      setStep("codes");
    } else if (m.user.must_change_password) {
      setPending(m);
      setStep("change");
    } else {
      onSignedIn(m);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <div className="logo">
          <Logo size={38} />
          <div>
            <b>VALOR</b>
            <div className="muted small">Verified, segmented test environments on Proxmox VE</div>
          </div>
        </div>
        {step === "password" && <PasswordStep busy={busy} run={run} next={setStep} />}
        {step === "mfa" && <CodeStep busy={busy} run={run} done={finish} back={() => setStep("password")} />}
        {step === "enroll" && <EnrollStep busy={busy} run={run} done={finish} />}
        {step === "codes" && pending && (
          <CodesStep codes={pending.recovery_codes ?? []}
            done={() => (pending.user.must_change_password ? setStep("change") : onSignedIn(pending))} />
        )}
        {step === "change" && <ChangeStep busy={busy} run={run} done={onSignedIn} />}
        <ErrorBox error={error} />
        <div className="muted small">
          Using VALOR's own certificate authority? <a href="/ca.crt" download>Download the CA certificate</a> and trust
          it in your browser or OS to remove the certificate warning.
        </div>
      </div>
    </div>
  );
}

type Run = (fn: () => Promise<void>) => Promise<void>;

function PasswordStep({ busy, run, next }: { busy: boolean; run: Run; next: (s: Step) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const r = await post<{ stage: "mfa" | "enroll"; csrf: string }>("/api/auth/login", { username, password });
      setCsrf(r.csrf);
      setPassword("");
      next(r.stage);
    });
  };
  return (
    <form className="stack" onSubmit={submit}>
      <h2 style={{ margin: 0 }}>Sign in</h2>
      <label className="field">Username
        <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label className="field">Password
        <input type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} required />
      </label>
      <button className="btn primary" disabled={busy || !username || !password}>Continue</button>
    </form>
  );
}

function CodeStep({ busy, run, done, back }: { busy: boolean; run: Run; done: (m: Me) => void; back: () => void }) {
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => done(await post<Me>("/api/auth/mfa", { code })));
  };
  return (
    <form className="stack" onSubmit={submit}>
      <h2 style={{ margin: 0 }}>Two-factor authentication</h2>
      <div className="muted">
        {recovery ? "Enter one of your recovery codes (each works once)."
          : "Enter the 6-digit code from your authenticator app."}
      </div>
      <input autoFocus value={code} onChange={(e) => setCode(e.target.value)}
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

function EnrollStep({ busy, run, done }: { busy: boolean; run: Run; done: (m: Me) => void }) {
  const [data, setData] = useState<{ secret: string; qr_svg: string; issuer: string } | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
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
  return (
    <form className="stack" onSubmit={submit}>
      <h2 style={{ margin: 0 }}>Set up two-factor authentication</h2>
      <div className="muted">
        VALOR requires an authenticator app (for example Aegis, Google Authenticator, Microsoft Authenticator or
        1Password). Scan the code, then enter the 6 digits it shows.
      </div>
      <ErrorBox error={err} />
      {data && (
        <>
          <div className="qr"><img src={src} alt="QR code for your authenticator app" /></div>
          <details>
            <summary className="small">Can't scan? Enter the key by hand</summary>
            <div className="mono" style={{ wordBreak: "break-all", marginTop: 6 }}>{data.secret.replace(/(.{4})/g, "$1 ")}</div>
            <div className="muted small">Account: {data.issuer} · time-based · 6 digits · 30 seconds</div>
          </details>
          <input autoFocus value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric"
            autoComplete="one-time-code" placeholder="123 456" className="mono" maxLength={7} />
          <button className="btn primary" disabled={busy || code.replace(/\s/g, "").length !== 6}>Turn on</button>
        </>
      )}
    </form>
  );
}

function CodesStep({ codes, done }: { codes: string[]; done: () => void }) {
  const [saved, setSaved] = useState(false);
  const download = () => {
    const blob = new Blob([`VALOR recovery codes (each works once)\n\n${codes.join("\n")}\n`], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "valor-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="stack">
      <h2 style={{ margin: 0 }}>Save your recovery codes</h2>
      <div className="muted">
        If you lose your phone, each of these codes lets you sign in once. They are shown only now.
      </div>
      <div className="codes">{codes.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="row">
        <button type="button" className="btn" onClick={download}>Download</button>
        <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>Copy</button>
      </div>
      <label className="checkbox"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        I saved them somewhere safe</label>
      <button className="btn primary" disabled={!saved} onClick={done}>Continue</button>
    </div>
  );
}

function ChangeStep({ busy, run, done }: { busy: boolean; run: Run; done: (m: Me) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => done(await post<Me>("/api/auth/password", { current, new: next })));
  };
  return (
    <form className="stack" onSubmit={submit}>
      <h2 style={{ margin: 0 }}>Choose a new password</h2>
      <div className="muted">Your administrator gave you a temporary password. Pick your own (12+ characters).</div>
      <label className="field">Temporary password
        <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      </label>
      <label className="field">New password
        <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={12} />
      </label>
      <label className="field">New password again
        <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
      </label>
      {again && next !== again && <div className="alert warn">The passwords don't match.</div>}
      <button className="btn primary" disabled={busy || next.length < 12 || next !== again}>Save and continue</button>
    </form>
  );
}
