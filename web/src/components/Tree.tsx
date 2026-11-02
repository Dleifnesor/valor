import { useEffect, useRef, useState } from "react";
import { RangeRow } from "../types";
import { go } from "../hooks";
import { Icon } from "./ui";
import { Menu, newTab, showMenu } from "./ContextMenu";
import { openConsoleWindow, rangeMenu, vmMenu } from "./RangeActions";

// The resource tree on the left, as in Proxmox: VALOR itself, its ranges and each range's VMs. Double-clicking a VM
// opens its console in a window of its own.

export type Selection = "root" | `range:${string}` | `vm:${string}/${string}`;

const rangeState = (r: RangeRow) => !r.vms ? "" : r.running === r.vms ? "ok" : r.running ? "warn" : "off";

export function ResourceTree({ ranges, instance, selected, canOperate }: {
  ranges: RangeRow[] | null; instance: string; selected: Selection | null; canOperate: boolean;
}) {
  const [closed, setClosed] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("valor-tree-closed") ?? "[]"); } catch { return []; }
  });
  const toggle = (key: string) => {
    const next = closed.includes(key) ? closed.filter((k) => k !== key) : [...closed, key];
    setClosed(next);
    try { localStorage.setItem("valor-tree-closed", JSON.stringify(next)); } catch { /* not kept */ }
  };
  const ref = useRef<HTMLDivElement>(null);
  // braces: scrollIntoView returns a Promise in newer browsers, and an effect may only return its cleanup function
  useEffect(() => { ref.current?.querySelector(".tree-row.selected")?.scrollIntoView({ block: "nearest" }); }, [selected]);
  const rootOpen = !closed.includes("root");

  return (
    <div className="tree" role="tree" aria-label="Resources" ref={ref}>
      <Row depth={0} icon="server" label={instance} selected={selected === "root"} href="dashboard"
        open={rootOpen} onToggle={() => toggle("root")} menu={{
          title: `Datacenter (${instance})`,
          items: [
            canOperate && { label: "Create range", icon: "plus", onClick: () => go("ranges/new") },
            canOperate && { label: "Chat builder", icon: "chat", onClick: () => go("builder") },
            "-",
            newTab("dashboard"),
          ],
        }} />
      {rootOpen && ranges?.map((r) => {
        const open = !closed.includes(`range:${r.name}`);
        const machines = r.machines ?? [];
        return (
          <div key={r.name} role="group">
            <Row depth={1} icon="ranges" label={r.name} state={rangeState(r)} selected={selected === `range:${r.name}`}
              href={r.has_spec ? `ranges/${r.name}` : undefined} title={r.has_spec ? r.description : "This range has VMs but no spec"}
              open={machines.length ? open : undefined} onToggle={() => toggle(`range:${r.name}`)}
              menu={r.has_spec ? rangeMenu(r, canOperate) : undefined} />
            {open && machines.map((m) => (
              <Row key={m.vmid} depth={2} icon="vm" label={`${m.vmid} (${m.host})`} state={m.status === "running" ? "ok" : "off"}
                selected={selected === `vm:${r.name}/${m.host}`} href={r.has_spec ? `ranges/${r.name}/vm/${m.host}` : undefined}
                title={`${m.host}: ${m.status}`} menu={r.has_spec ? vmMenu(r.name, m.host, m, canOperate) : undefined}
                onOpen={r.has_spec && canOperate ? () => openConsoleWindow(r.name, m.host) : undefined} />
            ))}
          </div>
        );
      })}
      {rootOpen && ranges && !ranges.length && <div className="tree-empty muted small">No ranges yet.</div>}
    </div>
  );
}

function Row({ depth, icon, label, state, selected, href, title, open, onToggle, menu, onOpen }: {
  depth: number; icon: "server" | "ranges" | "vm"; label: string; state?: string; selected: boolean; href?: string;
  title?: string; open?: boolean; onToggle?: () => void; menu?: Menu; onOpen?: () => void;
}) {
  return (
    <div className={`tree-row${selected ? " selected" : ""}${href ? "" : " disabled"}`} style={{ paddingLeft: 6 + depth * 16 }}
      role="treeitem" aria-selected={selected} aria-expanded={open} title={title}
      onClick={() => href && go(href)} onDoubleClick={onOpen} onContextMenu={(e) => menu && showMenu(e, menu)}>
      {open === undefined ? <span className="twisty" /> : (
        <button className="twisty" aria-label={open ? `Collapse ${label}` : `Expand ${label}`}
          onClick={(e) => { e.stopPropagation(); onToggle?.(); }}>
          <span className={`flip${open ? "" : " side"}`}><Icon name="caret" size={12} /></span>
        </button>
      )}
      <span className={`tree-icon ${state ?? ""}`}><Icon name={icon} size={16} /></span>
      {href ? <a href={`#/${href}`} onClick={(e) => e.stopPropagation()}>{label}</a> : <span>{label}</span>}
    </div>
  );
}
