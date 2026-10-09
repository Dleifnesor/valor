import { ReactNode, useMemo, useState } from "react";
import {
  Background, BaseEdge, Controls, Edge, EdgeLabelRenderer, EdgeProps, Handle, MarkerType, Node, NodeProps, Position, ReactFlow,
  getBezierPath, getSmoothStepPath,
} from "@xyflow/react";
import { Change, TopoEdge, TopoNode, Topology as Topo } from "../types";

// View-only map of a range. Layout is computed here (no dragging, no editing): Internet on top, the range router
// below it, one box per segment with its hosts inside, and the allowed flows between them as dashed arrows.
// With a plan, every VM gets a color for what the plan will do to it.

const SEG_W = 250;
const SEG_GAP = 64;                       // narrowest gap between segments; widened to fit the traffic rule labels
const MAX_GAP = 230;
const LABEL_H = 20;
const HEAD_H = 62;
const HOST_BASE = 60;                     // title + OS line
const CHIP_ROW = 20;                      // one row of role chips
const HOST_GAP = 12;
const PAD = 14;
const ROUTER_W = 220;
const INET_W = 130;
const SEG_Y = 250;

export const CHANGE_LABEL: Record<Change, string> = {
  create: "added", update: "changed", replace: "rebuilt", remove: "removed", keep: "unchanged",
};

type Data = { raw: TopoNode };

const hidden = { opacity: 0, width: 6, height: 6, minWidth: 0, minHeight: 0, border: 0 };

function Handles() {
  return (
    <>
      <Handle type="target" position={Position.Top} id="t" style={hidden} />
      <Handle type="source" position={Position.Bottom} id="b" style={hidden} />
      <Handle type="source" position={Position.Right} id="rs" style={hidden} />
      <Handle type="target" position={Position.Right} id="rt" style={hidden} />
      <Handle type="source" position={Position.Left} id="ls" style={hidden} />
      <Handle type="target" position={Position.Left} id="lt" style={hidden} />
    </>
  );
}

function statusDot(s?: string) {
  if (!s) return null;
  return <span className={`dot ${s === "running" ? "ok" : s === "stopped" ? "bad" : ""}`} title={s} />;
}

function HostNode({ data }: NodeProps<Node<Data>>) {
  const n = data.raw;
  return (
    <div className={`tnode${n.change && n.change !== "keep" ? ` change-${n.change}` : ""}`}>
      <Handles />
      <div className="t-title">{statusDot(n.status)}{n.label}
        {n.change && n.change !== "keep" && <span className={`badge`} style={{ marginLeft: "auto", color: `var(--c-${n.change})` }}>{CHANGE_LABEL[n.change]}</span>}
      </div>
      <div className="t-sub">{n.address} · {n.os}</div>
      {!!n.roles?.length && <div className="chips">{n.roles.map((r) => <span className="chip" key={r}>{r}</span>)}</div>}
    </div>
  );
}

function RouterNode({ data }: NodeProps<Node<Data>>) {
  const n = data.raw;
  return (
    <div className={`tnode router${n.change && n.change !== "keep" ? ` change-${n.change}` : ""}`}>
      <Handles />
      <div className="t-title">{statusDot(n.status)}{n.label}</div>
      <div className="t-sub">router · NAT · firewall</div>
      <div className="t-sub">{n.os}</div>
    </div>
  );
}

function VpnNode({ data }: NodeProps<Node<Data>>) {
  const n = data.raw;
  return (
    <div className="tnode vpn">
      <Handles />
      <div className="t-title">WireGuard</div>
      <div className="t-sub">udp/{n.port} · {n.peers?.length ?? 0} peer{n.peers?.length === 1 ? "" : "s"}</div>
      <div className="t-sub">reaches {n.reach?.join(", ")}</div>
    </div>
  );
}

function InternetNode() {
  return (
    <div className="tnode internet">
      <Handles />
      Internet
    </div>
  );
}

function SegmentNode({ data }: NodeProps<Node<Data>>) {
  const n = data.raw;
  return (
    <div className="tgroup">
      <Handles />
      <div className="g-head">
        <div className="row" style={{ gap: 6 }}>
          <b>{n.label}</b>
          <span className="badge">VLAN {n.vlan}</span>
          {n.internet && <span className="badge info" title="Hosts may reach public internet addresses">internet</span>}
        </div>
        <div className="t-sub">{n.cidr} · gw {n.gateway}</div>
      </div>
    </div>
  );
}

