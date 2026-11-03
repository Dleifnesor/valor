import { useEffect, useRef, useState } from "react";
import { RangeRow } from "../types";
import { go } from "../hooks";
import { Icon } from "./ui";
import { Menu, newTab, showMenu } from "./ContextMenu";
import { openConsoleWindow, rangeMenu, vmMenu } from "./RangeActions";

// The resource tree on the left, as in Proxmox: VALOR itself, its ranges and each range's VMs. Double-clicking a VM
// opens its console in a window of its own.

export type Selection = "root" | `range:${string}` | `vm:${string}/${string}`;

const rangeState = (range: RangeRow) => !range.vms ? "" : range.running === range.vms ? "ok" : range.running ? "warn" : "off";

export function ResourceTree({ ranges, instance, selected, canOperate }: {
  ranges: RangeRow[] | null; instance: string; selected: Selection | null; canOperate: boolean;
}) {
  const [closed, setClosed] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("valor-tree-closed") ?? "[]"); } catch { return []; }
  });
  const toggle = (key: string) => {
    const next = closed.includes(key) ? closed.filter((other) => other !== key) : [...closed, key];
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
      {rootOpen && ranges?.map((range) => {
        const open = !closed.includes(`range:${range.name}`);
        const machines = range.machines ?? [];
        return (
          <div key={range.name} role="group">
            <Row depth={1} icon="ranges" label={range.name} state={rangeState(range)} selected={selected === `range:${range.name}`}
              href={range.has_spec ? `ranges/${range.name}` : undefined} title={range.has_spec ? range.description : "This range has VMs but no spec"}
              open={machines.length ? open : undefined} onToggle={() => toggle(`range:${range.name}`)}
              menu={range.has_spec ? rangeMenu(range, canOperate) : undefined} />
            {open && machines.map((machine) => (
              <Row key={machine.vmid} depth={2} icon="vm" label={`${machine.vmid} (${machine.host})`} state={machine.status === "running" ? "ok" : "off"}
                selected={selected === `vm:${range.name}/${machine.host}`} href={range.has_spec ? `ranges/${range.name}/vm/${machine.host}` : undefined}
                title={`${machine.host}: ${machine.status}`} menu={range.has_spec ? vmMenu(range.name, machine.host, machine, canOperate) : undefined}
                onOpen={range.has_spec && canOperate ? () => openConsoleWindow(range.name, machine.host) : undefined} />
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
