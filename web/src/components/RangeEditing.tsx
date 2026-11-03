import { useEffect, useState } from "react";
import { ApiError, del, get, post } from "../api";
import { go, useApi, when } from "../hooks";
import { TopoNode } from "../types";
import { ErrorBox, Icon, Loading, MenuAction, MenuItems } from "./ui";
import { vmActions } from "./RangeActions";
import { TopologyMap } from "./Topology";
import { PlanModal, PlanResponse } from "./PlanModal";
import { AddSegmentModal, EditSegmentModal, RuleModal, rulesOf } from "./SegmentEditing";
import { AddServiceModal, AddVmModal, Catalog, EditVmModal, Op, View, YamlModal } from "./VmForms";
import { RangeChat } from "./RangeChat";

// Editing a built range on its map. Map controls and the chat panel both change the range's draft; the draft is
// drawn in the plan colors and built only through "Review plan" -> approve.
//
// The forms behind the map's buttons are in VmForms.tsx (VMs, services, YAML) and SegmentEditing.tsx (segments,
// traffic rules); the chat panel is RangeChat.tsx.

/** Where a draft's changes came from, as the draft bar says it. */
const SOURCE: Record<string, string> = { map: "map", yaml: "YAML", "chat:plan": "chat (plan)", "chat:code": "chat (code)" };

/** Which form is open over the map. */
type OpenModal = null | { kind: "addvm"; segment?: string } | { kind: "service" | "edit"; host: TopoNode }
  | { kind: "yaml" } | { kind: "addseg" } | { kind: "editseg"; seg: TopoNode } | { kind: "rule"; from?: string };