const nodeTypes = { host: HostNode, router: RouterNode, internet: InternetNode, segment: SegmentNode, vpn: VpnNode };

// Edge labels are HTML above the nodes, so a segment box or another line never covers them.
type LabelData = { lx?: number; ly?: number; onSelect?: () => void };

function EdgeLabel({ x, y, text, className, onSelect }: { x: number; y: number; text: ReactNode; className: string; onSelect?: () => void }) {
  return (
    <EdgeLabelRenderer>
      <div className={`edge-label ${className}${onSelect ? " clickable" : ""}`} onClick={onSelect}
        style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}>{text}</div>
    </EdgeLabelRenderer>
  );
}

function RuleEdge(p: EdgeProps<Edge<LabelData>>) {
  const [path, mx, my] = getBezierPath(p);
  return (
    <>
      <BaseEdge id={p.id} path={path} style={p.style} markerEnd={p.markerEnd} markerStart={p.markerStart} interactionWidth={14} />
      {p.label && <EdgeLabel x={p.data?.lx ?? mx} y={p.data?.ly ?? my} text={p.label} className="rule" onSelect={p.data?.onSelect} />}
    </>
  );
}

function GatewayEdge(p: EdgeProps<Edge<LabelData>>) {
  const [path, mx, my] = getSmoothStepPath(p);
  return (
    <>
      <BaseEdge id={p.id} path={path} style={p.style} />
      {p.label && <EdgeLabel x={mx} y={my} text={p.label} className="gateway" />}
    </>
  );
}

const edgeTypes = { rule: RuleEdge, gateway: GatewayEdge };

// Where the label of a rule between two columns goes: in a gap between segments, never over a segment box. The
// curve is React Flow's bezier from side handle to side handle (both control points at the middle x), so the label
// sits on the line at the gap's centre.
function bezierYAt(sx: number, sy: number, tx: number, ty: number, x: number): number {
  const mx = (sx + tx) / 2;
  const at = (a: number, b: number, c: number, d: number, t: number) =>
    a * (1 - t) ** 3 + 3 * b * (1 - t) ** 2 * t + 3 * c * (1 - t) * t ** 2 + d * t ** 3;
  let lo = 0, hi = 1;
  for (let i = 0; i < 30; i++) {
    const mid = (lo + hi) / 2;
    if ((at(sx, mx, mx, tx, mid) - x) * (tx - sx) < 0) lo = mid; else hi = mid;
  }
  return at(sy, sy, ty, ty, (lo + hi) / 2);
}

const labelWidth = (text: string) => text.length * 7 + 14;      // 11px monospace plus the label's padding
const VPN_W = 190;

// Height of a host card: its role chips wrap, so count the rows they need at the card's width.
function hostHeight(h: TopoNode): number {
  const roles = h.roles ?? [];
  if (!roles.length) return HOST_BASE + 4;
  const inner = SEG_W - 2 * PAD - 22;
  let rows = 1, used = 0;
  for (const r of roles) {
    const w = 12 + r.length * 6.6 + 4;
    if (used && used + w > inner) { rows += 1; used = 0; }
    used += w;
  }
  return HOST_BASE + rows * CHIP_ROW + 4;
}

// Long port lists would cover the map: "tcp 80,8080,+5" (the side panel shows the rule in full)
function shortLabel(label?: string): string | undefined {
  if (!label || label.length <= 20) return label;
  const [proto, ports = ""] = label.split(" ", 2);
  const list = ports.split(",");
  return list.length > 2 ? `${proto} ${list.slice(0, 2).join(",")},+${list.length - 2}` : `${label.slice(0, 18)}…`;
}

