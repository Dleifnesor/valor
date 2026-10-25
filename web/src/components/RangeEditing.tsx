import { FormEvent, useEffect, useMemo, useState } from "react";
import { ApiError, del, get, post, put } from "../api";
import { useApi, when } from "../hooks";
import { Topology, TopoNode } from "../types";
import { ErrorBox, Loading, Modal } from "./ui";
import { TopologyMap } from "./Topology";
import { PlanModal, PlanResponse } from "./PlanModal";
import { AddSegmentModal, EditSegmentModal, RuleModal, rulesOf } from "./SegmentEditing";
import { BuilderInfo, ChatLog, Mode, PendingApproval, ProviderPick, useChat } from "./ChatFeed";

// Editing a built range on its map. Map controls and the chat panel both change the range's draft; the draft is
// drawn in the plan colors and built only through "Review plan" -> approve.

interface CatalogRole { name: string; description: string; ports?: number[]; families?: string[]; params: Record<string, { default?: any; description?: string; required?: boolean }> }
interface Catalog { default_os: string; os: { name: string; family?: string; description: string; template: boolean }[]; roles: CatalogRole[] }
interface Draft { yaml: string; valid: boolean; error?: string; meta: { by?: string; source?: string; updated?: string }; summary?: Record<string, number>; diff?: string }
interface View { draft: Draft | null; topology: Topology }
interface Op { op: string; [k: string]: any }
interface ChatSummary { id: number; title: string; updated: number; status: string }

const SOURCE: Record<string, string> = { map: "map", yaml: "YAML", "chat:plan": "chat (plan)", "chat:code": "chat (code)" };

