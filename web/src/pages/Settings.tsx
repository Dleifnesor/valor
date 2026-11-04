// *fiddles with all the little knobs* OwO web/src/pages/Settings.tsx: the DATACENTER SETTINGS (admins only)! ^w^
// Notification channels, LDAP / Active Directory sign-in, the AI providers for the chats, and the web certificate.
// Each section loads its settings, edits a local copy, and saves it whole with PUT. Secrets come back MASKED and stay
// unchanged unless typed again *wags tail*
import { useEffect, useState } from "react";
// API calls, useApi, and UI pieces UwU
import { ApiError, get, post, put } from "../api";
import { useApi } from "../hooks";
import { Card, ErrorBox, IconName, Loading } from "../components/ui";

// Datacenter settings (admins only): notification channels, LDAP / Active Directory sign-in, the AI providers for
// the chats, and the web certificate. Each section loads its settings, edits a local copy and saves it whole with
// PUT; secrets (passwords, webhook URLs, API keys) come back blank and stay unchanged unless typed again.

// SettingsTab: the four sections. SETTINGS_TABS: their labels and icons for the Datacenter menu >w<
export type SettingsTab = "notifications" | "directory" | "ai" | "about";

/** The settings, one section each in the Datacenter menu. */
export const SETTINGS_TABS: { id: SettingsTab; label: string; icon: IconName }[] = [
  { id: "directory", label: "Directory (LDAP)", icon: "users" },
  { id: "ai", label: "AI providers", icon: "bot" },
  { id: "notifications", label: "Notifications", icon: "mail" },
  { id: "about", label: "Certificate", icon: "lock" },
];

// Settings: show the chosen section OwO
export function Settings({ tab }: { tab: SettingsTab }) {
  if (tab === "directory") return <DirectorySettings />;
  if (tab === "ai") return <AiSettings />;
  if (tab === "about") return <About />;
  return <NotificationSettings />;
}

// ---- Notifications ---------------------------------------------------------------------------------------------

// Kind / Channel: one notification channel (its config depends on the kind) ^w^
type Kind = "email" | "discord" | "slack" | "teams";
interface Channel {
  id?: string;               // none until saved
  type: Kind;
  name: string;
  enabled: boolean;
  events: "problems" | "all";
  config: Record<string, any>;
}

// KIND_LABEL: human names for the kinds UwU
const KIND_LABEL: Record<Kind, string> = { email: "Email (SMTP)", discord: "Discord webhook", slack: "Slack webhook", teams: "Microsoft Teams (Workflows webhook)" };

// NotificationSettings: the channel list >w<
function NotificationSettings() {
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Load the saved channels OwO
  useEffect(() => {
    get<{ channels: Channel[] }>("/api/settings/notifications").then((saved) => setChannels(saved.channels)).catch(setErr);
  }, []);
  if (!channels) return err ? <ErrorBox error={err} /> : <Loading />;

  // update(): change one channel in the local copy ^w^
  const update = (i: number, changes: Partial<Channel>) =>
    setChannels(channels.map((channel, j) => (i === j ? { ...channel, ...changes } : channel)));
  // add(): a new channel with sensible defaults (SMTP fields for email, a URL for webhooks) UwU
  const add = (type: Kind) => setChannels([...channels, {
    type, name: KIND_LABEL[type].split(" ")[0], enabled: true, events: "problems",
    config: type === "email" ? { host: "", port: 587, security: "starttls", username: "", password: "", from: "", to: [] } : { url: "" },
  }]);
  // save(): PUT the whole list; the server returns it with secrets masked >w<
  const save = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const saved = await put<{ channels: Channel[] }>("/api/settings/notifications", { channels });
      setChannels(saved.channels);
      setMsg("Saved.");
    } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  };
  // test(): send a test through a SAVED channel OwO
  const test = async (id?: string) => {
    if (!id) { setMsg("Save first, then send a test."); return; }
    setErr(null); setMsg("Sending…");
    try {
      const result = await post<{ ok: boolean; message: string }>("/api/settings/notifications/test", { id });
      setMsg(result.ok ? "Test sent - check the channel." : `Test failed: ${result.message}`);
    } catch (e) { setErr(e as ApiError); setMsg(null); }
  };

  // The card; its header has "Add a channel…" and Save ^w^
  return (
    <Card title="Where VALOR reports builds and problems" actions={
      <div className="row">
        <select name="add-channel" defaultValue="" onChange={(e) => { if (e.target.value) add(e.target.value as Kind); e.target.value = ""; }} style={{ width: 200 }} aria-label="Add a channel">
          <option value="">Add a channel…</option>
          {(Object.keys(KIND_LABEL) as Kind[]).map((kind) => <option key={kind} value={kind}>{KIND_LABEL[kind]}</option>)}
        </select>
        <button className="btn primary" onClick={save} disabled={busy}>Save</button>
      </div>
    }>
      <div className="stack">
        {/* What gets sent, and that secrets are encrypted */}
        <div className="muted small">
          In-app notifications (next to Tasks at the bottom) are always on. "Problems only" sends failed builds, failed verifications,
          locked accounts and certificate warnings; "Everything" also sends successful builds. Secrets (webhook URLs,
          SMTP passwords) are stored encrypted and never shown again.
        </div>
        {/* One card per channel (or "none yet"), then messages and errors */}
        {!channels.length && <div className="empty">No external channels yet.</div>}
        {channels.map((channel, i) => (
          <ChannelCard key={channel.id ?? `new-${i}`} channel={channel} onChange={(changes) => update(i, changes)}
            onTest={() => test(channel.id)} onRemove={() => setChannels(channels.filter((_, j) => j !== i))} />
        ))}
        {msg && <div className="alert info">{msg}</div>}
        <ErrorBox error={err} />
      </div>
    </Card>
  );
}