function layout(topo: Topo, onRules: (group: TopoEdge[]) => void): { nodes: Node<Data>[]; edges: Edge[] } {
  const segs = topo.nodes.filter((n) => n.kind === "segment");
  const hosts = topo.nodes.filter((n) => n.kind === "host");
  const nodes: Node<Data>[] = [];
  const abs: Record<string, { x: number; y: number; w: number; h: number }> = {};

  // rules between the same two endpoints (either direction) share one curve: draw them as one arrow
  const groups = new Map<string, TopoEdge[]>();
  for (const e of topo.edges) {
    if (e.kind !== "policy") continue;
    const k = [e.source, e.target].sort().join("|");
    groups.set(k, [...(groups.get(k) ?? []), e]);
  }
  const ruleLabel = (group: TopoEdge[]) => (group.length > 1 ? `${group.length} rules` : shortLabel(group[0].label) ?? "");
  // the gaps between segments are wide enough for the longest label plus an arrow on each side
  const widest = Math.max(0, ...[...groups.values()].map((g) => labelWidth(ruleLabel(g))));
  const gap = Math.min(MAX_GAP, Math.max(SEG_GAP, widest + 44));
  const total = segs.length * SEG_W + Math.max(0, segs.length - 1) * gap;
  const colX = (i: number) => -total / 2 + i * (SEG_W + gap);

  const inet = topo.nodes.find((n) => n.kind === "internet");
  if (inet) {
    nodes.push({ id: inet.id, type: "internet", position: { x: -INET_W / 2, y: 0 }, data: { raw: inet },
      style: { width: INET_W, height: 44 } });
    abs[inet.id] = { x: -INET_W / 2, y: 0, w: INET_W, h: 44 };
  }
  const rtr = topo.nodes.find((n) => n.kind === "router");
  if (rtr) {
    nodes.push({ id: rtr.id, type: "router", position: { x: -ROUTER_W / 2, y: 110 }, data: { raw: rtr },
      style: { width: ROUTER_W, height: 74 } });
    abs[rtr.id] = { x: -ROUTER_W / 2, y: 110, w: ROUTER_W, h: 74 };
  }
  const vpn = topo.nodes.find((n) => n.kind === "vpn");
  if (vpn) {
    const pos = { x: ROUTER_W / 2 + 90, y: 110 };
    nodes.push({ id: vpn.id, type: "vpn", position: pos, data: { raw: vpn }, style: { width: VPN_W, height: 74 } });
    abs[vpn.id] = { ...pos, w: VPN_W, h: 74 };
  }
  segs.forEach((s, i) => {
    const members = hosts.filter((h) => h.parent === s.id);
    const heights = members.map(hostHeight);
    const height = HEAD_H + (heights.length ? heights.reduce((a, b) => a + b + HOST_GAP, 0) : HOST_BASE + HOST_GAP)
      + PAD - HOST_GAP + 6;
    const x = colX(i);
    nodes.push({ id: s.id, type: "segment", position: { x, y: SEG_Y }, data: { raw: s },
      style: { width: SEG_W, height }, selectable: true });
    abs[s.id] = { x, y: SEG_Y, w: SEG_W, h: height };
    let y = HEAD_H;
    members.forEach((h, j) => {
      const rel = { x: PAD, y };
      nodes.push({ id: h.id, type: "host", parentId: s.id, extent: "parent", position: rel, data: { raw: h },
        style: { width: SEG_W - 2 * PAD, height: heights[j] } });
      abs[h.id] = { x: x + rel.x, y: SEG_Y + rel.y, w: SEG_W - 2 * PAD, h: heights[j] };
      y += heights[j] + HOST_GAP;
    });
  });
  let ry = SEG_Y;
  hosts.filter((h) => !h.parent).forEach((h) => {             // VMs a plan removes
    const pos = { x: colX(segs.length) + PAD, y: ry };
    nodes.push({ id: h.id, type: "host", position: pos, data: { raw: h }, style: { width: SEG_W - 2 * PAD, height: hostHeight(h) } });
    abs[h.id] = { ...pos, w: SEG_W - 2 * PAD, h: hostHeight(h) };
    ry += hostHeight(h) + HOST_GAP;
  });

  // a rule's label goes into one of the gaps its curve crosses: the most central one that is still free
  const column = (id: string) => {
    const n = topo.nodes.find((x) => x.id === id);
    const seg = n?.kind === "segment" ? n.id : n?.parent;
    const i = segs.findIndex((x) => x.id === seg);
    return i < 0 ? segs.length : i;
  };
  const placed: { x: number; y: number; w: number }[] = [];
  const free = (x: number, y: number, w: number) =>
    placed.every((o) => Math.abs(o.x - x) * 2 >= o.w + w || Math.abs(o.y - y) >= LABEL_H + 4);
  const labelAt = (e: TopoEdge, text: string): { lx?: number; ly?: number } => {
    const s = abs[e.source], t = abs[e.target];
    const a = column(e.source), b = column(e.target);
    if (a === b) return {};                                          // same column: React Flow's own midpoint
    const right = a < b;
    const sx = right ? s.x + s.w : s.x, tx = right ? t.x : t.x + t.w;
    const sy = s.y + s.h / 2, ty = t.y + t.h / 2;
    const lo = Math.min(a, b), hi = Math.max(a, b), mid = (lo + hi - 1) / 2;
    const gaps = Array.from({ length: hi - lo }, (_, k) => lo + k).sort((p, q) => Math.abs(p - mid) - Math.abs(q - mid));
    const w = labelWidth(text);
    const spots = gaps.map((g) => { const x = colX(g) + SEG_W + gap / 2; return { x, y: bezierYAt(sx, sy, tx, ty, x) }; });
    let spot = spots.find((p) => free(p.x, p.y, w));
    if (!spot) {                                                     // every gap is taken here: step below the others
      spot = { ...spots[0] };
      while (!free(spot.x, spot.y, w)) spot.y += LABEL_H + 4;
    }
    placed.push({ ...spot, w });
    return { lx: spot.x, ly: spot.y };
  };

  const edges: Edge[] = [];
  for (const e of topo.edges) {
    if (e.kind === "egress" || !abs[e.source] || !abs[e.target]) continue;   // internet access is a badge on the segment
    if (e.kind === "vpn") {
      edges.push({ id: e.id, source: e.source, target: e.target, sourceHandle: "ls", targetHandle: "rt", type: "straight",
        label: e.label, animated: true, style: { strokeWidth: 2, stroke: "var(--ok)", strokeDasharray: "4 3" },
        labelStyle: { fontFamily: "var(--mono)", fontSize: 11, fill: "var(--muted)" },
        labelBgStyle: { fill: "var(--surface)" }, labelBgPadding: [4, 2], labelBgBorderRadius: 4 });
      continue;
    }
    if (e.kind === "uplink" || e.kind === "gateway") {
      edges.push({ id: e.id, source: e.source, target: e.target, sourceHandle: "b", targetHandle: "t",
        type: e.kind === "gateway" ? "gateway" : "straight", label: e.kind === "gateway" ? e.label : undefined,
        style: { strokeWidth: 2, stroke: "var(--border-strong)" } });
      continue;
    }
    const group = groups.get([e.source, e.target].sort().join("|")) ?? [e];
    if (group[0].id !== e.id) continue;
    const s = abs[e.source], t = abs[e.target];
    const leftToRight = s.x < t.x;
    const both = group.some((g) => g.source !== e.source);
    const text = ruleLabel(group);
    edges.push({
      id: e.id, source: e.source, target: e.target, type: "rule", animated: true,
      sourceHandle: leftToRight ? "rs" : "ls", targetHandle: leftToRight ? "lt" : "rt",
      label: text, data: { description: e.description, group, ...labelAt(e, text), onSelect: () => onRules(group) },
      ...(both ? { markerStart: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 } } : {}),
      style: { stroke: "var(--accent)", strokeWidth: 1.8, strokeDasharray: "6 4" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
    });
  }
  return { nodes, edges };
}

