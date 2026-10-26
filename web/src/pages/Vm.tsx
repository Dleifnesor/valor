import { Suspense, lazy, useState } from "react";
import { ApiError } from "../api";
import { useApi } from "../hooks";
import { Card, DropButton, ErrorBox, Loading, StateBadge, ToolButton, View } from "../components/ui";
import { vmActions } from "../components/RangeActions";
import { Detail, machinesOf } from "./RangeDetail";

// The console page carries noVNC and xterm.js: loaded only when a console is opened.
const ConsolePage = lazy(() => import("./Console").then((m) => ({ default: m.ConsolePage })));

/** One VM of a range, as Proxmox shows a virtual machine: Summary and Console. */
export function VmView({ range, host, console, canOperate }: {
  range: string; host: string; console: "vnc" | "serial" | null; canOperate: boolean;
}) {
  const { data, error, loading } = useApi<Detail>(`/api/ranges/${encodeURIComponent(range)}`, 15000);
  const [err, setErr] = useState<ApiError | null>(null);
  const base = `ranges/${range}/vm/${host}`;
  const menu = [
    { id: "summary", label: "Summary", icon: "info" as const, href: base },
    ...(canOperate ? [{ id: "console", label: "Console", icon: "terminal" as const, href: `${base}/console` }] : []),
  ];
  const active = console && canOperate ? "console" : "summary";
  const vm = data?.vms[host];
  const title = vm ? `Virtual Machine ${vm.vmid} (${host}) on range '${range}'` : `Host '${host}' on range '${range}'`;
  if (!data) return <View title={title} menu={menu} active={active}>{loading ? <Loading /> : <ErrorBox error={error} />}</View>;

  const spec = machinesOf(data).find((h) => h.name === host);
  const node = data.topology.nodes.find((n) => n.id === (host === "rtr" ? "rtr" : `host:${host}`));
  const on = vm?.status === "running";
  const a = vmActions(range, host, vm?.status, setErr);
  const tools = canOperate && vm && (
    <>
      {a.start && <ToolButton action={a.start} />}
      <DropButton label="Shutdown" icon="power" disabled={!on} items={[a.shutdown, a.reboot, a.stop]} />
      <DropButton label="Console" icon="terminal" disabled={!on} items={[a.console, a.serial]} />
    </>
  );

  return (
    <View title={title} tools={tools} menu={menu} active={active} fill={active === "console"}>
      <ErrorBox error={err} />
      {!spec && <div className="alert warn">The spec of {range} has no host named {host}.</div>}
      {active === "console" ? (
        on ? (
          <Suspense fallback={<Loading label="Loading the console…" />}>
            <ConsolePage range={range} host={host} kind={console === "serial" ? "serial" : "vnc"} />
          </Suspense>
        ) : <div className="alert info">{vm ? `${host} is ${vm.status}. Start it to open its console.` : `${host} is not built yet.`}</div>
      ) : spec && (
        <div className="summary-row two">
          <Card title="Status">
            <dl className="kv">
              <dt>Status</dt><dd>{vm ? <StateBadge state={on ? "ok" : "unknown"} label={vm.status} /> : "not built"}</dd>
              <dt>VMID</dt><dd>{vm?.vmid ?? "–"}</dd>
              <dt>Range</dt><dd><a href={`#/ranges/${range}`}>{range}</a></dd>
              <dt>Segment</dt><dd>{spec.segment}</dd>
              <dt>Address</dt><dd className="mono">{spec.address}</dd>
              {node?.gateways && <><dt>Gateways</dt><dd className="mono">{node.gateways.join(", ")}</dd></>}
              <dt>OS</dt><dd>{spec.os}</dd>
              <dt>Size</dt><dd>{spec.cores} vCPU · {spec.memory} MiB memory · {spec.disk} GiB disk</dd>
              {vm && <><dt>Configured</dt><dd>{vm.converged ? "yes, matches the spec" : "not yet (the next build finishes it)"}</dd></>}
            </dl>
          </Card>
          <Card title="Services">
            {(spec.roles ?? []).length ? (
              <ul className="plain-list">{spec.roles!.map((r) => <li key={r.name}>{r.name}</li>)}</ul>
            ) : <div className="muted">No services.</div>}
          </Card>
        </div>
      )}
    </View>
  );
}