export function RangeMapTab({ name, canOperate, onConsole, chatId }: { name: string; canOperate: boolean; onConsole?: (host: string) => void; chatId?: number }) {
  const [view, setView] = useState<View | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [modal, setModal] = useState<null | { kind: "addvm"; segment?: string } | { kind: "service" | "edit"; host: TopoNode }
    | { kind: "yaml" } | { kind: "addseg" } | { kind: "editseg"; seg: TopoNode } | { kind: "rule"; from?: string }>(null);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [planning, setPlanning] = useState(false);
  const [chatOpen, setChatOpen] = useState(() => { if (chatId) return true; try { return localStorage.getItem("valor-range-chat") === "1"; } catch { return false; } });
  const catalog = useApi<Catalog>(canOperate ? "/api/catalog" : null);

  const load = () => get<View>(`/api/ranges/${name}/draft`).then(setView).catch(setErr);
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, [name]);
  useEffect(() => { try { localStorage.setItem("valor-range-chat", chatOpen ? "1" : "0"); } catch { /* not kept */ } }, [chatOpen]);

  const edit = async (ops: Op[]) => {
    setErr(null);
    try { setView(await post<View>(`/api/ranges/${name}/draft/ops`, { ops })); return true; }
    catch (e) { setErr(e as ApiError); return false; }
  };
  const reviewPlan = async () => {
    setPlanning(true); setErr(null);
    try { setPlan(await post<PlanResponse>(`/api/ranges/${name}/plan`, { draft: true })); }
    catch (e) { setErr(e as ApiError); }
    finally { setPlanning(false); }
  };
  const discard = async () => {
    try { setView(await del<View>(`/api/ranges/${name}/draft`)); } catch (e) { setErr(e as ApiError); }
  };

  if (!view) return err ? <div className="card-body"><ErrorBox error={err} /></div> : <Loading />;
  const segments = view.topology.nodes.filter((n) => n.kind === "segment").map((n) => n.label);
  const d = view.draft;
  const vpn = view.topology.nodes.find((n) => n.kind === "vpn");
  const vpnReaches = (seg: string) => !!vpn?.reach?.includes(seg);
  const removeSegment = (seg: TopoNode, close: () => void) => {
    const inside = view.topology.nodes.filter((n) => n.parent === seg.id && n.change !== "remove").map((n) => n.label);
    if (inside.length) {
      if (!window.confirm(`${seg.label} has ${inside.length} VM(s): ${inside.join(", ")}. Remove the segment AND these VMs? They are deleted when the plan is approved.`)) return;
    } else if (!window.confirm(`Remove segment ${seg.label}? The router is rebuilt when the plan is approved.`)) return;
    edit([{ op: "remove_segment", name: seg.label, with_hosts: inside.length > 0 }]);
    close();
  };

  const overlay = canOperate ? (
    <div className="map-tools">
      <button className="btn small" onClick={() => setModal({ kind: "addvm" })}>+ Add VM</button>
      <button className="btn small" onClick={() => setModal({ kind: "addseg" })}>+ Add segment</button>
      <button className="btn small" onClick={() => setModal({ kind: "rule" })}>+ Allow traffic</button>
      <button className={`btn small${chatOpen ? " primary" : ""}`} onClick={() => setChatOpen(!chatOpen)} aria-pressed={chatOpen}>Chat</button>
    </div>
  ) : null;

  const nodeActions = (n: TopoNode, close: () => void) => {
    if (!canOperate) return null;
    if (n.kind === "segment") {
      const rules = rulesOf(view.topology, n.label);
      return (
        <div className="node-actions">
          <div className="small muted" style={{ marginBottom: 4 }}>Traffic rules</div>
          {rules.length === 0 && <div className="small muted">None: blocked except inside the segment{n.internet ? " and to the internet" : ""}.</div>}
          {rules.map((r) => (
            <div className="rule-row" key={r.edge.id}>
              <span className="mono small">{r.from} → {r.to}</span><span className="badge">{r.edge.label}</span><span className="grow" />
              <button className="chip-x" aria-label="Remove rule" title="Remove rule"
                onClick={() => edit([{ op: "remove_rule", index: r.index, from: r.from, to: r.to }])}>×</button>
            </div>
          ))}
          <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
            <button className="btn small" onClick={() => { setModal({ kind: "addvm", segment: n.label }); close(); }}>+ Add VM here</button>
            <button className="btn small" onClick={() => { setModal({ kind: "rule", from: n.label }); close(); }}>+ Allow traffic</button>
            <button className="btn small" onClick={() => { setModal({ kind: "editseg", seg: n }); close(); }}>Edit segment</button>
            <button className="btn small danger" onClick={() => removeSegment(n, close)}>Remove</button>
          </div>
        </div>
      );
    }
    if (n.kind !== "host" || n.change === "remove") return null;
    const host = n.id.slice(5);
    return (
      <div className="node-actions">
        {!!n.roles?.length && (
          <div className="service-chips">
            {n.roles.map((r) => (
              <span className="service-chip" key={r}>{r}
                <button className="chip-x" title={`Remove ${r}`} aria-label={`Remove ${r}`} onClick={() => edit([{ op: "remove_role", host, role: r }])}>×</button>
              </span>
            ))}
          </div>
        )}
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <button className="btn small" onClick={() => { setModal({ kind: "service", host: n }); close(); }}>+ Add service</button>
          <button className="btn small" onClick={() => { setModal({ kind: "edit", host: n }); close(); }}>Edit VM</button>
          <button className="btn small danger" onClick={() => { if (window.confirm(`Remove ${host} from the range? It is deleted when the plan is approved.`)) { edit([{ op: "remove_host", name: host }]); close(); } }}>Remove VM</button>
        </div>
      </div>
    );
  };

  return (
    <>
      {d && (
        <div className={`draft-bar${d.valid ? "" : " invalid"}`}>
          <b>Draft changes</b>
          {d.valid ? Object.entries(d.summary ?? {}).map(([k, v]) => <span key={k} className={`badge c-${k}`}>{v} {k}</span>)
            : <span className="error-text">{d.error}</span>}
          <span className="muted small">from {SOURCE[d.meta.source ?? ""] ?? d.meta.source} by {d.meta.by} · {when(d.meta.updated)}</span>
          <span className="grow" />
          {canOperate && <>
            <button className="btn small" onClick={() => setModal({ kind: "yaml" })}>YAML</button>
            <button className="btn small" onClick={discard}>Discard</button>
            <button className="btn small primary" disabled={!d.valid || planning} onClick={reviewPlan}>
              {planning ? <><span className="spinner" /> Planning…</> : "Review plan"}
            </button>
          </>}
        </div>
      )}
      <ErrorBox error={err} />
      <div className={`map-layout${chatOpen ? " with-chat" : ""}`}>
        <div className="map-main">
          <TopologyMap topology={view.topology} tall onConsole={onConsole} overlay={overlay} nodeActions={nodeActions}
            edgeActions={canOperate ? (e, close) => {
              const index = Number(e.id.split(":")[1]);
              const from = e.source.replace(/^(host|seg):/, ""), to = e.target.replace(/^(host|seg):/, "");
              return <div className="node-actions"><button className="btn small danger"
                onClick={() => { edit([{ op: "remove_rule", index, from, to }]); close(); }}>Remove this rule</button></div>;
            } : undefined} />
        </div>
        {chatOpen && canOperate && (
          <RangeChat name={name} chatId={chatId} onView={(v) => setView(v)} onReview={reviewPlan} onClose={() => setChatOpen(false)} />
        )}
      </div>
      {modal?.kind === "addvm" && catalog.data && (
        <AddVmModal segments={segments} segment={modal.segment} catalog={catalog.data} onClose={() => setModal(null)}
          onSubmit={async (ops) => { if (await edit(ops)) setModal(null); }} />
      )}
      {modal?.kind === "service" && catalog.data && (
        <AddServiceModal host={modal.host} segments={segments} catalog={catalog.data} onClose={() => setModal(null)}
          onSubmit={async (ops) => { if (await edit(ops)) setModal(null); }} />
      )}
      {modal?.kind === "edit" && catalog.data && (
        <EditVmModal host={modal.host} catalog={catalog.data} onClose={() => setModal(null)}
          onSubmit={async (ops) => { if (await edit(ops)) setModal(null); }} />
      )}
      {modal?.kind === "addseg" && <AddSegmentModal name={name} topo={view.topology} hasVpn={!!vpn} onClose={() => setModal(null)}
        onSubmit={async (ops) => { if (await edit(ops)) setModal(null); }} />}
      {modal?.kind === "editseg" && <EditSegmentModal seg={modal.seg} hasVpn={!!vpn} vpnReaches={vpnReaches(modal.seg.label)}
        onClose={() => setModal(null)} onSubmit={async (ops) => { if (await edit(ops)) setModal(null); }} />}
      {modal?.kind === "rule" && <RuleModal topo={view.topology} from={modal.from} onClose={() => setModal(null)}
        onSubmit={async (ops) => { if (await edit(ops)) setModal(null); }} />}
      {modal?.kind === "yaml" && d && <YamlModal name={name} yaml={d.yaml} onClose={() => setModal(null)} onSaved={(v) => { setView(v); setModal(null); }} />}
      {plan && <PlanModal name={name} res={plan} draft onClose={() => { setPlan(null); load(); }} />}
    </>
  );
}

