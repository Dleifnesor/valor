import { ReactNode, useMemo, useState } from "react";
import {
  Background, BaseEdge, Controls, Edge, EdgeLabelRenderer, EdgeProps, Handle, MarkerType, Node, NodeProps, Position, ReactFlow,
  getBezierPath, getSmoothStepPath,
} from "@xyflow/react";
import { Change, TopoEdge, TopoNode, Topology as Topo } from "../types";
import { MenuItems } from "./ui";
import { showMenu } from "./ContextMenu";

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

/** What every React Flow node carries: the topology node it draws. */
type Data = { raw: TopoNode };

// Invisible connection points on all four sides: edges attach where the layout says, users can't connect anything.
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

function statusDot(status?: string) {
  if (!status) return null;
  return <span className={`dot ${status === "running" ? "ok" : status === "stopped" ? "bad" : ""}`} title={status} />;
}

function HostNode({ data }: NodeProps<Node<Data>>) {
  const node = data.raw;
  return (
    <div className={`tnode${node.change && node.change !== "keep" ? ` change-${node.change}` : ""}`}>
      <Handles />
      <div className="t-title">{statusDot(node.status)}{node.label}
        {node.change && node.change !== "keep" && <span className={`badge`} style={{ marginLeft: "auto", color: `var(--c-${node.change})` }}>{CHANGE_LABEL[node.change]}</span>}
      </div>
      <div className="t-sub">{node.address} · {node.os}</div>
      {!!node.roles?.length && <div className="chips">{node.roles.map((role) => <span className="chip" key={role}>{role}</span>)}</div>}
    </div>
  );
}

function RouterNode({ data }: NodeProps<Node<Data>>) {
  const node = data.raw;
  return (
    <div className={`tnode router${node.change && node.change !== "keep" ? ` change-${node.change}` : ""}`}>
      <Handles />
      <div className="t-title">{statusDot(node.status)}{node.label}</div>
      <div className="t-sub">router · NAT · firewall</div>
      <div className="t-sub">{node.os}</div>
    </div>
  );
}