/** One channel's settings: SMTP fields for email, a webhook URL for the chat services. */
// ChannelCard: one channel: name, enabled, what to send, and its kind's fields UwU
function ChannelCard({ channel, onChange, onTest, onRemove }: {
  channel: Channel; onChange: (changes: Partial<Channel>) => void; onTest: () => void; onRemove: () => void;
}) {
  const config = channel.config;
  // setConfig(): change one config field >w<
  const setConfig = (key: string, value: any) => onChange({ config: { ...config, [key]: value } });
  return (
    <div className="card" style={{ padding: 14 }}>
      {/* Header: kind, enabled, Send test, Remove */}
      <div className="row" style={{ marginBottom: 10 }}>
        <b>{KIND_LABEL[channel.type]}</b>
        <span className="grow" />
        <label className="checkbox"><input name="enabled" type="checkbox" checked={channel.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} /> enabled</label>
        <button className="btn small" onClick={onTest}>Send test</button>
        <button className="btn small danger" onClick={onRemove}>Remove</button>
      </div>
      {/* Name and "send what" */}
      <div className="grid cols-2">
        <label className="field">Name<input name="channel-name" value={channel.name} onChange={(e) => onChange({ name: e.target.value })} /></label>
        <label className="field">Send
          <select name="events" value={channel.events} onChange={(e) => onChange({ events: e.target.value as Channel["events"] })}>
            <option value="problems">Problems only</option><option value="all">Everything</option>
          </select>
        </label>
        {/* Email: server, port, security, username, password (blank = unchanged), from, to. Others: the webhook URL */}
        {channel.type === "email" ? (
          <>
            {/* The SMTP fields */}
            <label className="field">SMTP server<input name="smtp-host" value={config.host ?? ""} onChange={(e) => setConfig("host", e.target.value)} placeholder="smtp.example.com" /></label>
            <label className="field">Port<input name="smtp-port" type="number" value={config.port ?? 587} onChange={(e) => setConfig("port", Number(e.target.value))} /></label>
            <label className="field">Security
              <select name="smtp-security" value={config.security ?? "starttls"} onChange={(e) => setConfig("security", e.target.value)}>
                <option value="starttls">STARTTLS (587)</option><option value="tls">TLS (465)</option>
              </select>
            </label>
            <label className="field">Username<input name="smtp-username" value={config.username ?? ""} onChange={(e) => setConfig("username", e.target.value)} autoComplete="off" /></label>
            <label className="field">Password<input name="smtp-password" type="password" value={config.password ?? ""} onChange={(e) => setConfig("password", e.target.value)} autoComplete="new-password" placeholder={channel.id ? "unchanged" : ""} /></label>
            <label className="field">From<input name="smtp-from" value={config.from ?? ""} onChange={(e) => setConfig("from", e.target.value)} placeholder="valor@example.com" /></label>
            <label className="field">To <span className="hint">comma-separated</span>
              <input name="smtp-to" value={(config.to ?? []).join(", ")} onChange={(e) => setConfig("to", e.target.value.split(",").map((address) => address.trim()).filter(Boolean))} />
            </label>
          </>
        ) : (
          <label className="field" style={{ gridColumn: "1 / -1" }}>Webhook URL
            <input name="webhook-url" value={config.url ?? ""} onChange={(e) => setConfig("url", e.target.value)} placeholder="https://…" autoComplete="off" />
          </label>
        )}
      </div>
    </div>
  );
}