// ---------------------------------------------------------------------- forms

function familyOf(catalog: Catalog, os: string) {
  return catalog.os.find((o) => o.name === os)?.family ?? "debian";
}

function rolesFor(catalog: Catalog, os: string) {
  const fam = familyOf(catalog, os);
  return catalog.roles.filter((r) => (r.families ?? ["debian"]).includes(fam));
}

function RoleParams({ role, values, onChange }: { role: CatalogRole; values: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const entries = Object.entries(role.params ?? {});
  if (!entries.length) return null;
  return (
    <div className="params">
      {entries.map(([k, p]) => (
        <label className="field" key={k}>{role.name}: {k}{p.required ? " (required)" : ""}
          {p.description && <span className="hint">{p.description}</span>}
          <input name={`param-${k}`} value={values[k] ?? ""} placeholder={p.default !== undefined ? String(p.default) : ""} required={!!p.required}
            onChange={(e) => onChange({ ...values, [k]: e.target.value })} />
        </label>
      ))}
    </div>
  );
}

function AllowFrom({ segments, own, value, onChange }: { segments: string[]; own?: string; value: string[]; onChange: (v: string[]) => void }) {
  const others = segments.filter((s) => s !== own);
  if (!others.length) return null;
  return (
    <div className="field">Who may reach the new services <span className="hint">adds router rules for their ports; the VM's own segment always can</span>
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        {others.map((s) => (
          <label key={s} className="checkbox"><input name="allow-from" type="checkbox" checked={value.includes(s)}
            onChange={(e) => onChange(e.target.checked ? [...value, s] : value.filter((x) => x !== s))} /> {s}</label>
        ))}
      </div>
    </div>
  );
}

function roleOps(host: string, selected: string[], params: Record<string, Record<string, string>>, allow: string[]): Op[] {
  return selected.map((r) => ({
    op: "add_role", host, role: r, allow_from: allow,
    params: Object.fromEntries(Object.entries(params[r] ?? {}).filter(([, v]) => v !== "")),
  }));
}

function AddVmModal({ segments, segment, catalog, onClose, onSubmit }: {
  segments: string[]; segment?: string; catalog: Catalog; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const osList = catalog.os.filter((o) => o.template);
  const [name, setName] = useState("");
  const [seg, setSeg] = useState(segment ?? segments[0] ?? "");
  const [os, setOs] = useState(catalog.default_os);
  const [size, setSize] = useState({ cores: 1, memory: 1024, disk: 10 });
  const [roles, setRoles] = useState<string[]>([]);
  const [params, setParams] = useState<Record<string, Record<string, string>>>({});
  const [allow, setAllow] = useState<string[]>([]);
  const windows = familyOf(catalog, os) === "windows";
  const available = rolesFor(catalog, os);
  const valid = /^[a-z][a-z0-9-]{0,13}[a-z0-9]$/.test(name) && !!seg;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit([{ op: "add_host", name, segment: seg, os, ...size }, ...roleOps(name, roles.filter((r) => available.some((a) => a.name === r)), params, allow)]);
  };
  return (
    <Modal title="Add a VM" onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="addvm" disabled={!valid}>Add to the draft</button></>
    }>
      <form id="addvm" className="stack" onSubmit={submit}>
        <div className="grid cols-2">
          <label className="field">Name <span className="hint">2-15 lowercase letters, digits, dashes</span>
            <input name="vm-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} autoFocus required placeholder="web2" />
          </label>
          <label className="field">Segment
            <select name="segment" value={seg} onChange={(e) => setSeg(e.target.value)}>{segments.map((s) => <option key={s}>{s}</option>)}</select>
          </label>
          <label className="field">Operating system
            <select name="os" value={os} onChange={(e) => { setOs(e.target.value); setRoles([]); }}>
              {osList.map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
            </select>
          </label>
          <div className="size-row">
            <label className="field">vCPU<input name="cores" type="number" min={1} max={16} value={size.cores} onChange={(e) => setSize({ ...size, cores: Number(e.target.value) })} /></label>
            <label className="field">MiB<input name="memory" type="number" min={512} step={512} value={size.memory} onChange={(e) => setSize({ ...size, memory: Number(e.target.value) })} /></label>
            <label className="field">GiB<input name="disk" type="number" min={8} value={size.disk} onChange={(e) => setSize({ ...size, disk: Number(e.target.value) })} /></label>
          </div>
        </div>
        {windows && <div className="muted small">Windows gets at least the memory and disk its template needs, whatever you enter here.</div>}
        <div className="field">Services
          <div className="role-pick">
            {available.map((r) => (
              <label key={r.name} className="checkbox" title={r.description}>
                <input name="roles" type="checkbox" checked={roles.includes(r.name)} onChange={(e) => setRoles(e.target.checked ? [...roles, r.name] : roles.filter((x) => x !== r.name))} />
                <span><b>{r.name}</b> <span className="muted small">{r.description}</span></span>
              </label>
            ))}
          </div>
        </div>
        {roles.map((r) => { const role = available.find((a) => a.name === r); return role ? <RoleParams key={r} role={role} values={params[r] ?? {}} onChange={(v) => setParams({ ...params, [r]: v })} /> : null; })}
        {roles.length > 0 && <AllowFrom segments={segments} own={seg} value={allow} onChange={setAllow} />}
        <div className="muted small">The address is the segment's next free one (from .10). Nothing is built until you review and approve the plan.</div>
      </form>
    </Modal>
  );
}

function AddServiceModal({ host, segments, catalog, onClose, onSubmit }: {
  host: TopoNode; segments: string[]; catalog: Catalog; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const hostName = host.id.slice(5);
  const available = rolesFor(catalog, host.os ?? catalog.default_os).filter((r) => !(host.roles ?? []).includes(r.name));
  const [role, setRole] = useState(available[0]?.name ?? "");
  const [params, setParams] = useState<Record<string, string>>({});
  const [allow, setAllow] = useState<string[]>([]);
  const r = available.find((a) => a.name === role);
  const own = host.parent?.replace(/^seg:/, "");
  return (
    <Modal title={`Add a service to ${hostName}`} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!role} onClick={() => onSubmit(roleOps(hostName, [role], { [role]: params }, allow))}>Add to the draft</button></>
    }>
      {!available.length ? <div className="empty">No other service supports {host.os}.</div> : (
        <div className="stack">
          <label className="field">Service
            <select name="role" value={role} onChange={(e) => { setRole(e.target.value); setParams({}); }}>
              {available.map((a) => <option key={a.name} value={a.name}>{a.name} - {a.description}</option>)}
            </select>
          </label>
          {r && <RoleParams role={r} values={params} onChange={setParams} />}
          {r?.ports?.length ? <AllowFrom segments={segments} own={own} value={allow} onChange={setAllow} />
            : <div className="muted small">{role} listens on no network port.</div>}
        </div>
      )}
    </Modal>
  );
}

