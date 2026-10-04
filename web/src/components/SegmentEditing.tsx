import { FormEvent, useEffect, useState } from "react";
import { get } from "../api";
import { Topology, TopoEdge, TopoNode } from "../types";
import { Modal } from "./ui";

// Network segments and traffic rules on the map: presets, add / edit / remove segments, allow / remove flows.
// Everything becomes draft operations (see RangeEditing); the router is rebuilt when segments change.

export interface Op { op: string; [k: string]: any }
interface Rule { from: string; to: string; proto: "tcp" | "udp" | "icmp" | "any"; ports: (number | string)[]; description?: string }

const SELF = "\u0000self";       // the segment being added, until it has a name

interface Preset { id: string; label: string; name: string; internet: boolean; description: string; hint: string; rules: (others: string[]) => Rule[] }
const PRESETS: Preset[] = [
  { id: "dmz", label: "DMZ", name: "dmz", internet: true, description: "Public-facing services",
    hint: "internet egress; the other segments may reach it on 80/443",
    rules: (o) => o.map((s) => ({ from: s, to: SELF, proto: "tcp", ports: [80, 443], description: `${s} uses the DMZ's web services` })) },
  { id: "users", label: "Users / LAN", name: "users", internet: true, description: "Workstations",
    hint: "internet egress; reaches other segments only through rules you add", rules: () => [] },
  { id: "servers", label: "Servers", name: "servers", internet: false, description: "Internal servers",
    hint: "no internet; open services to it when you add them", rules: () => [] },
  { id: "mgmt", label: "Management", name: "mgmt", internet: false, description: "Administration hosts",
    hint: "no internet; may reach every segment over SSH (22) and RDP (3389)",
    rules: (o) => o.map((s) => ({ from: SELF, to: s, proto: "tcp", ports: [22, 3389], description: `administer ${s}` })) },
  { id: "attack", label: "Attacker lab", name: "attack", internet: true, description: "Attack machines",
    hint: "internet egress; may reach every other segment on any port",
    rules: (o) => o.map((s) => ({ from: SELF, to: s, proto: "any", ports: [], description: `attack ${s}` })) },
  { id: "isolated", label: "Isolated", name: "isolated", internet: false, description: "Isolated network",
    hint: "no internet and no rules in or out", rules: () => [] },
];

const NAME_RE = /^[a-z][a-z0-9-]{0,13}[a-z0-9]$/;

function freeName(base: string, taken: string[]) {
  if (!taken.includes(base)) return base;
  for (let i = 2; ; i++) if (!taken.includes(`${base}${i}`)) return `${base}${i}`;
}

export function endpoints(topo: Topology) {
  const segs = topo.nodes.filter((n) => n.kind === "segment" && n.change !== "remove").map((n) => n.label);
  const hosts = topo.nodes.filter((n) => n.kind === "host" && n.change !== "remove").map((n) => n.id.slice(5));
  return { segs, hosts };
}

const portsText = (r: Rule) => r.proto === "tcp" || r.proto === "udp" ? `${r.proto} ${r.ports.join(",")}` : r.proto;

function parsePorts(text: string): (number | string)[] {
  return text.split(/[\s,]+/).filter(Boolean).map((p) => (/^\d+$/.test(p) ? Number(p) : p));
}

function RuleRow({ rule, self, onRemove }: { rule: Rule; self: string; onRemove: () => void }) {
  const n = (x: string) => (x === SELF ? self || "(new segment)" : x);
  return (
    <div className="rule-row">
      <span className="mono">{n(rule.from)} → {n(rule.to)}</span>
      <span className="badge">{portsText(rule)}</span>
      <span className="muted small grow">{rule.description}</span>
      <button type="button" className="chip-x" aria-label="Remove rule" onClick={onRemove}>×</button>
    </div>
  );
}

function RuleFields({ options, value, onChange }: { options: string[]; value: Rule; onChange: (r: Rule) => void }) {
  return (
    <div className="rule-fields">
      <label className="field">From<select value={value.from} onChange={(e) => onChange({ ...value, from: e.target.value })}>
        {options.map((o) => <option key={o} value={o}>{o === SELF ? "(this segment)" : o}</option>)}</select></label>
      <label className="field">To<select value={value.to} onChange={(e) => onChange({ ...value, to: e.target.value })}>
        {options.map((o) => <option key={o} value={o}>{o === SELF ? "(this segment)" : o}</option>)}</select></label>
      <label className="field">Protocol<select value={value.proto} onChange={(e) => onChange({ ...value, proto: e.target.value as Rule["proto"] })}>
        <option value="tcp">tcp</option><option value="udp">udp</option><option value="icmp">icmp (ping)</option><option value="any">any</option></select></label>
      {(value.proto === "tcp" || value.proto === "udp") && (
        <label className="field">Ports<input value={value.ports.join(",")} placeholder="80,443 or 8000-8100"
          onChange={(e) => onChange({ ...value, ports: parsePorts(e.target.value) })} /></label>
      )}
    </div>
  );
}