export function RangeMapTab({ name, canOperate, onConsole, chatId }: { name: string; canOperate: boolean; onConsole?: (host: string) => void; chatId?: number }) {
  const [view, setView] = useState<View | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [modal, setModal] = useState<OpenModal>(null);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [planning, setPlanning] = useState(false);
  const [chatOpen, setChatOpen] = useState(() => { if (chatId) return true; try { return localStorage.getItem("valor-range-chat") === "1"; } catch { return false; } });
  const catalog = useApi<Catalog>(canOperate ? "/api/catalog" : null);

  const load = () => get<View>(`/api/ranges/${name}/draft`).then(setView).catch(setErr);
  useEffect(() => { load(); const timer = setInterval(load, 20000); return () => clearInterval(timer); }, [name]);
  useEffect(() => { try { localStorage.setItem("valor-range-chat", chatOpen ? "1" : "0"); } catch { /* not kept */ } }, [chatOpen]);

  /** Send map edits; the answer is the new view (draft + map). True when they were accepted. */
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
  const segments = view.topology.nodes.filter((node) => node.kind === "segment").map((node) => node.label);
  const draft = view.draft;
  const vpn = view.topology.nodes.find((node) => node.kind === "vpn");
  const vpnReaches = (segment: string) => !!vpn?.reach?.includes(segment);
  const removeSegment = (seg: TopoNode, close: () => void) => {
    const inside = view.topology.nodes.filter((node) => node.parent === seg.id && node.change !== "remove").map((node) => node.label);
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

  // What the map can change on a node: buttons in its panel and entries in its right-click menu.
  const nodeEdits = (node: TopoNode, close: () => void): MenuAction[] => {
    if (!canOperate) return [];
    if (node.kind === "segment") return [
      { label: "Add VM here", icon: "plus", onClick: () => { setModal({ kind: "addvm", segment: node.label }); close(); } },
      { label: "Allow traffic", icon: "plus", onClick: () => { setModal({ kind: "rule", from: node.label }); close(); } },
      { label: "Edit segment", icon: "edit", onClick: () => { setModal({ kind: "editseg", seg: node }); close(); } },
      { label: "Remove", icon: "trash", danger: true, onClick: () => removeSegment(node, close) },
    ];
    if (node.kind !== "host" || node.change === "remove") return [];
    const host = node.id.slice(5);                       // node ids are "host:<name>"
    return [
      { label: "Add service", icon: "plus", onClick: () => { setModal({ kind: "service", host: node }); close(); } },
      { label: "Edit VM", icon: "edit", onClick: () => { setModal({ kind: "edit", host: node }); close(); } },
      { label: "Remove VM", icon: "trash", danger: true,
        onClick: () => { if (window.confirm(`Remove ${host} from the range? It is deleted when the plan is approved.`)) { edit([{ op: "remove_host", name: host }]); close(); } } },
    ];
  };
  const editButtons = (node: TopoNode, close: () => void) => (
    <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
      {nodeEdits(node, close).map((action) => (
        <button key={action.label} className={`btn small${action.danger ? " danger" : ""}`} onClick={action.onClick}>
          {action.icon && <Icon name={action.icon} size={13} />} {action.label}
        </button>
      ))}
    </div>
  );

  // The panel under a clicked node: a segment's traffic rules, a VM's services, then the edit buttons.
  const nodeActions = (node: TopoNode, close: () => void) => {
    if (!canOperate) return null;
    if (node.kind === "segment") {
      const rules = rulesOf(view.topology, node.label);
      return (
        <div className="node-actions">
          <div className="small muted" style={{ marginBottom: 4 }}>Traffic rules</div>
          {rules.length === 0 && <div className="small muted">None: blocked except inside the segment{node.internet ? " and to the internet" : ""}.</div>}
          {rules.map((rule) => (
            <div className="rule-row" key={rule.edge.id}>
              <span className="mono small">{rule.from} → {rule.to}</span><span className="badge">{rule.edge.label}</span><span className="grow" />
              <button className="chip-x" aria-label="Remove rule" title="Remove rule"
                onClick={() => edit([{ op: "remove_rule", index: rule.index, from: rule.from, to: rule.to }])}>×</button>
            </div>
          ))}
          {editButtons(node, close)}
        </div>
      );
    }
    if (node.kind !== "host" || node.change === "remove") return null;
    const host = node.id.slice(5);
    return (
      <div className="node-actions">
        {!!node.roles?.length && (
          <div className="service-chips">
            {node.roles.map((role) => (
              <span className="service-chip" key={role}>{role}
                <button className="chip-x" title={`Remove ${role}`} aria-label={`Remove ${role}`} onClick={() => edit([{ op: "remove_role", host, role }])}>×</button>
              </span>
            ))}
          </div>
        )}
        {editButtons(node, close)}
      </div>
    );
  };

  // Right-click on a VM or the router: its page, power and consoles (once built), then the map's edits.
  const nodeMenu = (node: TopoNode): MenuItems => {
    const host = node.kind === "router" ? "rtr" : node.kind === "host" ? node.id.slice(5) : "";
    if (!host || !node.vmid) return nodeEdits(node, () => {});
    const vm = vmActions(name, host, node.status);
    const operate: MenuItems = canOperate ? [vm.start, vm.shutdown, vm.reboot, vm.stop, "-", vm.console, vm.serial] : [];
    return [{ label: "Open VM page", icon: "vm", onClick: () => go(`ranges/${name}/vm/${host}`) }, ...operate, "-", ...nodeEdits(node, () => {})];
  };

  const closeModal = () => setModal(null);
  const submitAndClose = async (ops: Op[]) => { if (await edit(ops)) setModal(null); };

  return (
    <>
      {draft && (
        <div className={`draft-bar${draft.valid ? "" : " invalid"}`}>
          <b>Draft changes</b>
          {draft.valid ? Object.entries(draft.summary ?? {}).map(([action, count]) => <span key={action} className={`badge c-${action}`}>{count} {action}</span>)
            : <span className="error-text">{draft.error}</span>}
          <span className="muted small">from {SOURCE[draft.meta.source ?? ""] ?? draft.meta.source} by {draft.meta.by} · {when(draft.meta.updated)}</span>
          <span className="grow" />
          {canOperate && <>
            <button className="btn small" onClick={() => setModal({ kind: "yaml" })}>YAML</button>
            <button className="btn small" onClick={discard}>Discard</button>
            <button className="btn small primary" disabled={!draft.valid || planning} onClick={reviewPlan}>
              {planning ? <><span className="spinner" /> Planning…</> : "Review plan"}
            </button>
          </>}
        </div>
      )}
      <ErrorBox error={err} />
      <div className={`map-layout${chatOpen ? " with-chat" : ""}`}>
        <div className="map-main">
          <TopologyMap topology={view.topology} tall onConsole={onConsole} overlay={overlay} nodeActions={nodeActions} nodeMenu={nodeMenu}
            edgeActions={canOperate ? (edge, close) => {
              const index = Number(edge.id.split(":")[1]);         // rule edges are "rule:<index in the policy>"
              const from = edge.source.replace(/^(host|seg):/, ""), to = edge.target.replace(/^(host|seg):/, "");
              return <div className="node-actions"><button className="btn small danger"
                onClick={() => { edit([{ op: "remove_rule", index, from, to }]); close(); }}>Remove this rule</button></div>;
            } : undefined} />
        </div>
        {chatOpen && canOperate && (
          <RangeChat name={name} chatId={chatId} onView={setView} onReview={reviewPlan} onClose={() => setChatOpen(false)} />
        )}
      </div>
      {modal?.kind === "addvm" && catalog.data && (
        <AddVmModal segments={segments} segment={modal.segment} catalog={catalog.data} onClose={closeModal} onSubmit={submitAndClose} />
      )}
      {modal?.kind === "service" && catalog.data && (
        <AddServiceModal host={modal.host} segments={segments} catalog={catalog.data} onClose={closeModal} onSubmit={submitAndClose} />
      )}
      {modal?.kind === "edit" && catalog.data && (
        <EditVmModal host={modal.host} catalog={catalog.data} onClose={closeModal} onSubmit={submitAndClose} />
      )}
      {modal?.kind === "addseg" && <AddSegmentModal name={name} topo={view.topology} hasVpn={!!vpn} onClose={closeModal} onSubmit={submitAndClose} />}
      {modal?.kind === "editseg" && <EditSegmentModal seg={modal.seg} hasVpn={!!vpn} vpnReaches={vpnReaches(modal.seg.label)}
        onClose={closeModal} onSubmit={submitAndClose} />}
      {modal?.kind === "rule" && <RuleModal topo={view.topology} from={modal.from} onClose={closeModal} onSubmit={submitAndClose} />}
      {modal?.kind === "yaml" && draft && <YamlModal name={name} yaml={draft.yaml} onClose={closeModal} onSaved={(saved) => { setView(saved); setModal(null); }} />}
      {plan && <PlanModal name={name} res={plan} draft onClose={() => { setPlan(null); load(); }} />}
    </>
  );
}