function EditVmModal({ host, catalog, onClose, onSubmit }: { host: TopoNode; catalog: Catalog; onClose: () => void; onSubmit: (ops: Op[]) => void }) {
  const hostName = host.id.slice(5);
  const [os, setOs] = useState(host.os ?? catalog.default_os);
  const [size, setSize] = useState({ cores: host.cores ?? 1, memory: host.memory ?? 1024, disk: host.disk ?? 10 });
  const osChanged = os !== host.os;
  return (
    <Modal title={`Edit ${hostName}`} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => onSubmit([{ op: "update_host", name: hostName, ...size, ...(osChanged ? { os } : {}) }])}>Add to the draft</button></>
    }>
      <div className="stack">
        <label className="field">Operating system
          <select name="os" value={os} onChange={(e) => setOs(e.target.value)}>
            {catalog.os.filter((o) => o.template || o.name === host.os).map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
          </select>
        </label>
        {osChanged && <div className="alert warn">A new OS means a new VM: its disk is replaced when the plan runs.</div>}
        <div className="size-row">
          <label className="field">vCPU<input name="cores" type="number" min={1} max={16} value={size.cores} onChange={(e) => setSize({ ...size, cores: Number(e.target.value) })} /></label>
          <label className="field">MiB<input name="memory" type="number" min={512} step={512} value={size.memory} onChange={(e) => setSize({ ...size, memory: Number(e.target.value) })} /></label>
          <label className="field">GiB<input name="disk" type="number" min={host.disk ?? 8} value={size.disk} onChange={(e) => setSize({ ...size, disk: Number(e.target.value) })} /></label>
        </div>
        <div className="muted small">More CPU or memory restarts the VM; disks can grow, never shrink.</div>
      </div>
    </Modal>
  );
}

