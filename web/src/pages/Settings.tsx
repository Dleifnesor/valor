import { useEffect, useState } from "react";
import { ApiError, get, post, put } from "../api";
import { useApi } from "../hooks";
import { Card, ErrorBox, Loading } from "../components/ui";

type Kind = "email" | "discord" | "slack" | "teams";
interface Channel {
  id?: string;
  type: Kind;
  name: string;
  enabled: boolean;
  events: "problems" | "all";
  config: Record<string, any>;
}

const KIND_LABEL: Record<Kind, string> = { email: "Email (SMTP)", discord: "Discord webhook", slack: "Slack webhook", teams: "Microsoft Teams (Workflows webhook)" };

export function Settings() {
  const [tab, setTab] = useState<"notifications" | "directory" | "ai" | "about">("notifications");
  return (
    <>
      <div className="tabs" role="tablist">
        <button className={tab === "notifications" ? "active" : ""} onClick={() => setTab("notifications")}>Notifications</button>
        <button className={tab === "directory" ? "active" : ""} onClick={() => setTab("directory")}>Directory (LDAP / AD)</button>
        <button className={tab === "ai" ? "active" : ""} onClick={() => setTab("ai")}>AI provider</button>
        <button className={tab === "about" ? "active" : ""} onClick={() => setTab("about")}>Certificate & about</button>
      </div>
      {tab === "notifications" && <NotificationSettings />}
      {tab === "directory" && <DirectorySettings />}
      {tab === "ai" && <AiSettings />}
      {tab === "about" && <About />}
    </>
  );
}