function VpnNode({ data }: NodeProps<Node<Data>>) {
  const node = data.raw;
  return (
    <div className="tnode vpn">
      <Handles />
      <div className="t-title">WireGuard</div>
      <div className="t-sub">udp/{node.port} · {node.peers?.length ?? 0} peer{node.peers?.length === 1 ? "" : "s"}</div>
      <div className="t-sub">reaches {node.reach?.join(", ")}</div>
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
  const segment = data.raw;
  return (
    <div className="tgroup">
      <Handles />
      <div className="g-head">
        <div className="row" style={{ gap: 6 }}>
          <b>{segment.label}</b>
          <span className="badge">VLAN {segment.vlan}</span>
          {segment.internet && <span className="badge info" title="Hosts may reach public internet addresses">internet</span>}
        </div>
        <div className="t-sub">{segment.cidr} · gw {segment.gateway}</div>
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

function RuleEdge(props: EdgeProps<Edge<LabelData>>) {
  const [path, midX, midY] = getBezierPath(props);
  return (
    <>
      <BaseEdge id={props.id} path={path} style={props.style} markerEnd={props.markerEnd} markerStart={props.markerStart} interactionWidth={14} />
      {props.label && <EdgeLabel x={props.data?.lx ?? midX} y={props.data?.ly ?? midY} text={props.label} className="rule" onSelect={props.data?.onSelect} />}
    </>
  );
}

function GatewayEdge(props: EdgeProps<Edge<LabelData>>) {
  const [path, midX, midY] = getSmoothStepPath(props);
  return (
    <>
      <BaseEdge id={props.id} path={path} style={props.style} />
      {props.label && <EdgeLabel x={midX} y={midY} text={props.label} className="gateway" />}
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
  let lo = 0, hi = 1;                                // bisect for the curve parameter t at which the curve reaches x
  for (let i = 0; i < 30; i++) {
    const mid = (lo + hi) / 2;
    if ((at(sx, mx, mx, tx, mid) - x) * (tx - sx) < 0) lo = mid; else hi = mid;
  }
  return at(sy, sy, ty, ty, (lo + hi) / 2);
}

const labelWidth = (text: string) => text.length * 7 + 14;      // 11px monospace plus the label's padding
const VPN_W = 190;

// Height of a host card: its role chips wrap, so count the rows they need at the card's width.
function hostHeight(host: TopoNode): number {
  const roles = host.roles ?? [];
  if (!roles.length) return HOST_BASE + 4;
  const inner = SEG_W - 2 * PAD - 22;
  let rows = 1, used = 0;
  for (const role of roles) {
    const width = 12 + role.length * 6.6 + 4;
    if (used && used + width > inner) { rows += 1; used = 0; }
    used += width;
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

// ---- Layout ----------------------------------------------------------------------------------------------------

type Box = { x: number; y: number; w: number; h: number };

/** Rules between the same two endpoints (either direction) share one curve: they are drawn as one arrow. */
function ruleGroups(topo: Topo): Map<string, TopoEdge[]> {
  const groups = new Map<string, TopoEdge[]>();
  for (const edge of topo.edges) {
    if (edge.kind !== "policy") continue;
    const key = [edge.source, edge.target].sort().join("|");
    groups.set(key, [...(groups.get(key) ?? []), edge]);
  }
  return groups;
}

const ruleLabel = (group: TopoEdge[]) => (group.length > 1 ? `${group.length} rules` : shortLabel(group[0].label) ?? "");

/** Positions every node: Internet, router and VPN on top, one column per segment with its hosts stacked inside,
 *  and VMs a plan removes in an extra column on the right. `boxes` gets every node's absolute box (React Flow
 *  positions hosts relative to their segment), which the edge labels need. */
function placeNodes(topo: Topo, colX: (i: number) => number, boxes: Record<string, Box>): Node<Data>[] {
  const segments = topo.nodes.filter((node) => node.kind === "segment");
  const hosts = topo.nodes.filter((node) => node.kind === "host");
  const nodes: Node<Data>[] = [];
  const fixed = (node: TopoNode | undefined, type: string, box: Box) => {
    if (!node) return;
    nodes.push({ id: node.id, type, position: { x: box.x, y: box.y }, data: { raw: node }, style: { width: box.w, height: box.h } });
    boxes[node.id] = box;
  };
  fixed(topo.nodes.find((node) => node.kind === "internet"), "internet", { x: -INET_W / 2, y: 0, w: INET_W, h: 44 });
  fixed(topo.nodes.find((node) => node.kind === "router"), "router", { x: -ROUTER_W / 2, y: 110, w: ROUTER_W, h: 74 });
  fixed(topo.nodes.find((node) => node.kind === "vpn"), "vpn", { x: ROUTER_W / 2 + 90, y: 110, w: VPN_W, h: 74 });

  segments.forEach((segment, i) => {
    const members = hosts.filter((host) => host.parent === segment.id);
    const heights = members.map(hostHeight);
    const height = HEAD_H + (heights.length ? heights.reduce((sum, h) => sum + h + HOST_GAP, 0) : HOST_BASE + HOST_GAP)
      + PAD - HOST_GAP + 6;
    const x = colX(i);
    nodes.push({ id: segment.id, type: "segment", position: { x, y: SEG_Y }, data: { raw: segment },
      style: { width: SEG_W, height }, selectable: true });
    boxes[segment.id] = { x, y: SEG_Y, w: SEG_W, h: height };
    let y = HEAD_H;
    members.forEach((host, j) => {
      const relative = { x: PAD, y };
      nodes.push({ id: host.id, type: "host", parentId: segment.id, extent: "parent", position: relative, data: { raw: host },
        style: { width: SEG_W - 2 * PAD, height: heights[j] } });
      boxes[host.id] = { x: x + relative.x, y: SEG_Y + relative.y, w: SEG_W - 2 * PAD, h: heights[j] };
      y += heights[j] + HOST_GAP;
    });
  });

  let removedY = SEG_Y;
  hosts.filter((host) => !host.parent).forEach((host) => {         // VMs a plan removes
    const position = { x: colX(segments.length) + PAD, y: removedY };
    nodes.push({ id: host.id, type: "host", position, data: { raw: host }, style: { width: SEG_W - 2 * PAD, height: hostHeight(host) } });
    boxes[host.id] = { ...position, w: SEG_W - 2 * PAD, h: hostHeight(host) };
    removedY += hostHeight(host) + HOST_GAP;
  });
  return nodes;
}

/** A function that picks a free spot for a rule's label: in one of the gaps the curve crosses, the most central
 *  one that is still free (or below the labels already there). */
function labelPlacer(topo: Topo, boxes: Record<string, Box>, colX: (i: number) => number, gap: number) {
  const segments = topo.nodes.filter((node) => node.kind === "segment");
  const column = (id: string) => {
    const node = topo.nodes.find((x) => x.id === id);
    const segmentId = node?.kind === "segment" ? node.id : node?.parent;
    const i = segments.findIndex((segment) => segment.id === segmentId);
    return i < 0 ? segments.length : i;
  };
  const placed: { x: number; y: number; w: number }[] = [];
  const free = (x: number, y: number, w: number) =>
    placed.every((other) => Math.abs(other.x - x) * 2 >= other.w + w || Math.abs(other.y - y) >= LABEL_H + 4);
  return (edge: TopoEdge, text: string): { lx?: number; ly?: number } => {
    const source = boxes[edge.source], target = boxes[edge.target];
    const a = column(edge.source), b = column(edge.target);
    if (a === b) return {};                                          // same column: React Flow's own midpoint
    const right = a < b;
    const sx = right ? source.x + source.w : source.x, tx = right ? target.x : target.x + target.w;
    const sy = source.y + source.h / 2, ty = target.y + target.h / 2;
    const lo = Math.min(a, b), hi = Math.max(a, b), mid = (lo + hi - 1) / 2;
    const gaps = Array.from({ length: hi - lo }, (_, k) => lo + k).sort((p, q) => Math.abs(p - mid) - Math.abs(q - mid));
    const w = labelWidth(text);
    const spots = gaps.map((g) => { const x = colX(g) + SEG_W + gap / 2; return { x, y: bezierYAt(sx, sy, tx, ty, x) }; });
    let spot = spots.find((candidate) => free(candidate.x, candidate.y, w));
    if (!spot) {                                                     // every gap is taken here: step below the others
      spot = { ...spots[0] };
      while (!free(spot.x, spot.y, w)) spot.y += LABEL_H + 4;
    }
    placed.push({ ...spot, w });
    return { lx: spot.x, ly: spot.y };
  };
}

function layout(topo: Topo, onRules: (group: TopoEdge[]) => void): { nodes: Node<Data>[]; edges: Edge[] } {
  const segmentCount = topo.nodes.filter((node) => node.kind === "segment").length;
  const groups = ruleGroups(topo);
  // the gaps between segments are wide enough for the longest label plus an arrow on each side
  const widest = Math.max(0, ...[...groups.values()].map((group) => labelWidth(ruleLabel(group))));
  const gap = Math.min(MAX_GAP, Math.max(SEG_GAP, widest + 44));
  const total = segmentCount * SEG_W + Math.max(0, segmentCount - 1) * gap;
  const colX = (i: number) => -total / 2 + i * (SEG_W + gap);

  const boxes: Record<string, Box> = {};
  const nodes = placeNodes(topo, colX, boxes);
  const labelAt = labelPlacer(topo, boxes, colX, gap);

  const edges: Edge[] = [];
  for (const edge of topo.edges) {
    if (edge.kind === "egress" || !boxes[edge.source] || !boxes[edge.target]) continue;   // internet access is a badge on the segment
    if (edge.kind === "vpn") {
      edges.push({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: "ls", targetHandle: "rt", type: "straight",
        label: edge.label, animated: true, style: { strokeWidth: 2, stroke: "var(--ok)", strokeDasharray: "4 3" },
        labelStyle: { fontFamily: "var(--mono)", fontSize: 11, fill: "var(--muted)" },
        labelBgStyle: { fill: "var(--surface)" }, labelBgPadding: [4, 2], labelBgBorderRadius: 4 });
      continue;
    }
    if (edge.kind === "uplink" || edge.kind === "gateway") {
      edges.push({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: "b", targetHandle: "t",
        type: edge.kind === "gateway" ? "gateway" : "straight", label: edge.kind === "gateway" ? edge.label : undefined,
        style: { strokeWidth: 2, stroke: "var(--border-strong)" } });
      continue;
    }
    // a traffic rule: only the first rule of a group draws the (shared) arrow
    const group = groups.get([edge.source, edge.target].sort().join("|")) ?? [edge];
    if (group[0].id !== edge.id) continue;
    const leftToRight = boxes[edge.source].x < boxes[edge.target].x;
    const bothWays = group.some((other) => other.source !== edge.source);
    const text = ruleLabel(group);
    edges.push({
      id: edge.id, source: edge.source, target: edge.target, type: "rule", animated: true,
      sourceHandle: leftToRight ? "rs" : "ls", targetHandle: leftToRight ? "lt" : "rt",
      label: text, data: { description: edge.description, group, ...labelAt(edge, text), onSelect: () => onRules(group) },
      ...(bothWays ? { markerStart: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 } } : {}),
      style: { stroke: "var(--accent)", strokeWidth: 1.8, strokeDasharray: "6 4" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
    });
  }
  return { nodes, edges };
}

function colorMode(): "light" | "dark" {
  const theme = document.documentElement.dataset.theme;
  if (theme === "light" || theme === "dark") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// ---- The map -----------------------------------------------------------------------------------------------------

export function TopologyMap({ topology, tall, onConsole, overlay, nodeActions, edgeActions, nodeMenu }: {
  topology: Topo; tall?: boolean; onConsole?: (host: string) => void;
  overlay?: ReactNode;                                   // e.g. editing / chat buttons over the map
  nodeActions?: (node: TopoNode, close: () => void) => ReactNode;   // buttons in the selected node's panel
  edgeActions?: (edge: TopoEdge, close: () => void) => ReactNode;   // buttons for a selected traffic rule
  nodeMenu?: (node: TopoNode) => MenuItems;              // a node's right-click menu, after "Details"
}) {
  const [selected, setSelected] = useState<TopoNode | null>(null);
  const [rules, setRules] = useState<TopoEdge[] | null>(null);
  const showRules = (group: TopoEdge[]) => { setRules(group); setSelected(null); };
  const showNode = (node: TopoNode) => { setSelected(node); setRules(null); };
  const { nodes, edges } = useMemo(() => layout(topology, showRules), [topology]);
  const label = (id: string) => topology.nodes.find((node) => node.id === id)?.label ?? id.replace(/^(host|seg):/, "");
  // a new key remounts React Flow, so fitView runs again when nodes appear, go or change state
  const key = useMemo(() => topology.nodes.map((node) => node.id + (node.change ?? "")).join("|"), [topology]);
  const changes = topology.changes;

  return (
    <div className={`map${tall ? " tall" : ""}`}>
      {changes && (
        <div className="legend" aria-label="Legend">
          {(["create", "update", "replace", "remove", "keep"] as Change[]).map((change) => (
            <span key={change}><i style={{ background: `var(--c-${change})` }} />{CHANGE_LABEL[change]} {changes[change] ?? 0}</span>
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
        onNodeClick={(_, flowNode) => showNode((flowNode.data as Data).raw)}
        onEdgeClick={(_, flowEdge) => {
          const group = (flowEdge.data as { group?: TopoEdge[] } | undefined)?.group;
          if (group?.length) showRules(group);
        }}
        onPaneClick={() => { setSelected(null); setRules(null); }}
        onNodeContextMenu={(event, flowNode) => {
          const node = (flowNode.data as Data).raw;
          if (node.kind === "internet") return;
          showMenu(event, { title: node.label, items: [
            { label: "Details", icon: "info", onClick: () => showNode(node) },
            "-",
            ...(nodeMenu?.(node) ?? []),
          ] });
        }}
        onEdgeContextMenu={(event, flowEdge) => {
          const group = (flowEdge.data as { group?: TopoEdge[] } | undefined)?.group;
          if (!group?.length) return;
          showMenu(event, { title: group.length > 1 ? `${group.length} traffic rules` : "Traffic rule", items: [
            { label: "Details", icon: "info", onClick: () => showRules(group) },
          ] });
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {rules && <RulesPanel rules={rules} label={label} edgeActions={edgeActions} onClose={() => setRules(null)} />}
      {selected && selected.kind !== "internet" && (
        <NodePanel node={selected} onConsole={onConsole} nodeActions={nodeActions} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

/** The side panel for a clicked rule arrow: every rule it stands for, in full. */
function RulesPanel({ rules, label, edgeActions, onClose }: {
  rules: TopoEdge[]; label: (id: string) => string;
  edgeActions?: (edge: TopoEdge, close: () => void) => ReactNode; onClose: () => void;
}) {
  return (
    <div className="side-panel card">
      <div className="card-head">
        <h2>{rules.length > 1 ? `${rules.length} traffic rules` : "Traffic rule"}</h2>
        <button className="btn ghost small" onClick={onClose} aria-label="Close">✕</button>
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
            {edgeActions?.(rule, onClose)}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The side panel for a clicked node: its details, a console button for a running VM, and the caller's actions. */
function NodePanel({ node, onConsole, nodeActions, onClose }: {
  node: TopoNode; onConsole?: (host: string) => void;
  nodeActions?: (node: TopoNode, close: () => void) => ReactNode; onClose: () => void;
}) {
  return (
    <div className="side-panel card">
      <div className="card-head">
        <h2>{node.label}</h2>
        <button className="btn ghost small" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="card-body">
        <dl className="kv">
          {node.kind === "vpn" ? (
            <>
              <dt>Port</dt><dd className="mono">udp/{node.port} on the router's uplink</dd>
              {node.endpoint && <><dt>Endpoint</dt><dd className="mono">{node.endpoint}</dd></>}
              <dt>Tunnel</dt><dd className="mono">{node.cidr}</dd>
              <dt>Peers</dt><dd>{node.peers?.join(", ")}</dd>
              <dt>Reach</dt><dd>{node.reach?.join(", ")}</dd>
            </>
          ) : node.kind === "segment" ? (
            <>
              <dt>VLAN</dt><dd>{node.vlan}</dd>
              <dt>Network</dt><dd className="mono">{node.cidr}</dd>
              <dt>Gateway</dt><dd className="mono">{node.gateway}</dd>
              <dt>Internet</dt><dd>{node.internet ? "yes (public addresses only)" : "no"}</dd>
              {node.description && <><dt>About</dt><dd>{node.description}</dd></>}
            </>
          ) : (
            <>
              {node.address && <><dt>Address</dt><dd className="mono">{node.address}</dd></>}
              {node.gateways && <><dt>Gateways</dt><dd className="mono">{node.gateways.join(", ")}</dd></>}
              <dt>OS</dt><dd>{node.os || "–"}</dd>
              {node.cores !== undefined && <><dt>Size</dt><dd>{node.cores} vCPU · {node.memory} MiB · {node.disk} GiB</dd></>}
              {!!node.roles?.length && <><dt>Roles</dt><dd>{node.roles.join(", ")}</dd></>}
              <dt>VM</dt><dd>{node.vmid ? `${node.vmid} · ${node.status}` : "not built yet"}</dd>
              {node.change && <><dt>Plan</dt><dd>{CHANGE_LABEL[node.change]}</dd></>}
              {!!node.reasons?.length && <><dt>Why</dt><dd>{node.reasons.join("; ")}</dd></>}
            </>
          )}
        </dl>
        {onConsole && node.kind !== "segment" && node.kind !== "vpn" && node.status === "running" && (
          <button className="btn primary small" style={{ marginTop: 10 }}
            onClick={() => onConsole(node.id === "rtr" ? "rtr" : node.id.replace(/^host:/, ""))}>
            Open console
          </button>
        )}
        {nodeActions?.(node, onClose)}
      </div>
    </div>
  );
}