function YamlModal({ name, yaml, onClose, onSaved }: { name: string; yaml: string; onClose: () => void; onSaved: (v: View) => void }) {
  const [text, setText] = useState(yaml);
  const [err, setErr] = useState<ApiError | null>(null);
  const save = async () => {
    try { onSaved(await put<View>(`/api/ranges/${name}/draft`, { yaml: text })); } catch (e) { setErr(e as ApiError); }
  };
  return (
    <Modal title={`Draft spec of ${name}`} onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save draft</button></>
    }>
      <textarea name="spec" className="mono" rows={24} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} style={{ width: "100%" }} />
      <ErrorBox error={err} />
    </Modal>
  );
}

// ---------------------------------------------------------------------- chat

const MODES: { mode: Mode; label: string; hint: string; placeholder: string }[] = [
  { mode: "question", label: "Question", hint: "Answers about this range; may look inside the VMs with read-only commands.", placeholder: "Who can reach the database? Is nginx running on web?" },
  { mode: "plan", label: "Plan", hint: "Proposes a change as a draft on the map, with VALOR's plan to approve.", placeholder: "Add a second web server behind the same policy" },
  { mode: "code", label: "Code", hint: "Works in the VMs (commands, files, services, power, snapshots) and records changes in the spec. Changes wait for your approval.", placeholder: "Install and configure PostgreSQL on db, reachable from web" },
];