const ruleOk = (r: Rule) => r.from && r.to && r.from !== r.to && (r.proto === "icmp" || r.proto === "any" || r.ports.length > 0);

export function AddSegmentModal({ name: range, topo, hasVpn, onClose, onSubmit }: {
  name: string; topo: Topology; hasVpn: boolean; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const { segs, hosts } = endpoints(topo);
  const [preset, setPreset] = useState<string>("");
  const [name, setName] = useState("");
  const [vlan, setVlan] = useState("");
  const [cidr, setCidr] = useState("");
  const [internet, setInternet] = useState(false);
  const [description, setDescription] = useState("");
  const [vpnReach, setVpnReach] = useState(true);
  const [rules, setRules] = useState<Rule[]>([]);
  const [draftRule, setDraftRule] = useState<Rule>({ from: SELF, to: segs[0] ?? "", proto: "tcp", ports: [] });
  const [range_, setRange] = useState<[number, number]>([100, 3999]);

  useEffect(() => {
    get<{ vlan: number; cidr: string; vlan_range: [number, number] }>(`/api/ranges/${range}/draft/suggest-segment`)
      .then((s) => { setVlan(String(s.vlan)); setCidr(s.cidr); setRange(s.vlan_range); }).catch(() => {});
  }, [range]);

  const pick = (p: Preset) => {
    setPreset(p.id);
    setName(freeName(p.name, [...segs, ...hosts]));
    setInternet(p.internet);
    setDescription(p.description);
    setRules(p.rules(segs));
    setVpnReach(p.id !== "isolated");
  };
  const options = [SELF, ...segs, ...hosts];
  const valid = NAME_RE.test(name) && !segs.includes(name) && !hosts.includes(name) && /^\d+$/.test(vlan) && /\/\d+$/.test(cidr);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const fix = (x: string) => (x === SELF ? name : x);
    onSubmit([
      { op: "add_segment", name, vlan: Number(vlan), cidr, internet, description, ...(hasVpn ? { vpn_reach: vpnReach } : {}) },
      ...rules.map((r) => ({ op: "add_rule", from: fix(r.from), to: fix(r.to), proto: r.proto, ports: r.ports, description: r.description ?? "" })),
    ]);
  };
  return (
    <Modal title="Add a network segment" onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="addseg" disabled={!valid}>Add to the draft</button></>
    }>
      <form id="addseg" className="stack" onSubmit={submit}>
        <div className="field">Start from a preset <span className="hint">fills in the settings and rules below; change anything</span>
          <div className="preset-row">
            {PRESETS.map((p) => (
              <button type="button" key={p.id} className={`preset${preset === p.id ? " active" : ""}`} onClick={() => pick(p)} title={p.hint}>
                <b>{p.label}</b><span>{p.hint}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="grid cols-2">
          <label className="field">Name <span className="hint">2-15 lowercase letters, digits, dashes</span>
            <input value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="servers" required />
          </label>
          <label className="field">Description<input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          <label className="field">VLAN <span className="hint">a free one is suggested ({range_[0]}-{range_[1]})</span>
            <input value={vlan} onChange={(e) => setVlan(e.target.value.replace(/\D/g, ""))} inputMode="numeric" />
          </label>
          <label className="field">Network <span className="hint">private, /16 to /29; a free /24 is suggested</span>
            <input className="mono" value={cidr} onChange={(e) => setCidr(e.target.value.trim())} placeholder="10.100.5.0/24" />
          </label>
        </div>
        <label className="checkbox"><input type="checkbox" checked={internet} onChange={(e) => setInternet(e.target.checked)} />
          Internet egress (public addresses only, never your LAN or the cluster)</label>
        {hasVpn && <label className="checkbox"><input type="checkbox" checked={vpnReach} onChange={(e) => setVpnReach(e.target.checked)} />
          WireGuard peers may reach this segment</label>}
        <div className="field">Traffic rules <span className="hint">everything else between segments stays blocked</span>
          {rules.length === 0 && <div className="muted small">No rules: hosts here reach only their own segment{internet ? " and the internet" : ""}.</div>}
          {rules.map((r, i) => <RuleRow key={i} rule={r} self={name} onRemove={() => setRules(rules.filter((_, j) => j !== i))} />)}
          <RuleFields options={options} value={draftRule} onChange={setDraftRule} />
          <div><button type="button" className="btn small" disabled={!ruleOk(draftRule)} onClick={() => setRules([...rules, draftRule])}>+ Add rule</button></div>
        </div>
        <div className="alert warn small">Adding a segment rebuilds the range router (it gets a NIC per segment): routing pauses
          for about a minute. The router keeps its LAN address, so VPN configs stay valid.</div>
      </form>
    </Modal>
  );
}

export function EditSegmentModal({ seg, hasVpn, vpnReaches, onClose, onSubmit }: {
  seg: TopoNode; hasVpn: boolean; vpnReaches: boolean; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const [description, setDescription] = useState(seg.description ?? "");
  const [internet, setInternet] = useState(!!seg.internet);
  const [vlan, setVlan] = useState(String(seg.vlan ?? ""));
  const [cidr, setCidr] = useState(seg.cidr ?? "");
  const [vpnReach, setVpnReach] = useState(vpnReaches);
  const netChanged = vlan !== String(seg.vlan) || cidr !== seg.cidr;
  const submit = () => onSubmit([{
    op: "update_segment", name: seg.label, description, internet,
    ...(vlan !== String(seg.vlan) ? { vlan: Number(vlan) } : {}), ...(cidr !== seg.cidr ? { cidr } : {}),
    ...(hasVpn && vpnReach !== vpnReaches ? { vpn_reach: vpnReach } : {}),
  }]);
  return (
    <Modal title={`Edit segment ${seg.label}`} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={submit}>Add to the draft</button></>
    }>
      <div className="stack">
        <label className="field">Description<input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label className="checkbox"><input type="checkbox" checked={internet} onChange={(e) => setInternet(e.target.checked)} /> Internet egress</label>
        {hasVpn && <label className="checkbox"><input type="checkbox" checked={vpnReach} onChange={(e) => setVpnReach(e.target.checked)} /> WireGuard peers may reach it</label>}
        <div className="grid cols-2">
          <label className="field">VLAN<input value={vlan} onChange={(e) => setVlan(e.target.value.replace(/\D/g, ""))} /></label>
          <label className="field">Network<input className="mono" value={cidr} onChange={(e) => setCidr(e.target.value.trim())} /></label>
        </div>
        {netChanged && <div className="alert warn small">A new VLAN or network rebuilds the router and every VM in this segment
          (new addresses keep their last part, e.g. .10 stays .10); their disks are replaced.</div>}
      </div>
    </Modal>
  );
}

export function RuleModal({ topo, from, onClose, onSubmit }: { topo: Topology; from?: string; onClose: () => void; onSubmit: (ops: Op[]) => void }) {
  const { segs, hosts } = endpoints(topo);
  const options = [...segs, ...hosts];
  const [rule, setRule] = useState<Rule>({ from: from ?? options[0] ?? "", to: options.find((o) => o !== from) ?? "", proto: "tcp", ports: [] });
  const [description, setDescription] = useState("");
  return (
    <Modal title="Allow traffic" onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!ruleOk(rule)} onClick={() => onSubmit([{ op: "add_rule", from: rule.from, to: rule.to, proto: rule.proto, ports: rule.ports, description }])}>Add to the draft</button></>
    }>
      <div className="stack">
        <RuleFields options={options} value={rule} onChange={setRule} />
        <label className="field">Why <span className="hint">optional, shown on the map</span><input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <div className="muted small">Traffic between two hosts in the same segment never passes the router, so it needs no rule.</div>
      </div>
    </Modal>
  );
}

/** Rules that touch a segment or one of its hosts, with their index in the spec's policy. */
export function rulesOf(topo: Topology, seg: string): { edge: TopoEdge; index: number; from: string; to: string }[] {
  const members = new Set([`seg:${seg}`, ...topo.nodes.filter((n) => n.parent === `seg:${seg}`).map((n) => n.id)]);
  return topo.edges.filter((e) => e.kind === "policy" && (members.has(e.source) || members.has(e.target)))
    .map((e) => ({ edge: e, index: Number(e.id.split(":")[1]), from: e.source.replace(/^(host|seg):/, ""), to: e.target.replace(/^(host|seg):/, "") }));
}
