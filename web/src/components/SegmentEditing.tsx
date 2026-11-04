// *draws new network boxes* OwO web/src/components/SegmentEditing.tsx: the map's forms for SEGMENTS and TRAFFIC RULES! ^w^
// Add a segment (from a preset), edit one, allow traffic. Like the VM forms they only produce edits for the range's
// DRAFT; the router is rebuilt when the segments change *wags tail*
import { FormEvent, useEffect, useState } from "react";
// get, the map types, Modal, and the Op type UwU
import { get } from "../api";
import { Topology, TopoEdge, TopoNode } from "../types";
import { Modal } from "./ui";
import type { Op } from "./VmForms";

// The map's forms for network segments and traffic rules: add a segment (from a preset), edit one, allow traffic.
// Like the VM forms, they only produce map edits for the range's draft (see RangeEditing); the router is rebuilt
// when the segments change.

/** A traffic rule as the forms edit it (the spec's policy entry). */
// Rule: a traffic rule as the forms edit it >w<
interface Rule { from: string; to: string; proto: "tcp" | "udp" | "icmp" | "any"; ports: (number | string)[]; description?: string }

// Stands for the segment being added in its rules, until it has a name (no real name can contain a NUL).
// SELF: stands for the new segment in its rules until it has a name (a NUL character, which no real name contains) OwO
const SELF = "\u0000self";

/** A starting point for a new segment: its settings and the rules it needs towards the existing segments. */
// Preset: a ready-made new segment: its settings and the rules it needs towards the existing segments ^w^
interface Preset { id: string; label: string; name: string; internet: boolean; description: string; hint: string; rules: (others: string[]) => Rule[] }
const PRESETS: Preset[] = [
  // DMZ: internet; the others may reach it on 80/443 UwU
  { id: "dmz", label: "DMZ", name: "dmz", internet: true, description: "Public-facing services",
    hint: "internet egress; the other segments may reach it on 80/443",
    rules: (others) => others.map((segment) => ({ from: segment, to: SELF, proto: "tcp", ports: [80, 443], description: `${segment} uses the DMZ's web services` })) },
  // Users/LAN: internet, no rules >w<
  { id: "users", label: "Users / LAN", name: "users", internet: true, description: "Workstations",
    hint: "internet egress; reaches other segments only through rules you add", rules: () => [] },
  // Servers: no internet, no rules (open services later) OwO
  { id: "servers", label: "Servers", name: "servers", internet: false, description: "Internal servers",
    hint: "no internet; open services to it when you add them", rules: () => [] },
  // Management: no internet; may reach every segment on SSH and RDP ^w^
  { id: "mgmt", label: "Management", name: "mgmt", internet: false, description: "Administration hosts",
    hint: "no internet; may reach every segment over SSH (22) and RDP (3389)",
    rules: (others) => others.map((segment) => ({ from: SELF, to: segment, proto: "tcp", ports: [22, 3389], description: `administer ${segment}` })) },
  // Attacker lab: internet; may reach every segment on anything UwU
  { id: "attack", label: "Attacker lab", name: "attack", internet: true, description: "Attack machines",
    hint: "internet egress; may reach every other segment on any port",
    rules: (others) => others.map((segment) => ({ from: SELF, to: segment, proto: "any", ports: [], description: `attack ${segment}` })) },
  // Isolated: nothing in or out >w<
  { id: "isolated", label: "Isolated", name: "isolated", internet: false, description: "Isolated network",
    hint: "no internet and no rules in or out", rules: () => [] },
];

// NAME_RE: valid names (2-15 chars) OwO
const NAME_RE = /^[a-z][a-z0-9-]{0,13}[a-z0-9]$/;

/** base, or base2, base3, ... whichever is not taken yet. */
// freeName(): base, or base2, base3... whichever isn't taken ^w^
function freeName(base: string, taken: string[]) {
  if (!taken.includes(base)) return base;
  for (let i = 2; ; i++) if (!taken.includes(`${base}${i}`)) return `${base}${i}`;
}

/** What a rule can name: the segments and hosts that stay after the draft. */
// endpoints(): the segments and hosts a rule may name (not ones the draft removes) UwU
function endpoints(topo: Topology) {
  const segments = topo.nodes.filter((node) => node.kind === "segment" && node.change !== "remove").map((node) => node.label);
  const hosts = topo.nodes.filter((node) => node.kind === "host" && node.change !== "remove").map((node) => node.id.slice(5));
  return { segments, hosts };
}