function colorMode(): "light" | "dark" {
  const t = document.documentElement.dataset.theme;
  if (t === "light" || t === "dark") return t;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function TopologyMap({ topology, tall, onConsole, overlay, nodeActions, edgeActions }: {
  topology: Topo; tall?: boolean; onConsole?: (host: string) => void;
  overlay?: ReactNode;                                   // e.g. editing / chat buttons over the map
  nodeActions?: (node: TopoNode, close: () => void) => ReactNode;   // buttons in the selected node's panel
  edgeActions?: (edge: TopoEdge, close: () => void) => ReactNode;   // buttons for a selected traffic rule
}) {
  const [selected, setSelected] = useState<TopoNode | null>(null);
  const [rules, setRules] = useState<TopoEdge[] | null>(null);
  const { nodes, edges } = useMemo(() => layout(topology, (group) => { setRules(group); setSelected(null); }), [topology]);
  const label = (id: string) => topology.nodes.find((n) => n.id === id)?.label ?? id.replace(/^(host|seg):/, "");
  const key = useMemo(() => topology.nodes.map((n) => n.id + (n.change ?? "")).join("|"), [topology]);
  const changes = topology.changes;

  return (
    <div className={`map${tall ? " tall" : ""}`}>
      {changes && (
        <div className="legend" aria-label="Legend">
          {(["create", "update", "replace", "remove", "keep"] as Change[]).map((c) => (
            <span key={c}><i style={{ background: `var(--c-${c})` }} />{CHANGE_LABEL[c]} {changes[c] ?? 0}</span>
          ))}
        </div>
      )}
      {overlay}
      <ReactFlow
        key={key}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        colorMode={colorMode()}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        elementsSelectable
        minZoom={0.2}
        maxZoom={1.75}
        onNodeClick={(_, n) => { setSelected((n.data as Data).raw); setRules(null); }}
        onEdgeClick={(_, e) => {
          const group = (e.data as { group?: TopoEdge[] } | undefined)?.group;
          if (group?.length) { setRules(group); setSelected(null); }
        }}
        onPaneClick={() => { setSelected(null); setRules(null); }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {rules && (
        <div className="side-panel card">
          <div className="card-head">
            <h2>{rules.length > 1 ? `${rules.length} traffic rules` : "Traffic rule"}</h2>
            <button className="btn ghost small" onClick={() => setRules(null)} aria-label="Close">✕</button>
          </div>
          <div className="card-body">
            {rules.map((rule) => (
              <div key={rule.id} className="rule-item">
                <dl className="kv">
                  <dt>From</dt><dd>{label(rule.source)}</dd>
                  <dt>To</dt><dd>{label(rule.target)}</dd>
                  <dt>Allows</dt><dd className="mono">{rule.label}</dd>
                  {rule.description && <><dt>Why</dt><dd>{rule.description}</dd></>}
                </dl>
                {edgeActions?.(rule, () => setRules(null))}
              </div>
            ))}
          </div>
        </div>
      )}
      {selected && selected.kind !== "internet" && (
        <div className="side-panel card">
          <div className="card-head">
            <h2>{selected.label}</h2>
            <button className="btn ghost small" onClick={() => setSelected(null)} aria-label="Close">✕</button>
          </div>
          <div className="card-body">
            <dl className="kv">
              {selected.kind === "vpn" ? (
                <>
                  <dt>Port</dt><dd className="mono">udp/{selected.port} on the router's uplink</dd>
                  {selected.endpoint && <><dt>Endpoint</dt><dd className="mono">{selected.endpoint}</dd></>}
                  <dt>Tunnel</dt><dd className="mono">{selected.cidr}</dd>
                  <dt>Peers</dt><dd>{selected.peers?.join(", ")}</dd>
                  <dt>Reach</dt><dd>{selected.reach?.join(", ")}</dd>
                </>
              ) : selected.kind === "segment" ? (
                <>
                  <dt>VLAN</dt><dd>{selected.vlan}</dd>
                  <dt>Network</dt><dd className="mono">{selected.cidr}</dd>
                  <dt>Gateway</dt><dd className="mono">{selected.gateway}</dd>
                  <dt>Internet</dt><dd>{selected.internet ? "yes (public addresses only)" : "no"}</dd>
                  {selected.description && <><dt>About</dt><dd>{selected.description}</dd></>}
                </>
              ) : (
                <>
                  {selected.address && <><dt>Address</dt><dd className="mono">{selected.address}</dd></>}
                  {selected.gateways && <><dt>Gateways</dt><dd className="mono">{selected.gateways.join(", ")}</dd></>}
                  <dt>OS</dt><dd>{selected.os || "–"}</dd>
                  {selected.cores !== undefined && <><dt>Size</dt><dd>{selected.cores} vCPU · {selected.memory} MiB · {selected.disk} GiB</dd></>}
                  {!!selected.roles?.length && <><dt>Roles</dt><dd>{selected.roles.join(", ")}</dd></>}
                  <dt>VM</dt><dd>{selected.vmid ? `${selected.vmid} · ${selected.status}` : "not built yet"}</dd>
                  {selected.change && <><dt>Plan</dt><dd>{CHANGE_LABEL[selected.change]}</dd></>}
                  {!!selected.reasons?.length && <><dt>Why</dt><dd>{selected.reasons.join("; ")}</dd></>}
                </>
              )}
            </dl>
            {onConsole && selected.kind !== "segment" && selected.kind !== "vpn" && selected.status === "running" && (
              <button className="btn primary small" style={{ marginTop: 10 }}
                onClick={() => onConsole(selected.id === "rtr" ? "rtr" : selected.id.replace(/^host:/, ""))}>
                Open console
              </button>
            )}
            {nodeActions?.(selected, () => setSelected(null))}
          </div>
        </div>
      )}
    </div>
  );
}
