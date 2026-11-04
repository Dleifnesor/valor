// *draws a pretty network map with crayons* OwO web/src/components/Topology.tsx is the RANGE MAP! ^w^
// A view-only map: the Internet on top, the range router below it, one box per segment with its hosts inside, and the
// allowed traffic between them as dashed arrows. VALOR computes the layout itself (no dragging). With a plan, every
// VM gets a color for what the plan will do to it. Clicking opens side panels; right-clicking opens menus *wags tail*
import { ReactNode, useMemo, useState } from "react";
// React Flow (@xyflow/react) draws the nodes and edges; we give it positions and custom node/edge components UwU
import {
  Background, BaseEdge, Controls, Edge, EdgeLabelRenderer, EdgeProps, Handle, MarkerType, Node, NodeProps, Position, ReactFlow,
  getBezierPath, getSmoothStepPath,
} from "@xyflow/react";
// The map types, menu items, and showMenu >w<
import { Change, TopoEdge, TopoNode, Topology as Topo } from "../types";
import { MenuItems } from "./ui";
import { showMenu } from "./ContextMenu";

// View-only map of a range. Layout is computed here (no dragging, no editing): Internet on top, the range router
// below it, one box per segment with its hosts inside, and the allowed flows between them as dashed arrows.
// With a plan, every VM gets a color for what the plan will do to it.

// Layout sizes in pixels: segment width, the gap between segments (widened to fit rule labels, up to MAX_GAP),
// label height, segment header height, host card base height, one row of role chips, the gap between hosts, inner
// padding, router and Internet widths, and where the segment row starts OwO
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

// CHANGE_LABEL: the words for each plan change ^w^
export const CHANGE_LABEL: Record<Change, string> = {
  create: "added", update: "changed", replace: "rebuilt", remove: "removed", keep: "unchanged",
};

/** What every React Flow node carries: the topology node it draws. */
// Data: what every React Flow node carries: the topology node it draws UwU
type Data = { raw: TopoNode };

// Invisible connection points on all four sides: edges attach where the layout says, users can't connect anything.
// hidden: invisible connection points (the layout chooses where edges attach; nobody can draw new edges) >w<
const hidden = { opacity: 0, width: 6, height: 6, minWidth: 0, minHeight: 0, border: 0 };

// Handles: one invisible handle on each side (top/bottom/left/right; in and out) OwO
function Handles() {
  return (
    <>
      {/* Top in, bottom out, and both directions on the left and right */}
      <Handle type="target" position={Position.Top} id="t" style={hidden} />
      <Handle type="source" position={Position.Bottom} id="b" style={hidden} />
      <Handle type="source" position={Position.Right} id="rs" style={hidden} />
      <Handle type="target" position={Position.Right} id="rt" style={hidden} />
      <Handle type="source" position={Position.Left} id="ls" style={hidden} />
      <Handle type="target" position={Position.Left} id="lt" style={hidden} />
    </>
  );
}

// statusDot(): a green (running) / red (stopped) dot ^w^
function statusDot(status?: string) {
  if (!status) return null;
  return <span className={`dot ${status === "running" ? "ok" : status === "stopped" ? "bad" : ""}`} title={status} />;
}

// HostNode: a VM card: state dot, name, change badge, address and OS, and role chips UwU
function HostNode({ data }: NodeProps<Node<Data>>) {
  const node = data.raw;
  return (
    <div className={`tnode${node.change && node.change !== "keep" ? ` change-${node.change}` : ""}`}>
      {/* Handles, title (with the change badge), address and OS, roles */}
      <Handles />
      <div className="t-title">{statusDot(node.status)}{node.label}
        {node.change && node.change !== "keep" && <span className={`badge`} style={{ marginLeft: "auto", color: `var(--c-${node.change})` }}>{CHANGE_LABEL[node.change]}</span>}
      </div>
      <div className="t-sub">{node.address} · {node.os}</div>
      {!!node.roles?.length && <div className="chips">{node.roles.map((role) => <span className="chip" key={role}>{role}</span>)}</div>}
    </div>
  );
}