function NotificationSettings() {
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<{ channels: Channel[] }>("/api/settings/notifications").then((r) => setChannels(r.channels)).catch(setErr);
  }, []);
  if (!channels) return err ? <ErrorBox error={err} /> : <Loading />;

  const update = (i: number, ch: Partial<Channel>) => setChannels(channels.map((c, j) => (i === j ? { ...c, ...ch } : c)));
  const setCfg = (i: number, k: string, v: any) => update(i, { config: { ...channels[i].config, [k]: v } });
  const add = (type: Kind) => setChannels([...channels, {
    type, name: KIND_LABEL[type].split(" ")[0], enabled: true, events: "problems",
    config: type === "email" ? { host: "", port: 587, security: "starttls", username: "", password: "", from: "", to: [] } : { url: "" },
  }]);
  const save = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await put<{ channels: Channel[] }>("/api/settings/notifications", { channels });
      setChannels(r.channels);
      setMsg("Saved.");
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  };
  const test = async (id?: string) => {
    if (!id) { setMsg("Save first, then send a test."); return; }
    setErr(null); setMsg("Sending…");
    try {
      const r = await post<{ ok: boolean; message: string }>("/api/settings/notifications/test", { id });
      setMsg(r.ok ? "Test sent - check the channel." : `Test failed: ${r.message}`);
    } catch (e) { setErr(e as ApiError); setMsg(null); }
  };

  return (
    <Card title="Where VALOR reports builds and problems" actions={
      <div className="row">
        <select defaultValue="" onChange={(e) => { if (e.target.value) add(e.target.value as Kind); e.target.value = ""; }} style={{ width: 200 }} aria-label="Add a channel">
          <option value="">Add a channel…</option>
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
        </select>
        <button className="btn primary" onClick={save} disabled={busy}>Save</button>
      </div>
    }>
      <div className="stack">
        <div className="muted small">
          In-app notifications (the bell) are always on. "Problems only" sends failed builds, failed verifications,
          locked accounts and certificate warnings; "Everything" also sends successful builds. Secrets (webhook URLs,
          SMTP passwords) are stored encrypted and never shown again.
        </div>
        {!channels.length && <div className="empty">No external channels yet.</div>}
        {channels.map((c, i) => (
          <div key={c.id ?? `new-${i}`} className="card" style={{ padding: 14 }}>
            <div className="row" style={{ marginBottom: 10 }}>
              <b>{KIND_LABEL[c.type]}</b>
              <span className="grow" />
              <label className="checkbox"><input type="checkbox" checked={c.enabled} onChange={(e) => update(i, { enabled: e.target.checked })} /> enabled</label>
              <button className="btn small" onClick={() => test(c.id)}>Send test</button>
              <button className="btn small danger" onClick={() => setChannels(channels.filter((_, j) => j !== i))}>Remove</button>
            </div>
            <div className="grid cols-2">
              <label className="field">Name<input value={c.name} onChange={(e) => update(i, { name: e.target.value })} /></label>
              <label className="field">Send
                <select value={c.events} onChange={(e) => update(i, { events: e.target.value as Channel["events"] })}>
                  <option value="problems">Problems only</option><option value="all">Everything</option>
                </select>
              </label>
              {c.type === "email" ? (
                <>
                  <label className="field">SMTP server<input value={c.config.host ?? ""} onChange={(e) => setCfg(i, "host", e.target.value)} placeholder="smtp.example.com" /></label>
                  <label className="field">Port<input type="number" value={c.config.port ?? 587} onChange={(e) => setCfg(i, "port", Number(e.target.value))} /></label>
                  <label className="field">Security
                    <select value={c.config.security ?? "starttls"} onChange={(e) => setCfg(i, "security", e.target.value)}>
                      <option value="starttls">STARTTLS (587)</option><option value="tls">TLS (465)</option>
                    </select>
                  </label>
                  <label className="field">Username<input value={c.config.username ?? ""} onChange={(e) => setCfg(i, "username", e.target.value)} autoComplete="off" /></label>
                  <label className="field">Password<input type="password" value={c.config.password ?? ""} onChange={(e) => setCfg(i, "password", e.target.value)} autoComplete="new-password" placeholder={c.id ? "unchanged" : ""} /></label>
                  <label className="field">From<input value={c.config.from ?? ""} onChange={(e) => setCfg(i, "from", e.target.value)} placeholder="valor@example.com" /></label>
                  <label className="field">To <span className="hint">comma-separated</span>
                    <input value={(c.config.to ?? []).join(", ")} onChange={(e) => setCfg(i, "to", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
                  </label>
                </>
              ) : (
                <label className="field" style={{ gridColumn: "1 / -1" }}>Webhook URL
                  <input value={c.config.url ?? ""} onChange={(e) => setCfg(i, "url", e.target.value)} placeholder="https://…" autoComplete="off" />
                </label>
              )}
            </div>
          </div>
        ))}
        {msg && <div className="alert info">{msg}</div>}
        <ErrorBox error={err} />
      </div>
    </Card>
  );
}

interface Ldap {
  enabled: boolean; url: string; starttls: boolean; ca_pem: string; bind_dn: string; bind_password: string;
  user_base: string; user_filter: string; attr_display_name: string; attr_email: string;
  group_admin: string; group_operator: string; group_viewer: string; nested_groups: boolean; timeout: number;
}

function DirectorySettings() {
  const [s, setS] = useState<Ldap | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [testUser, setTestUser] = useState("");
  useEffect(() => { get<Ldap>("/api/settings/ldap").then(setS).catch(setErr); }, []);
  if (!s) return err ? <ErrorBox error={err} /> : <Loading />;
  const set = (k: keyof Ldap, v: any) => setS({ ...s, [k]: v });
  const save = async () => {
    setErr(null); setMsg(null);
    try { setS(await put<Ldap>("/api/settings/ldap", s)); setMsg({ ok: true, text: "Saved." }); } catch (e) { setErr(e as ApiError); }
  };
  const test = async () => {
    setErr(null); setMsg({ ok: true, text: "Testing…" });
    try {
      const r = await post<{ ok: boolean; message: string }>("/api/settings/ldap/test", { username: testUser });
      setMsg({ ok: r.ok, text: r.message });
    } catch (e) { setErr(e as ApiError); setMsg(null); }
  };
  const text = (k: keyof Ldap, label: string, hint?: string, ph?: string) => (
    <label className="field">{label}{hint && <span className="hint">{hint}</span>}
      <input value={String(s[k] ?? "")} onChange={(e) => set(k, e.target.value)} placeholder={ph} autoComplete="off" />
    </label>
  );
  return (
    <Card title="Sign in with LDAP or Active Directory" actions={<button className="btn primary" onClick={save}>Save</button>}>
      <div className="stack">
        <label className="checkbox"><input type="checkbox" checked={s.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Allow directory accounts to sign in</label>
        <div className="muted small">Directory users still set up VALOR's two-factor authentication. Their role comes from the groups below (checked at every sign-in).</div>
        <div className="grid cols-2">
          {text("url", "Server URL", "ldaps:// (or ldap:// with StartTLS)", "ldaps://dc1.example.com")}
          <label className="checkbox" style={{ alignSelf: "end" }}><input type="checkbox" checked={s.starttls} onChange={(e) => set("starttls", e.target.checked)} /> Use StartTLS (ldap:// only)</label>
          {text("bind_dn", "Service account DN", undefined, "CN=valor-svc,OU=Service,DC=example,DC=com")}
          <label className="field">Service account password
            <input type="password" value={s.bind_password} onChange={(e) => set("bind_password", e.target.value)} autoComplete="new-password" />
          </label>
          {text("user_base", "Search base for users", undefined, "OU=People,DC=example,DC=com")}
          {text("user_filter", "User filter", "{username} is replaced (escaped)")}
          {text("group_admin", "Group for admins (DN)")}
          {text("group_operator", "Group for operators (DN)")}
          {text("group_viewer", "Group for viewers (DN)")}
          <label className="checkbox" style={{ alignSelf: "end" }}><input type="checkbox" checked={s.nested_groups} onChange={(e) => set("nested_groups", e.target.checked)} /> Include nested groups (Active Directory)</label>
        </div>
        <label className="field">CA certificate (PEM) <span className="hint">leave empty to trust the system CAs</span>
          <textarea className="mono" rows={5} value={s.ca_pem} onChange={(e) => set("ca_pem", e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
        </label>
        <div className="row">
          <input style={{ maxWidth: 240 }} placeholder="Optional: a username to look up" value={testUser} onChange={(e) => setTestUser(e.target.value)} />
          <button className="btn" onClick={test}>Test connection</button>
        </div>
        {msg && <div className={`alert ${msg.ok ? "info" : "error"}`}>{msg.text}</div>}
        <ErrorBox error={err} />
      </div>
    </Card>
  );
}

interface Ai {
  provider: "none" | "anthropic" | "openai";
  model: string;
  base_url: string;
  api_key: string;
  max_tokens: number;
  daily_tokens_per_user: number;
  timeout: number;
  providers?: string[];
  anthropic_models?: string[];
}

function AiSettings() {
  const [s, setS] = useState<Ai | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const usage = useApi<{ users: { username: string; requests: number; input: number; output: number; today: number }[] }>("/api/settings/ai/usage");
  useEffect(() => { get<Ai>("/api/settings/ai").then(setS).catch(setErr); }, []);
  if (!s) return err ? <ErrorBox error={err} /> : <Loading />;
  const set = (k: keyof Ai, v: any) => setS({ ...s, [k]: v });
  const save = async () => {
    setErr(null); setMsg(null);
    const { providers, anthropic_models, ...body } = s;
    try { setS({ ...s, ...(await put<Ai>("/api/settings/ai", body)) }); setMsg({ ok: true, text: "Saved." }); } catch (e) { setErr(e as ApiError); }
  };
  const test = async () => {
    setErr(null); setMsg({ ok: true, text: "Asking the model…" });
    try {
      const r = await post<{ seconds: number; model: string; reply: string }>("/api/settings/ai/test");
      setMsg({ ok: true, text: `${r.model} answered in ${r.seconds} s: "${r.reply}"` });
    } catch (e) { setErr(e as ApiError); setMsg(null); }
  };
  return (
    <>
      <Card title="AI provider for the chat builder" actions={<button className="btn primary" onClick={save}>Save</button>}>
        <div className="stack">
          <div className="muted small">
            The chat builder turns a description into a range spec. VALOR sends the conversation, its spec reference and
            the cluster's free VLANs/networks to the provider - no passwords or keys. Specs are validated, and nothing is
            built until someone plans and approves it. The API key is stored encrypted and never shown again.
          </div>
          <div className="grid cols-2">
            <label className="field">Provider
              <select value={s.provider} onChange={(e) => set("provider", e.target.value)}>
                <option value="none">None (chat builder off)</option>
                <option value="anthropic">Anthropic (Claude)</option>
                <option value="openai">OpenAI-compatible endpoint (e.g. a local model server)</option>
              </select>
            </label>
            {s.provider === "anthropic" && (
              <label className="field">Model
                <select value={s.model || s.anthropic_models?.[0]} onChange={(e) => set("model", e.target.value)}>
                  {s.anthropic_models?.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
            )}
            {s.provider === "openai" && (
              <>
                <label className="field">Base URL <span className="hint">ending in /v1; http:// only to a private IP</span>
                  <input value={s.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://llm.example.org/v1" />
                </label>
                <label className="field">Model
                  <input value={s.model} onChange={(e) => set("model", e.target.value)} placeholder="llama-3.3-70b" />
                </label>
              </>
            )}
            {s.provider !== "none" && (
              <>
                <label className="field">API key {s.provider === "openai" && <span className="hint">optional for local servers</span>}
                  <input type="password" value={s.api_key} onChange={(e) => set("api_key", e.target.value)} autoComplete="new-password" />
                </label>
                <label className="field">Daily token budget per user <span className="hint">0 = unlimited</span>
                  <input type="number" min={0} value={s.daily_tokens_per_user} onChange={(e) => set("daily_tokens_per_user", Number(e.target.value))} />
                </label>
                <label className="field">Max tokens per answer
                  <input type="number" min={256} max={32000} value={s.max_tokens} onChange={(e) => set("max_tokens", Number(e.target.value))} />
                </label>
              </>
            )}
          </div>
          {s.provider !== "none" && <div className="row"><button className="btn" onClick={test}>Test connection</button></div>}
          {msg && <div className={`alert ${msg.ok ? "info" : "error"}`}>{msg.text}</div>}
          <ErrorBox error={err} />
        </div>
      </Card>
      <Card title="Usage (last 30 days)">
        {!usage.data?.users.length ? <div className="empty">No requests yet.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>User</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>Last 24 h</th></tr></thead>
              <tbody>
                {usage.data.users.map((u) => (
                  <tr key={u.username}><td>{u.username}</td><td>{u.requests}</td><td>{u.input}</td><td>{u.output}</td><td>{u.today}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function About() {
  const { data } = useApi<any>("/api/status");
  const cert = data?.checks?.find((c: any) => c.name === "Certificate");
  return (
    <div className="grid cols-2">
      <Card title="Web certificate">
        {!cert ? <div className="muted">No certificate information.</div> : (
          <dl className="kv">
            <dt>Names</dt><dd className="mono">{(cert.names ?? []).join(", ")}</dd>
            <dt>Issuer</dt><dd className="mono">{cert.issuer}</dd>
            <dt>Expires</dt><dd>{new Date(cert.expires).toLocaleDateString()} ({cert.days_left} days)</dd>
          </dl>
        )}
        <hr />
        <div className="small">
          If VALOR uses its own certificate authority, <a href="/ca.crt" download>download the CA certificate</a> and add it
          to your browser's or operating system's trusted roots. The certificate is renewed automatically.
        </div>
      </Card>
      <Card title="About">
        <dl className="kv">
          <dt>Version</dt><dd>{data?.version ?? "–"}</dd>
          <dt>Instance</dt><dd>{data?.instance ?? "–"}</dd>
          <dt>VALOR VM</dt><dd>{data?.hostname ?? "–"}</dd>
        </dl>
        <hr />
        <div className="small muted">
          Change the installation itself (network, storage, TLS mode) by re-running the installer on a Proxmox node:
          <code> ./install.sh --upgrade</code> keeps your data.
        </div>
      </Card>
    </div>
  );
}