function RangeChat({ name, chatId, onView, onReview, onClose }: {
  name: string; chatId?: number; onView: (v: View) => void; onReview: () => void; onClose: () => void;
}) {
  const info = useApi<BuilderInfo>("/api/builder");
  const list = useApi<{ chats: ChatSummary[] }>(`/api/chats?range=${encodeURIComponent(name)}`);
  const [current, setCurrent] = useState<number | null>(chatId ?? null);
  const [mode, setMode] = useState<Mode>("question");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [pick, setPick] = useState("");                  // the provider for a new conversation ("" = the default)
  const [err, setErr] = useState<ApiError | null>(null);
  const refreshMap = () => get<View>(`/api/ranges/${name}/draft`).then(onView).catch(() => undefined);
  const ctl = useChat(current, (m) => { if (m.meta.draft_changed) refreshMap(); });
  const def = useMemo(() => MODES.find((m) => m.mode === mode)!, [mode]);

  useEffect(() => { if (chatId) setCurrent(chatId); }, [chatId]);
  useEffect(() => {                                   // open the newest conversation about this range
    if (current === null && list.data?.chats.length) setCurrent(list.data.chats[0].id);
  }, [list.data]);

  const send = async (msg: string, as: Mode = mode) => {
    msg = msg.trim();
    if (!msg || sending || ctl.busy) return;
    setSending(true); setErr(null);
    try {
      if (!ctl.chat) {
        const c = await post<{ id: number }>("/api/chats", { kind: "range", range: name, ...(pick ? { provider: pick } : {}) });
        await post(`/api/chats/${c.id}/messages`, { message: msg, mode: as });
        setCurrent(c.id);
        list.reload();
      } else {
        await ctl.send(msg, as);
      }
      setText("");
    } catch (e) { setErr(e as ApiError); }
    finally { setSending(false); }
  };
  const chat = ctl.chat;
  const readonly = !!chat?.readonly;
  const lastMode = chat?.messages.filter((m) => m.role === "user").slice(-1)[0]?.mode ?? mode;

  return (
    <aside className="range-chat card" aria-label="Range chat">
      <div className="card-head">
        <h2>Chat</h2>
        <select name="conversation" className="chat-pick grow" value={current ?? ""} aria-label="Conversation"
          onChange={(e) => setCurrent(e.target.value ? Number(e.target.value) : null)}>
          <option value="">New conversation</option>
          {(list.data?.chats ?? []).map((c) => <option key={c.id} value={c.id}>{c.title || `Conversation ${c.id}`}</option>)}
        </select>
        <button className="btn ghost small" onClick={onClose} aria-label="Close chat">✕</button>
      </div>
      {info.data && !info.data.configured ? (
        <div className="card-body muted">The chat needs an AI provider: an administrator can connect one in Datacenter → AI providers.</div>
      ) : (
        <>
          <div className="seg-control" role="tablist">
            {MODES.map((m) => (
              <button key={m.mode} role="tab" aria-selected={mode === m.mode} className={mode === m.mode ? "active" : ""} onClick={() => setMode(m.mode)}>{m.label}</button>
            ))}
          </div>
          <div className="muted small" style={{ padding: "0 12px" }}>{def.hint}</div>
          {info.data && info.data.providers.length > 1 && (
            <label className="provider-row small">Model
              <ProviderPick info={info.data} value={chat ? chat.provider : pick} disabled={readonly || sending || ctl.busy}
                onChange={(id) => chat ? ctl.setProvider(id).catch((e) => setErr(e as ApiError)) : setPick(id)} />
            </label>
          )}
          {mode === "code" && chat && !readonly && (
            <label className="allow-all small">
              <input name="allow-all" type="checkbox" checked={chat.allow_all} onChange={(e) => {
                if (!e.target.checked || window.confirm("Let the assistant run commands and other changes in this chat without asking first?"))
                  ctl.setAllowAll(e.target.checked).catch((x) => setErr(x as ApiError));
              }} /> Run changes without asking (this conversation)
            </label>
          )}
          <div className="chat-log range">
            {chat ? <ChatLog chat={chat} ctl={ctl} onReview={onReview} onAnswer={(a) => send(a, lastMode)}
              empty={<div className="muted small">Ask about this range or describe a change. Conversations are saved (Datacenter → Chats).</div>} />
              : <div className="muted small">Ask about this range or describe a change. Conversations are saved (Datacenter → Chats).</div>}
          </div>
          <PendingApproval chat={chat} ctl={ctl} />
          <ErrorBox error={err ?? ctl.error} />
          {readonly ? <div className="card-body muted small">This is {chat?.owner}'s conversation (read-only).</div> : (
            <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
              <textarea name="message" rows={3} value={text} disabled={sending || ctl.busy} placeholder={ctl.busy ? "The assistant is working…" : def.placeholder}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
              <button className="btn primary" disabled={sending || ctl.busy || !text.trim()}>Send</button>
            </form>
          )}
        </>
      )}
    </aside>
  );
}