// RouterNode: the router card >w<
function RouterNode({ data }: NodeProps<Node<Data>>) {
  const node = data.raw;
  return (
    <div className={`tnode router${node.change && node.change !== "keep" ? ` change-${node.change}` : ""}`}>
      {/* Handles, name, what it does, and its OS */}
      <Handles />
      <div className="t-title">{statusDot(node.status)}{node.label}</div>
      <div className="t-sub">router · NAT · firewall</div>
      <div className="t-sub">{node.os}</div>
    </div>
  );
}

// VpnNode: the WireGuard card: port, number of peers, reachable segments OwO
function VpnNode({ data }: NodeProps<Node<Data>>) {
  const node = data.raw;
  return (
    <div className="tnode vpn">
      {/* Handles and the VPN's facts */}
      <Handles />
      <div className="t-title">WireGuard</div>
      <div className="t-sub">udp/{node.port} · {node.peers?.length ?? 0} peer{node.peers?.length === 1 ? "" : "s"}</div>
      <div className="t-sub">reaches {node.reach?.join(", ")}</div>
    </div>
  );
}

// InternetNode: the Internet bubble ^w^
function InternetNode() {
  return (
    <div className="tnode internet">
      {/* Handles, then the word */}
      <Handles />
      Internet
    </div>
  );
}