// portsText(): "tcp 80,443" or just the protocol; parsePorts(): "80, 443 8000-8100" -> [80, 443, "8000-8100"] >w<
const portsText = (rule: Rule) => rule.proto === "tcp" || rule.proto === "udp" ? `${rule.proto} ${rule.ports.join(",")}` : rule.proto;

function parsePorts(text: string): (number | string)[] {
  return text.split(/[\s,]+/).filter(Boolean).map((port) => (/^\d+$/.test(port) ? Number(port) : port));
}

// RuleRow: one rule in a list, with × to remove it OwO
function RuleRow({ rule, self, onRemove }: { rule: Rule; self: string; onRemove: () => void }) {
  const shown = (endpoint: string) => (endpoint === SELF ? self || "(new segment)" : endpoint);
  return (
    <div className="rule-row">
      {/* From → to, ports, description, × */}
      <span className="mono">{shown(rule.from)} → {shown(rule.to)}</span>
      <span className="badge">{portsText(rule)}</span>
      <span className="muted small grow">{rule.description}</span>
      <button type="button" className="chip-x" aria-label="Remove rule" onClick={onRemove}>×</button>
    </div>
  );
}

// RuleFields: from / to / protocol (and ports for tcp/udp) ^w^
function RuleFields({ options, value, onChange }: { options: string[]; value: Rule; onChange: (rule: Rule) => void }) {
  return (
    <div className="rule-fields">
      {/* The fields */}
      <label className="field">From<select name="from" value={value.from} onChange={(e) => onChange({ ...value, from: e.target.value })}>
        {options.map((option) => <option key={option} value={option}>{option === SELF ? "(this segment)" : option}</option>)}</select></label>
      <label className="field">To<select name="to" value={value.to} onChange={(e) => onChange({ ...value, to: e.target.value })}>
        {options.map((option) => <option key={option} value={option}>{option === SELF ? "(this segment)" : option}</option>)}</select></label>
      <label className="field">Protocol<select name="proto" value={value.proto} onChange={(e) => onChange({ ...value, proto: e.target.value as Rule["proto"] })}>
        <option value="tcp">tcp</option><option value="udp">udp</option><option value="icmp">icmp (ping)</option><option value="any">any</option></select></label>
      {(value.proto === "tcp" || value.proto === "udp") && (
        <label className="field">Ports<input name="ports" value={value.ports.join(",")} placeholder="80,443 or 8000-8100"
          onChange={(e) => onChange({ ...value, ports: parsePorts(e.target.value) })} /></label>
      )}
    </div>
  );
}

// ruleOk(): two different endpoints, and ports unless it's icmp or any UwU
const ruleOk = (rule: Rule) =>
  rule.from && rule.to && rule.from !== rule.to && (rule.proto === "icmp" || rule.proto === "any" || rule.ports.length > 0);

