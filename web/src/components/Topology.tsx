import { useMemo, useState } from "react";
import {
  Background, Controls, Edge, Handle, MarkerType, Node, NodeProps, Position, ReactFlow,
} from "@xyflow/react";
import { Change, TopoNode, Topology as Topo } from "../types";

// View-only map of a range. Layout is computed here (no dragging, no editing): Internet on top, the range router
// below it, one box per segment with its hosts inside, and the allowed flows between them as dashed arrows.
// With a plan, every VM gets a color for what the plan will do to it.

const SEG_W = 250;
const SEG_GAP = 64;
const HEAD_H = 62;
const HOST_H = 84;
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

const nodeTypes = { host: HostNode, router: RouterNode, internet: InternetNode, segment: SegmentNode };

function layout(topo: Topo): { nodes: Node<Data>[]; edges: Edge[] } {
  const segs = topo.nodes.filter((n) => n.kind === "segment");
  const hosts = topo.nodes.filter((n) => n.kind === "host");
  const total = segs.length * SEG_W + Math.max(0, segs.length - 1) * SEG_GAP;
  const nodes: Node<Data>[] = [];
  const abs: Record<string, { x: number; y: number; w: number }> = {};

  const inet = topo.nodes.find((n) => n.kind === "internet");
  if (inet) {
    nodes.push({ id: inet.id, type: "internet", position: { x: -INET_W / 2, y: 0 }, data: { raw: inet },
      style: { width: INET_W, height: 44 } });
    abs[inet.id] = { x: -INET_W / 2, y: 0, w: INET_W };
  }
  const rtr = topo.nodes.find((n) => n.kind === "router");
  if (rtr) {
    nodes.push({ id: rtr.id, type: "router", position: { x: -ROUTER_W / 2, y: 110 }, data: { raw: rtr },
      style: { width: ROUTER_W, height: 74 } });
    abs[rtr.id] = { x: -ROUTER_W / 2, y: 110, w: ROUTER_W };
  }
  segs.forEach((s, i) => {
    const members = hosts.filter((h) => h.parent === s.id);
    const height = HEAD_H + Math.max(1, members.length) * (HOST_H + HOST_GAP) + PAD - HOST_GAP + 6;
    const x = -total / 2 + i * (SEG_W + SEG_GAP);
    nodes.push({ id: s.id, type: "segment", position: { x, y: SEG_Y }, data: { raw: s },
      style: { width: SEG_W, height }, selectable: true });
    abs[s.id] = { x, y: SEG_Y, w: SEG_W };
    members.forEach((h, j) => {
      const rel = { x: PAD, y: HEAD_H + j * (HOST_H + HOST_GAP) };
      nodes.push({ id: h.id, type: "host", parentId: s.id, extent: "parent", position: rel, data: { raw: h },
        style: { width: SEG_W - 2 * PAD, height: HOST_H } });
      abs[h.id] = { x: x + rel.x, y: SEG_Y + rel.y, w: SEG_W - 2 * PAD };
    });
  });
  hosts.filter((h) => !h.parent).forEach((h, j) => {          // VMs a plan removes
    const pos = { x: total / 2 + SEG_GAP, y: SEG_Y + j * (HOST_H + HOST_GAP) };
    nodes.push({ id: h.id, type: "host", position: pos, data: { raw: h }, style: { width: SEG_W - 2 * PAD, height: HOST_H } });
    abs[h.id] = { ...pos, w: SEG_W - 2 * PAD };
  });

  const edges: Edge[] = [];
  for (const e of topo.edges) {
    if (e.kind === "egress" || !abs[e.source] || !abs[e.target]) continue;   // internet access is a badge on the segment
    if (e.kind === "uplink" || e.kind === "gateway") {
      edges.push({ id: e.id, source: e.source, target: e.target, sourceHandle: "b", targetHandle: "t",
        type: e.kind === "gateway" ? "smoothstep" : "straight", label: e.kind === "gateway" ? e.label : undefined,
        style: { strokeWidth: 2, stroke: "var(--border-strong)" }, labelBgPadding: [4, 2], labelBgBorderRadius: 4,
        labelStyle: { fontFamily: "var(--mono)", fontSize: 11, fill: "var(--muted)" },
        labelBgStyle: { fill: "var(--surface)" } });
      continue;
    }
    const s = abs[e.source], t = abs[e.target];
    const leftToRight = s.x < t.x;
    edges.push({
      id: e.id, source: e.source, target: e.target, type: "default", animated: true,
      sourceHandle: leftToRight ? "rs" : "ls", targetHandle: leftToRight ? "lt" : "rt",
      label: e.label, data: { description: e.description },
      style: { stroke: "var(--accent)", strokeWidth: 1.8, strokeDasharray: "6 4" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)", width: 16, height: 16 },
      labelStyle: { fontFamily: "var(--mono)", fontSize: 11, fill: "var(--accent-strong)", fontWeight: 600 },
      labelBgStyle: { fill: "var(--surface)" }, labelBgPadding: [4, 2], labelBgBorderRadius: 4,
    });
  }
  return { nodes, edges };
}

function colorMode(): "light" | "dark" {
  const t = document.documentElement.dataset.theme;
  if (t === "light" || t === "dark") return t;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function TopologyMap({ topology, tall }: { topology: Topo; tall?: boolean }) {
  const { nodes, edges } = useMemo(() => layout(topology), [topology]);
  const [selected, setSelected] = useState<TopoNode | null>(null);
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
      <ReactFlow
        key={key}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode={colorMode()}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        elementsSelectable
        minZoom={0.2}
        maxZoom={1.75}
        onNodeClick={(_, n) => setSelected((n.data as Data).raw)}
        onPaneClick={() => setSelected(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {selected && selected.kind !== "internet" && (
        <div className="side-panel card">
          <div className="card-head">
            <h2>{selected.label}</h2>
            <button className="btn ghost small" onClick={() => setSelected(null)} aria-label="Close">✕</button>
          </div>
          <div className="card-body">
            <dl className="kv">
              {selected.kind === "segment" ? (
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
          </div>
        </div>
      )}
    </div>
  );
}