// SegmentNode: a segment's box (its hosts are separate nodes placed inside it): name, VLAN, internet badge, network
// and gateway UwU
function SegmentNode({ data }: NodeProps<Node<Data>>) {
  const segment = data.raw;
  return (
    <div className="tgroup">
      {/* Handles and the header */}
      <Handles />
      <div className="g-head">
        {/* Name, VLAN, internet badge; then the network and gateway */}
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

// nodeTypes: which component draws each node kind >w<
const nodeTypes = { host: HostNode, router: RouterNode, internet: InternetNode, segment: SegmentNode, vpn: VpnNode };

// Edge labels are HTML above the nodes, so a segment box or another line never covers them.
// LabelData: where to put an edge's label, and what clicking it does OwO
type LabelData = { lx?: number; ly?: number; onSelect?: () => void };

// EdgeLabel: an edge label drawn as HTML above everything ^w^
function EdgeLabel({ x, y, text, className, onSelect }: { x: number; y: number; text: ReactNode; className: string; onSelect?: () => void }) {
  return (
    <EdgeLabelRenderer>
      <div className={`edge-label ${className}${onSelect ? " clickable" : ""}`} onClick={onSelect}
        style={{ transform: `translate(-50%, -50%) translate(${x}px, ${y}px)` }}>{text}</div>
    </EdgeLabelRenderer>
  );
}

// RuleEdge: a traffic-rule arrow (a bezier curve) with its label at the placed spot (or the curve's middle) UwU
function RuleEdge(props: EdgeProps<Edge<LabelData>>) {
  const [path, midX, midY] = getBezierPath(props);
  return (
    <>
      {/* The line, then the clickable label */}
      <BaseEdge id={props.id} path={path} style={props.style} markerEnd={props.markerEnd} markerStart={props.markerStart} interactionWidth={14} />
      {props.label && <EdgeLabel x={props.data?.lx ?? midX} y={props.data?.ly ?? midY} text={props.label} className="rule" onSelect={props.data?.onSelect} />}
    </>
  );
}

// GatewayEdge: router-to-segment lines (right-angled steps) labeled with the gateway address >w<
function GatewayEdge(props: EdgeProps<Edge<LabelData>>) {
  const [path, midX, midY] = getSmoothStepPath(props);
  return (
    <>
      {/* The line and its label */}
      <BaseEdge id={props.id} path={path} style={props.style} />
      {props.label && <EdgeLabel x={midX} y={midY} text={props.label} className="gateway" />}
    </>
  );
}

// edgeTypes: which component draws each custom edge kind OwO
const edgeTypes = { rule: RuleEdge, gateway: GatewayEdge };

// Where the label of a rule between two columns goes: in a gap between segments, never over a segment box. The
// curve is React Flow's bezier from side handle to side handle (both control points at the middle x), so the label
// sits on the line at the gap's centre.
// bezierYAt(): the curve's height at a given x. React Flow's side-to-side bezier has both control points at the
// middle x; we find the curve parameter t for that x by bisection (30 halvings), then compute y ^w^
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

// labelWidth(): a label's width at 11px monospace (~7px per char) plus padding UwU
const labelWidth = (text: string) => text.length * 7 + 14;      // 11px monospace plus the label's padding
const VPN_W = 190;

// Height of a host card: its role chips wrap, so count the rows they need at the card's width.
// hostHeight(): a host card's height: base height plus one row per line of wrapped role chips >w<
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
// shortLabel(): long port lists become "tcp 80,8080,+5" (the side panel shows the full rule) OwO
function shortLabel(label?: string): string | undefined {
  if (!label || label.length <= 20) return label;
  const [proto, ports = ""] = label.split(" ", 2);
  const list = ports.split(",");
  return list.length > 2 ? `${proto} ${list.slice(0, 2).join(",")},+${list.length - 2}` : `${label.slice(0, 18)}…`;
}

// ---- Layout ----------------------------------------------------------------------------------------------------

// Box: a node's absolute position and size ^w^
type Box = { x: number; y: number; w: number; h: number };

/** Rules between the same two endpoints (either direction) share one curve: they are drawn as one arrow. */
// ruleGroups(): rules between the same two endpoints (either direction) share ONE arrow UwU
function ruleGroups(topo: Topo): Map<string, TopoEdge[]> {
  const groups = new Map<string, TopoEdge[]>();
  for (const edge of topo.edges) {
    if (edge.kind !== "policy") continue;
    const key = [edge.source, edge.target].sort().join("|");
    groups.set(key, [...(groups.get(key) ?? []), edge]);
  }
  return groups;
}

// ruleLabel(): "N rules" for a group, or the single rule's short label >w<
const ruleLabel = (group: TopoEdge[]) => (group.length > 1 ? `${group.length} rules` : shortLabel(group[0].label) ?? "");

/** Positions every node: Internet, router and VPN on top, one column per segment with its hosts stacked inside,
 *  and VMs a plan removes in an extra column on the right. `boxes` gets every node's absolute box (React Flow
 *  positions hosts relative to their segment), which the edge labels need. */
// placeNodes(): position every node (see the comment above); `boxes` collects absolute boxes for the labels OwO
function placeNodes(topo: Topo, colX: (i: number) => number, boxes: Record<string, Box>): Node<Data>[] {
  const segments = topo.nodes.filter((node) => node.kind === "segment");
  const hosts = topo.nodes.filter((node) => node.kind === "host");
  const nodes: Node<Data>[] = [];
  // fixed(): add a node at a fixed box ^w^
  const fixed = (node: TopoNode | undefined, type: string, box: Box) => {
    if (!node) return;
    nodes.push({ id: node.id, type, position: { x: box.x, y: box.y }, data: { raw: node }, style: { width: box.w, height: box.h } });
    boxes[node.id] = box;
  };
  // Internet, router and VPN on top UwU
  fixed(topo.nodes.find((node) => node.kind === "internet"), "internet", { x: -INET_W / 2, y: 0, w: INET_W, h: 44 });
  fixed(topo.nodes.find((node) => node.kind === "router"), "router", { x: -ROUTER_W / 2, y: 110, w: ROUTER_W, h: 74 });
  fixed(topo.nodes.find((node) => node.kind === "vpn"), "vpn", { x: ROUTER_W / 2 + 90, y: 110, w: VPN_W, h: 74 });

  // One column per segment; its height fits its stacked hosts >w<
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
    // Hosts stacked inside their segment (positions relative to the segment, as React Flow wants) OwO
    members.forEach((host, j) => {
      const relative = { x: PAD, y };
      nodes.push({ id: host.id, type: "host", parentId: segment.id, extent: "parent", position: relative, data: { raw: host },
        style: { width: SEG_W - 2 * PAD, height: heights[j] } });
      boxes[host.id] = { x: x + relative.x, y: SEG_Y + relative.y, w: SEG_W - 2 * PAD, h: heights[j] };
      y += heights[j] + HOST_GAP;
    });
  });

  // VMs a plan removes (no segment any more) go in an extra column on the right ^w^
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
// labelPlacer(): returns a function that picks a free spot for each rule's label: in the most central gap the
// curve crosses that's still free, otherwise stepped below the labels already there UwU
function labelPlacer(topo: Topo, boxes: Record<string, Box>, colX: (i: number) => number, gap: number) {
  const segments = topo.nodes.filter((node) => node.kind === "segment");
  // column(): which column an endpoint is in (a segment, or a host's segment; removed VMs = the extra column) >w<
  const column = (id: string) => {
    const node = topo.nodes.find((x) => x.id === id);
    const segmentId = node?.kind === "segment" ? node.id : node?.parent;
    const i = segments.findIndex((segment) => segment.id === segmentId);
    return i < 0 ? segments.length : i;
  };
  // placed = labels so far; free() = no overlap with any of them OwO
  const placed: { x: number; y: number; w: number }[] = [];
  const free = (x: number, y: number, w: number) =>
    placed.every((other) => Math.abs(other.x - x) * 2 >= other.w + w || Math.abs(other.y - y) >= LABEL_H + 4);
  return (edge: TopoEdge, text: string): { lx?: number; ly?: number } => {
    // Same column: let React Flow put it at the midpoint ^w^
    const source = boxes[edge.source], target = boxes[edge.target];
    const a = column(edge.source), b = column(edge.target);
    if (a === b) return {};                                          // same column: React Flow's own midpoint
    // From the right side of the left box to the left side of the right box UwU
    const right = a < b;
    const sx = right ? source.x + source.w : source.x, tx = right ? target.x : target.x + target.w;
    const sy = source.y + source.h / 2, ty = target.y + target.h / 2;
    // The gaps between the two columns, most central first >w<
    const lo = Math.min(a, b), hi = Math.max(a, b), mid = (lo + hi - 1) / 2;
    const gaps = Array.from({ length: hi - lo }, (_, k) => lo + k).sort((p, q) => Math.abs(p - mid) - Math.abs(q - mid));
    const w = labelWidth(text);
    // Candidate spots on the curve in each gap; the first free one wins OwO
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

// layout(): the whole map: nodes and edges ^w^
function layout(topo: Topo, onRules: (group: TopoEdge[]) => void): { nodes: Node<Data>[]; edges: Edge[] } {
  const segmentCount = topo.nodes.filter((node) => node.kind === "segment").length;
  const groups = ruleGroups(topo);
  // the gaps between segments are wide enough for the longest label plus an arrow on each side
  // The gap fits the widest label plus arrows; colX() = a column's x, centered UwU
  const widest = Math.max(0, ...[...groups.values()].map((group) => labelWidth(ruleLabel(group))));
  const gap = Math.min(MAX_GAP, Math.max(SEG_GAP, widest + 44));
  const total = segmentCount * SEG_W + Math.max(0, segmentCount - 1) * gap;
  const colX = (i: number) => -total / 2 + i * (SEG_W + gap);

  // Place the nodes and get a label placer >w<
  const boxes: Record<string, Box> = {};
  const nodes = placeNodes(topo, colX, boxes);
  const labelAt = labelPlacer(topo, boxes, colX, gap);

  // Edges by kind OwO
  const edges: Edge[] = [];
  for (const edge of topo.edges) {
    if (edge.kind === "egress" || !boxes[edge.source] || !boxes[edge.target]) continue;   // internet access is a badge on the segment
    // VPN: a green dashed animated line ^w^
    if (edge.kind === "vpn") {
      edges.push({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: "ls", targetHandle: "rt", type: "straight",
        label: edge.label, animated: true, style: { strokeWidth: 2, stroke: "var(--ok)", strokeDasharray: "4 3" },
        labelStyle: { fontFamily: "var(--mono)", fontSize: 11, fill: "var(--muted)" },
        labelBgStyle: { fill: "var(--surface)" }, labelBgPadding: [4, 2], labelBgBorderRadius: 4 });
      continue;
    }
    // Uplink and gateways: solid lines from bottom to top UwU
    if (edge.kind === "uplink" || edge.kind === "gateway") {
      edges.push({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: "b", targetHandle: "t",
        type: edge.kind === "gateway" ? "gateway" : "straight", label: edge.kind === "gateway" ? edge.label : undefined,
        style: { strokeWidth: 2, stroke: "var(--border-strong)" } });
      continue;
    }
    // a traffic rule: only the first rule of a group draws the (shared) arrow
    // Traffic rules: one arrow per group, left/right handles by position, arrowheads on both ends for two-way groups,
    // the label at its placed spot, and a click opens the rules panel >w<
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

// colorMode(): light or dark, as the app's theme says (or the system's) OwO
function colorMode(): "light" | "dark" {
  const theme = document.documentElement.dataset.theme;
  if (theme === "light" || theme === "dark") return theme;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// ---- The map -----------------------------------------------------------------------------------------------------

// TopologyMap: the map component ^w^
//   topology  what to draw; tall = a taller map; onConsole = open a VM's console
//   overlay   buttons drawn over the map; nodeActions / edgeActions = extra buttons in the side panels
//   nodeMenu  extra right-click menu entries for a node
export function TopologyMap({ topology, tall, onConsole, overlay, nodeActions, edgeActions, nodeMenu }: {
  topology: Topo; tall?: boolean; onConsole?: (host: string) => void;
  overlay?: ReactNode;                                   // e.g. editing / chat buttons over the map
  nodeActions?: (node: TopoNode, close: () => void) => ReactNode;   // buttons in the selected node's panel
  edgeActions?: (edge: TopoEdge, close: () => void) => ReactNode;   // buttons for a selected traffic rule
  nodeMenu?: (node: TopoNode) => MenuItems;              // a node's right-click menu, after "Details"
}) {
  // The selected node or rule group (each opens its side panel) UwU
  const [selected, setSelected] = useState<TopoNode | null>(null);
  const [rules, setRules] = useState<TopoEdge[] | null>(null);
  const showRules = (group: TopoEdge[]) => { setRules(group); setSelected(null); };
  const showNode = (node: TopoNode) => { setSelected(node); setRules(null); };
  // The layout is only recomputed when the topology changes (useMemo) >w<
  const { nodes, edges } = useMemo(() => layout(topology, showRules), [topology]);
  // label(): an id's readable name OwO
  const label = (id: string) => topology.nodes.find((node) => node.id === id)?.label ?? id.replace(/^(host|seg):/, "");
  // a new key remounts React Flow, so fitView runs again when nodes appear, go or change state
  const key = useMemo(() => topology.nodes.map((node) => node.id + (node.change ?? "")).join("|"), [topology]);
  const changes = topology.changes;

  // The map ^w^
  return (
    <div className={`map${tall ? " tall" : ""}`}>
      {/* With a plan: the color legend with counts */}
      {changes && (
        <div className="legend" aria-label="Legend">
          {(["create", "update", "replace", "remove", "keep"] as Change[]).map((change) => (
            <span key={change}><i style={{ background: `var(--c-${change})` }} />{CHANGE_LABEL[change]} {changes[change] ?? 0}</span>
          ))}
        </div>
      )}
      {/* The caller's overlay, then React Flow itself */}
      {overlay}
      {/* View-only: no dragging or connecting; zoom 0.2-1.75; clicks open panels; right-clicks open menus */}
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
        {/* A dotted background and the zoom controls */}
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {/* The rules panel or the node panel */}
      {rules && <RulesPanel rules={rules} label={label} edgeActions={edgeActions} onClose={() => setRules(null)} />}
      {selected && selected.kind !== "internet" && (
        <NodePanel node={selected} onConsole={onConsole} nodeActions={nodeActions} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

/** The side panel for a clicked rule arrow: every rule it stands for, in full. */
// RulesPanel: every rule a clicked arrow stands for, in full UwU
function RulesPanel({ rules, label, edgeActions, onClose }: {
  rules: TopoEdge[]; label: (id: string) => string;
  edgeActions?: (edge: TopoEdge, close: () => void) => ReactNode; onClose: () => void;
}) {
  return (
    <div className="side-panel card">
      {/* Title and close */}
      <div className="card-head">
        <h2>{rules.length > 1 ? `${rules.length} traffic rules` : "Traffic rule"}</h2>
        <button className="btn ghost small" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {/* One block per rule: from, to, what it allows, why, and the caller's buttons */}
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
// NodePanel: a clicked node's details, a console button for a running VM, and the caller's buttons >w<
function NodePanel({ node, onConsole, nodeActions, onClose }: {
  node: TopoNode; onConsole?: (host: string) => void;
  nodeActions?: (node: TopoNode, close: () => void) => ReactNode; onClose: () => void;
}) {
  return (
    <div className="side-panel card">
      {/* Title and close */}
      <div className="card-head">
        <h2>{node.label}</h2>
        <button className="btn ghost small" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="card-body">
        {/* The facts depend on the kind: VPN, segment, or host/router */}
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
        {/* Open console (running VMs only), then the caller's buttons */}
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