// ---- Directory (LDAP) ------------------------------------------------------------------------------------------

// Ldap: every directory setting (see the server's ldapauth.py) OwO
interface Ldap {
  enabled: boolean; url: string; starttls: boolean; ca_pem: string; bind_dn: string; bind_password: string;
  user_base: string; user_filter: string; attr_display_name: string; attr_email: string;
  group_admin: string; group_operator: string; group_viewer: string; nested_groups: boolean; timeout: number;
}

// DirectorySettings: the LDAP form ^w^
function DirectorySettings() {
  const [ldap, setLdap] = useState<Ldap | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [testUser, setTestUser] = useState("");
  useEffect(() => { get<Ldap>("/api/settings/ldap").then(setLdap).catch(setErr); }, []);
  if (!ldap) return err ? <ErrorBox error={err} /> : <Loading />;
  // set(): change one setting; save(): PUT it all; test(): try the SAVED settings (optionally looking a user up) UwU
  const set = (key: keyof Ldap, value: any) => setLdap({ ...ldap, [key]: value });
  const save = async () => {
    setErr(null); setMsg(null);
    try { setLdap(await put<Ldap>("/api/settings/ldap", ldap)); setMsg({ ok: true, text: "Saved." }); } catch (e) { setErr(e as ApiError); }
  };
  const test = async () => {
    setErr(null); setMsg({ ok: true, text: "Testing…" });
    try {
      const result = await post<{ ok: boolean; message: string }>("/api/settings/ldap/test", { username: testUser });
      setMsg({ ok: result.ok, text: result.message });
    } catch (e) { setErr(e as ApiError); setMsg(null); }
  };
  // text(): a labelled text field for one setting >w<
  const text = (key: keyof Ldap, label: string, hint?: string, placeholder?: string) => (
    <label className="field">{label}{hint && <span className="hint">{hint}</span>}
      <input name={`ldap-${key}`} value={String(ldap[key] ?? "")} onChange={(e) => set(key, e.target.value)} placeholder={placeholder} autoComplete="off" />
    </label>
  );
  // The card with Save in its header OwO
  return (
    <Card title="Sign in with LDAP or Active Directory" actions={<button className="btn primary" onClick={save}>Save</button>}>
      <div className="stack">
        {/* On/off and a note: directory users still use VALOR's MFA, and their role comes from their groups */}
        <label className="checkbox"><input name="ldap-enabled" type="checkbox" checked={ldap.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Allow directory accounts to sign in</label>
        <div className="muted small">Directory users still set up VALOR's two-factor authentication. Their role comes from the groups below (checked at every sign-in).</div>
        {/* Server, StartTLS, service account, search base and filter, one group per role, nested groups */}
        <div className="grid cols-2">
          {text("url", "Server URL", "ldaps:// (or ldap:// with StartTLS)", "ldaps://dc1.example.com")}
          <label className="checkbox" style={{ alignSelf: "end" }}><input name="ldap-starttls" type="checkbox" checked={ldap.starttls} onChange={(e) => set("starttls", e.target.checked)} /> Use StartTLS (ldap:// only)</label>
          {text("bind_dn", "Service account DN", undefined, "CN=valor-svc,OU=Service,DC=example,DC=com")}
          <label className="field">Service account password
            <input name="ldap-bind-password" type="password" value={ldap.bind_password} onChange={(e) => set("bind_password", e.target.value)} autoComplete="new-password" />
          </label>
          {text("user_base", "Search base for users", undefined, "OU=People,DC=example,DC=com")}
          {text("user_filter", "User filter", "{username} is replaced (escaped)")}
          {text("group_admin", "Group for admins (DN)")}
          {text("group_operator", "Group for operators (DN)")}
          {text("group_viewer", "Group for viewers (DN)")}
          <label className="checkbox" style={{ alignSelf: "end" }}><input name="ldap-nested-groups" type="checkbox" checked={ldap.nested_groups} onChange={(e) => set("nested_groups", e.target.checked)} /> Include nested groups (Active Directory)</label>
        </div>
        {/* The directory's CA certificate (empty = the system's CAs) */}
        <label className="field">CA certificate (PEM) <span className="hint">leave empty to trust the system CAs</span>
          <textarea name="ldap-ca-pem" className="mono" rows={5} value={ldap.ca_pem} onChange={(e) => set("ca_pem", e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
        </label>
        {/* Test (with an optional username), the result, and errors */}
        <div className="row">
          <input name="ldap-test-user" style={{ maxWidth: 240 }} placeholder="Optional: a username to look up" value={testUser} onChange={(e) => setTestUser(e.target.value)} />
          <button className="btn" onClick={test}>Test connection</button>
        </div>
        {msg && <div className={`alert ${msg.ok ? "info" : "error"}`}>{msg.text}</div>}
        <ErrorBox error={err} />
      </div>
    </Card>
  );
}

// ---- AI providers ----------------------------------------------------------------------------------------------

// AiKind / AiProvider / Ai / AiUsage: the AI settings' shapes ^w^
type AiKind = "openai" | "openrouter";
interface AiProvider {
  id?: string;               // none until saved
  name: string;
  provider: AiKind;
  model: string;
  base_url: string;
  api_key: string;
  max_tokens: number;
  timeout: number;
  default: boolean;
}
interface Ai {
  providers: AiProvider[];
  daily_tokens_per_user: number;
  fix_failed_builds: boolean;
  fix_provider: string;
  fix_mode: "auto" | "fix" | "prepare";
  kinds?: AiKind[];          // sent by the API, never saved back
}
interface AiUsage { users: { username: string; requests: number; input: number; output: number; today: number }[] }

// AI_KIND: human names for the provider kinds; AI_NAME: default names for new providers UwU
const AI_KIND: Record<AiKind, string> = {
  openrouter: "OpenRouter (hosted models, one key)",
  openai: "OpenAI-compatible server (e.g. LM Studio)",
};
const AI_NAME: Record<AiKind, string> = { openrouter: "OpenRouter", openai: "Local model" };

// AiSettings: the AI providers and the failed-build assistant >w<
function AiSettings() {
  const [ai, setAi] = useState<Ai | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  // Token usage per user (for the table at the bottom) OwO
  const usage = useApi<AiUsage>("/api/settings/ai/usage");
  useEffect(() => { get<Ai>("/api/settings/ai").then(setAi).catch(setErr); }, []);
  if (!ai) return err ? <ErrorBox error={err} /> : <Loading />;
  // update(): change one provider ^w^
  const update = (i: number, changes: Partial<AiProvider>) =>
    setAi({ ...ai, providers: ai.providers.map((provider, j) => (i === j ? { ...provider, ...changes } : provider)) });
  // add(): a new provider with a unique name; the first one becomes the default UwU
  const add = (kind: AiKind) => {
    const taken = new Set(ai.providers.map((provider) => provider.name.toLowerCase()));
    let name = AI_NAME[kind];
    for (let n = 2; taken.has(name.toLowerCase()); n++) name = `${AI_NAME[kind]} ${n}`;
    setAi({ ...ai, providers: [...ai.providers, {
      name, provider: kind, model: "", base_url: "", api_key: "",
      max_tokens: 32768, timeout: 300, default: !ai.providers.length,
    }] });
  };
  // save(): PUT everything except `kinds` (sent by the server, never saved back) >w<
  const save = async () => {
    setBusy(true); setErr(null); setMsg(null);
    const { kinds, ...body } = ai;
    try { setAi({ ...ai, ...(await put<Ai>("/api/settings/ai", body)) }); setMsg({ ok: true, text: "Saved." }); }
    catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  };
  // test(): ask a SAVED provider to answer, and report the time, the reply, the context window and any advice OwO
  const test = async (provider: AiProvider) => {
    if (!provider.id) { setMsg({ ok: false, text: "Save first, then test." }); return; }
    setErr(null); setMsg({ ok: true, text: `Asking ${provider.name}…` });
    try {
      const result = await post<{ seconds: number; model: string; reply: string; context_length?: number | null;
        prompt_tokens: number; advice: string }>("/api/settings/ai/test", { id: provider.id });
      const context = result.context_length ? ` Context window: ${result.context_length.toLocaleString()} tokens;` : "";
      setMsg({ ok: !result.advice, text: `${provider.name} (${result.model}) answered in ${result.seconds} s: "${result.reply}".${context} VALOR's prompt: about `
        + `${result.prompt_tokens.toLocaleString()} tokens.${result.advice ? " " + result.advice : ""}` });
    } catch (e) { setErr(e as ApiError); setMsg(null); }
  };
  // The page ^w^
  return (
    <>
      {/* The providers card, with "Add a provider…" and Save in its header */}
      <Card title="AI providers for the chats" actions={
        <div className="row">
          <select name="add-provider" defaultValue="" onChange={(e) => { if (e.target.value) add(e.target.value as AiKind); e.target.value = ""; }} style={{ width: 220 }} aria-label="Add a provider">
            <option value="">Add a provider…</option>
            {(Object.keys(AI_KIND) as AiKind[]).map((kind) => <option key={kind} value={kind}>{AI_KIND[kind]}</option>)}
          </select>
          <button className="btn primary" onClick={save} disabled={busy}>Save</button>
        </div>
      }>
        <div className="stack">
          {/* What the chats send to the provider (no passwords or keys), and that keys are encrypted */}
          <div className="muted small">
            The chat builder and the range chats send the conversation, VALOR's spec reference and the cluster's free
            VLANs/networks to the provider (range chats in Code mode also send command output) - no passwords or keys.
            Specs are validated, and nothing is built until someone plans and approves it. Every chat uses the default
            provider unless someone picks another one for it. API keys are stored encrypted and never shown again.
          </div>
          {/* One card per provider (default radio, test, remove) */}
          {!ai.providers.length && <div className="empty">No provider yet: the chats are off.</div>}
          {ai.providers.map((provider, i) => (
            <ProviderCard key={provider.id ?? `new-${i}`} provider={provider} onChange={(changes) => update(i, changes)}
              onDefault={() => setAi({ ...ai, providers: ai.providers.map((other, j) => ({ ...other, default: i === j })) })}
              onTest={() => test(provider)} onRemove={() => setAi({ ...ai, providers: ai.providers.filter((_, j) => j !== i) })} />
          ))}
          {/* The daily token budget per user */}
          <label className="field" style={{ maxWidth: 320 }}>Daily token budget per user <span className="hint">all providers together; 0 = unlimited</span>
            <input name="daily-tokens-per-user" type="number" min={0} value={ai.daily_tokens_per_user} onChange={(e) => setAi({ ...ai, daily_tokens_per_user: Number(e.target.value) })} />
          </label>
          {/* The failed-build assistant: on/off, how far it goes, and which provider gets failed builds */}
          <label className="checkbox">
            <input name="fix-failed-builds" type="checkbox" checked={ai.fix_failed_builds} onChange={(e) => setAi({ ...ai, fix_failed_builds: e.target.checked })} />
            When a build fails, the assistant finds and fixes the cause in a range chat of the person who started it
            (builds from the CLI: the first administrator's)
          </label>
          {ai.fix_failed_builds && (
            <label className="field" style={{ maxWidth: 420 }}>How far it goes
              <select name="fix-mode" value={ai.fix_mode} onChange={(e) => setAi({ ...ai, fix_mode: e.target.value as Ai["fix_mode"] })}>
                <option value="auto">Fix and rebuild on its own (3 rounds at most; never replaces or removes a VM)</option>
                <option value="fix">Fix on its own; the person rebuilds</option>
                <option value="prepare">Prepare the fix; the person approves each change</option>
              </select>
            </label>
          )}
          {ai.fix_failed_builds && (
            <label className="field" style={{ maxWidth: 420 }}>Failed builds go to <span className="hint">then the other providers if it cannot answer</span>
              <select name="fix-provider" value={ai.fix_provider} onChange={(e) => setAi({ ...ai, fix_provider: e.target.value })}>
                <option value="">the default provider</option>
                {ai.providers.filter((provider) => provider.id).map((provider) => <option key={provider.id} value={provider.id}>{provider.name} - {provider.model}</option>)}
              </select>
            </label>
          )}
          {/* Messages and errors */}
          {msg && <div className={`alert ${msg.ok ? "info" : "error"}`}>{msg.text}</div>}
          <ErrorBox error={err} />
        </div>
      </Card>
      {/* Usage over 30 days, per user */}
      <Card title="Usage (last 30 days)">
        {!usage.data?.users.length ? <div className="empty">No requests yet.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>User</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>Last 24 h</th></tr></thead>
              <tbody>
                {usage.data.users.map((user) => (
                  <tr key={user.username}><td>{user.username}</td><td>{user.requests}</td><td>{user.input}</td><td>{user.output}</td><td>{user.today}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/** One AI provider: its model, where it runs (a local server's URL), its key and its limits. */
// ProviderCard: one provider: name, model, base URL (local servers), API key (blank = unchanged), limits UwU
function ProviderCard({ provider, onChange, onDefault, onTest, onRemove }: {
  provider: AiProvider; onChange: (changes: Partial<AiProvider>) => void;
  onDefault: () => void; onTest: () => void; onRemove: () => void;
}) {
  const hosted = provider.provider === "openrouter";
  return (
    <div className="card" style={{ padding: 14 }}>
      {/* Header: kind, default, Test, Remove */}
      <div className="row" style={{ marginBottom: 10 }}>
        <b>{AI_KIND[provider.provider]}</b>
        <span className="grow" />
        <label className="checkbox"><input type="radio" name="ai-default" checked={provider.default} onChange={onDefault} /> default</label>
        <button className="btn small" onClick={onTest}>Test</button>
        <button className="btn small danger" onClick={onRemove}>Remove</button>
      </div>
      {/* The fields, with hints that differ for hosted and local models */}
      <div className="grid cols-2">
        <label className="field">Name<input name="provider-name" value={provider.name} onChange={(e) => onChange({ name: e.target.value })} /></label>
        <label className="field">Model {hosted && <span className="hint">its id on openrouter.ai/models; Code mode needs tool support</span>}
          <input name="model" value={provider.model} onChange={(e) => onChange({ model: e.target.value })}
            placeholder={hosted ? "qwen/qwen3.8-27b" : "llama-3.3-70b"} />
        </label>
        {provider.provider === "openai" && (
          <label className="field">Base URL <span className="hint">ending in /v1; http:// only to a private IP</span>
            <input name="base-url" value={provider.base_url} onChange={(e) => onChange({ base_url: e.target.value })} placeholder="http://192.168.1.50:1234/v1" />
          </label>
        )}
        <label className="field">API key {provider.provider === "openai" && <span className="hint">optional for local servers</span>}
          <input name="api-key" type="password" value={provider.api_key} onChange={(e) => onChange({ api_key: e.target.value })} autoComplete="new-password" />
        </label>
        <label className="field">Max tokens per answer <span className="hint">{hosted
          ? "a ceiling: only the tokens the model writes are billed, but OpenRouter needs credits for the whole ceiling"
          : "a ceiling, not a cost: only the tokens the model writes count. Local servers with a small context window may need less."}</span>
          <input name="max-tokens" type="number" min={1024} max={128000} value={provider.max_tokens} onChange={(e) => onChange({ max_tokens: Number(e.target.value) })} />
        </label>
        <label className="field">Silence timeout (seconds) <span className="hint">how long the model may send nothing (loading, long thinking) before VALOR gives up</span>
          <input name="timeout" type="number" min={30} max={900} value={provider.timeout} onChange={(e) => onChange({ timeout: Number(e.target.value) })} />
        </label>
      </div>
    </div>
  );
}

// ---- Certificate and version -----------------------------------------------------------------------------------

// About: the web certificate (names, issuer, expiry, the CA download) and VALOR's version >w<
function About() {
  const { data } = useApi<any>("/api/status");
  const cert = data?.checks?.find((check: any) => check.name === "Certificate");
  return (
    <div className="grid cols-2">
      {/* The certificate card */}
      <Card title="Web certificate">
        {!cert ? <div className="muted">No certificate information.</div> : (
          <dl className="kv">
            <dt>Names</dt><dd className="mono">{(cert.names ?? []).join(", ")}</dd>
            <dt>Issuer</dt><dd className="mono">{cert.issuer}</dd>
            <dt>Expires</dt><dd>{new Date(cert.expires).toLocaleDateString()} ({cert.days_left} days)</dd>
          </dl>
        )}
        {/* How to trust VALOR's own CA */}
        <hr />
        <div className="small">
          If VALOR uses its own certificate authority, <a href="/ca.crt" download>download the CA certificate</a> and add it
          to your browser's or operating system's trusted roots. The certificate is renewed automatically.
        </div>
      </Card>
      {/* Version, instance, VALOR VM, and how to change the installation */}
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