// AddSegmentModal: the "Add a network segment" form >w<
export function AddSegmentModal({ name: range, topo, hasVpn, onClose, onSubmit }: {
  name: string; topo: Topology; hasVpn: boolean; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  // The endpoints, and the form state: preset, name, VLAN, network, internet, description, VPN reach, rules, the rule
  // being typed, and the allowed VLAN range OwO
  const { segments, hosts } = endpoints(topo);
  const [preset, setPreset] = useState<string>("");
  const [name, setName] = useState("");
  const [vlan, setVlan] = useState("");
  const [cidr, setCidr] = useState("");
  const [internet, setInternet] = useState(false);
  const [description, setDescription] = useState("");
  const [vpnReach, setVpnReach] = useState(true);
  const [rules, setRules] = useState<Rule[]>([]);
  const [draftRule, setDraftRule] = useState<Rule>({ from: SELF, to: segments[0] ?? "", proto: "tcp", ports: [] });
  const [vlanRange, setVlanRange] = useState<[number, number]>([100, 3999]);

  // Prefill a free VLAN and network from the server ^w^
  useEffect(() => {
    get<{ vlan: number; cidr: string; vlan_range: [number, number] }>(`/api/ranges/${range}/draft/suggest-segment`)
      .then((free) => { setVlan(String(free.vlan)); setCidr(free.cidr); setVlanRange(free.vlan_range); }).catch(() => {});
  }, [range]);

  // pick(): apply a preset (a free name, its settings and rules) UwU
  const pick = (chosen: Preset) => {
    setPreset(chosen.id);
    setName(freeName(chosen.name, [...segments, ...hosts]));
    setInternet(chosen.internet);
    setDescription(chosen.description);
    setRules(chosen.rules(segments));
    setVpnReach(chosen.id !== "isolated");
  };
  // The endpoint options (including the new segment itself) and whether the form is valid >w<
  const options = [SELF, ...segments, ...hosts];
  const valid = NAME_RE.test(name) && !segments.includes(name) && !hosts.includes(name) && /^\d+$/.test(vlan) && /\/\d+$/.test(cidr);
  // submit(): an add_segment edit plus one add_rule per rule (SELF becomes the new name) OwO
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const named = (endpoint: string) => (endpoint === SELF ? name : endpoint);
    onSubmit([
      { op: "add_segment", name, vlan: Number(vlan), cidr, internet, description, ...(hasVpn ? { vpn_reach: vpnReach } : {}) },
      ...rules.map((rule) => ({ op: "add_rule", from: named(rule.from), to: named(rule.to), proto: rule.proto, ports: rule.ports, description: rule.description ?? "" })),
    ]);
  };
  return (
    <Modal title="Add a network segment" onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="addseg" disabled={!valid}>Add to the draft</button></>
    }>
      {/* The form */}
      <form id="addseg" className="stack" onSubmit={submit}>
        {/* Presets, each with its hint */}
        <div className="field">Start from a preset <span className="hint">fills in the settings and rules below; change anything</span>
          <div className="preset-row">
            {PRESETS.map((option) => (
              <button type="button" key={option.id} className={`preset${preset === option.id ? " active" : ""}`} onClick={() => pick(option)} title={option.hint}>
                <b>{option.label}</b><span>{option.hint}</span>
              </button>
            ))}
          </div>
        </div>
        {/* Name, description, VLAN and network (both suggested) */}
        <div className="grid cols-2">
          <label className="field">Name <span className="hint">2-15 lowercase letters, digits, dashes</span>
            <input name="segment-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="servers" required />
          </label>
          <label className="field">Description<input name="description" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          <label className="field">VLAN <span className="hint">a free one is suggested ({vlanRange[0]}-{vlanRange[1]})</span>
            <input name="vlan" value={vlan} onChange={(e) => setVlan(e.target.value.replace(/\D/g, ""))} inputMode="numeric" />
          </label>
          <label className="field">Network <span className="hint">private, /16 to /29; a free /24 is suggested</span>
            <input name="cidr" className="mono" value={cidr} onChange={(e) => setCidr(e.target.value.trim())} placeholder="10.100.5.0/24" />
          </label>
        </div>
        {/* Internet egress, VPN reach, the rules (and adding one), and a warning that the router is rebuilt */}
        <label className="checkbox"><input name="internet" type="checkbox" checked={internet} onChange={(e) => setInternet(e.target.checked)} />
          Internet egress (public addresses only, never your LAN or the cluster)</label>
        {hasVpn && <label className="checkbox"><input name="vpn-reach" type="checkbox" checked={vpnReach} onChange={(e) => setVpnReach(e.target.checked)} />
          WireGuard peers may reach this segment</label>}
        <div className="field">Traffic rules <span className="hint">everything else between segments stays blocked</span>
          {rules.length === 0 && <div className="muted small">No rules: hosts here reach only their own segment{internet ? " and the internet" : ""}.</div>}
          {rules.map((rule, i) => <RuleRow key={i} rule={rule} self={name} onRemove={() => setRules(rules.filter((_, j) => j !== i))} />)}
          <RuleFields options={options} value={draftRule} onChange={setDraftRule} />
          <div><button type="button" className="btn small" disabled={!ruleOk(draftRule)} onClick={() => setRules([...rules, draftRule])}>+ Add rule</button></div>
        </div>
        <div className="alert warn small">Adding a segment rebuilds the range router (it gets a NIC per segment): routing pauses
          for about a minute. The router keeps its LAN address, so VPN configs stay valid.</div>
      </form>
    </Modal>
  );
}

// EditSegmentModal: change description, internet, VPN reach, VLAN or network ^w^
export function EditSegmentModal({ seg: segment, hasVpn, vpnReaches, onClose, onSubmit }: {
  seg: TopoNode; hasVpn: boolean; vpnReaches: boolean; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const [description, setDescription] = useState(segment.description ?? "");
  const [internet, setInternet] = useState(!!segment.internet);
  const [vlan, setVlan] = useState(String(segment.vlan ?? ""));
  const [cidr, setCidr] = useState(segment.cidr ?? "");
  const [vpnReach, setVpnReach] = useState(vpnReaches);
  const netChanged = vlan !== String(segment.vlan) || cidr !== segment.cidr;
  // submit(): only send what changed UwU
  const submit = () => onSubmit([{
    op: "update_segment", name: segment.label, description, internet,
    ...(vlan !== String(segment.vlan) ? { vlan: Number(vlan) } : {}), ...(cidr !== segment.cidr ? { cidr } : {}),
    ...(hasVpn && vpnReach !== vpnReaches ? { vpn_reach: vpnReach } : {}),
  }]);
  return (
    <Modal title={`Edit segment ${segment.label}`} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={submit}>Add to the draft</button></>
    }>
      {/* The fields, and a warning when the VLAN or network changes (the router and the segment's VMs are rebuilt) */}
      <div className="stack">
        <label className="field">Description<input name="description" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label className="checkbox"><input name="internet" type="checkbox" checked={internet} onChange={(e) => setInternet(e.target.checked)} /> Internet egress</label>
        {hasVpn && <label className="checkbox"><input name="vpn-reach" type="checkbox" checked={vpnReach} onChange={(e) => setVpnReach(e.target.checked)} /> WireGuard peers may reach it</label>}
        <div className="grid cols-2">
          <label className="field">VLAN<input name="vlan" value={vlan} onChange={(e) => setVlan(e.target.value.replace(/\D/g, ""))} /></label>
          <label className="field">Network<input name="cidr" className="mono" value={cidr} onChange={(e) => setCidr(e.target.value.trim())} /></label>
        </div>
        {netChanged && <div className="alert warn small">A new VLAN or network rebuilds the router and every VM in this segment
          (new addresses keep their last part, e.g. .10 stays .10); their disks are replaced.</div>}
      </div>
    </Modal>
  );
}

// RuleModal: the "Allow traffic" form: one rule with an optional reason >w<
export function RuleModal({ topo, from, onClose, onSubmit }: { topo: Topology; from?: string; onClose: () => void; onSubmit: (ops: Op[]) => void }) {
  const { segments, hosts } = endpoints(topo);
  const options = [...segments, ...hosts];
  const [rule, setRule] = useState<Rule>({ from: from ?? options[0] ?? "", to: options.find((option) => option !== from) ?? "", proto: "tcp", ports: [] });
  const [description, setDescription] = useState("");
  return (
    <Modal title="Allow traffic" onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!ruleOk(rule)} onClick={() => onSubmit([{ op: "add_rule", from: rule.from, to: rule.to, proto: rule.proto, ports: rule.ports, description }])}>Add to the draft</button></>
    }>
      {/* The rule's fields, why, and a note about same-segment traffic */}
      <div className="stack">
        <RuleFields options={options} value={rule} onChange={setRule} />
        <label className="field">Why <span className="hint">optional, shown on the map</span><input name="description" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <div className="muted small">Traffic between two hosts in the same segment never passes the router, so it needs no rule.</div>
      </div>
    </Modal>
  );
}

/** Rules that touch a segment or one of its hosts, with their index in the spec's policy. */
// rulesOf(): the rules touching a segment or its hosts, with their index in the spec's policy (for removing) OwO
export function rulesOf(topo: Topology, segment: string): { edge: TopoEdge; index: number; from: string; to: string }[] {
  const members = new Set([`seg:${segment}`, ...topo.nodes.filter((node) => node.parent === `seg:${segment}`).map((node) => node.id)]);
  return topo.edges.filter((edge) => edge.kind === "policy" && (members.has(edge.source) || members.has(edge.target)))
    .map((edge) => ({
      edge,
      index: Number(edge.id.split(":")[1]),                     // rule edge ids are "rule:<index in the policy>"
      from: edge.source.replace(/^(host|seg):/, ""),
      to: edge.target.replace(/^(host|seg):/, ""),
    }));
}
