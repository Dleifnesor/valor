// *zooms into one VM* OwO web/src/pages/Vm.tsx: one VM's page, and the pop-out console window! ^w^
// VmView = a VM inside a range, like Proxmox shows a virtual machine: a Summary tab and a Console tab. ConsoleWindow =
// the console in a browser window of its own (double-click a VM in the tree) *wags tail*
import { ReactNode, Suspense, lazy, useEffect, useState } from "react";
// post/ApiError, useApi, shared UI pieces, the VM actions (start, shutdown, console...), and the range detail types UwU
import { ApiError, post } from "../api";
import { useApi } from "../hooks";
import { Card, DropButton, ErrorBox, Loading, StateBadge, ToolButton, View } from "../components/ui";
import { vmActions } from "../components/RangeActions";
import { Detail, machinesOf } from "./RangeDetail";

// The console page carries noVNC and xterm.js: loaded only when a console is opened.
// lazy(): the Console page (with the big noVNC and xterm.js libraries) is only downloaded when a console is opened >w<
const ConsolePage = lazy(() => import("./Console").then((module) => ({ default: module.ConsolePage })));

/** One VM of a range, as Proxmox shows a virtual machine: Summary and Console. */
// VmView: `range` + `host` say which VM; `console` = which console tab is open (or none); canOperate = may use it OwO
export function VmView({ range, host, console, canOperate }: {
  range: string; host: string; console: "vnc" | "serial" | null; canOperate: boolean;
}) {
  // The range's details (refreshed every 15 s) and the last error ^w^
  const { data, error, loading } = useApi<Detail>(`/api/ranges/${encodeURIComponent(range)}`, 15000);
  const [err, setErr] = useState<ApiError | null>(null);
  const base = `ranges/${range}/vm/${host}`;
  // The side menu: Summary, and Console for operators UwU
  const menu = [
    { id: "summary", label: "Summary", icon: "info" as const, href: base },
    ...(canOperate ? [{ id: "console", label: "Console", icon: "terminal" as const, href: `${base}/console` }] : []),
  ];
  // Which tab is active, this VM's live state, and the page title >w<
  const active = console && canOperate ? "console" : "summary";
  const vm = data?.vms[host];
  const title = vm ? `Virtual Machine ${vm.vmid} (${host}) on range '${range}'` : `Host '${host}' on range '${range}'`;
  // Not loaded yet: just the frame with a spinner or the error OwO
  if (!data) return <View title={title} menu={menu} active={active}>{loading ? <Loading /> : <ErrorBox error={error} />}</View>;

  // The VM as the spec describes it, and its map node (the router's node is "rtr", hosts are "host:<name>") ^w^
  const spec = machinesOf(data).find((machine) => machine.name === host);
  const node = data.topology.nodes.find((candidate) => candidate.id === (host === "rtr" ? "rtr" : `host:${host}`));
  // The toolbar (operators, built VMs): Start, a Shutdown menu (shutdown/reboot/stop) and a Console menu UwU
  const on = vm?.status === "running";
  const actions = vmActions(range, host, vm?.status, setErr);
  const tools = canOperate && vm && (
    <>
      {/* Start only shows when the VM is off */}
      {actions.start && <ToolButton action={actions.start} />}
      <DropButton label="Shutdown" icon="power" disabled={!on} items={[actions.shutdown, actions.reboot, actions.stop]} />
      <DropButton label="Console" icon="terminal" disabled={!on} items={[actions.console, actions.serial]} />
    </>
  );

  // The page >w<
  return (
    <View title={title} tools={tools} menu={menu} active={active} fill={active === "console"}>
      {/* Errors, and a warning when the spec doesn't have this host */}
      <ErrorBox error={err} />
      {!spec && <div className="alert warn">The spec of {range} has no host named {host}.</div>}
      {/* Console tab: the console when running (loaded lazily), otherwise a hint to start it; Summary tab: the cards */}
      {active === "console" ? (
        on ? (
          <Suspense fallback={<Loading label="Loading the console…" />}>
            <ConsolePage range={range} host={host} kind={console === "serial" ? "serial" : "vnc"} />
          </Suspense>
        ) : <div className="alert info">{vm ? `${host} is ${vm.status}. Start it to open its console.` : `${host} is not built yet.`}</div>
      ) : spec && (
        <div className="summary-row two">
          {/* Status: state, id, range, segment, address, gateways (router), OS, size, and whether it matches the spec */}
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
          {/* Services: the roles on this host */}
          <Card title="Services">
            {(spec.roles ?? []).length ? (
              <ul className="plain-list">{spec.roles!.map((role) => <li key={role.name}>{role.name}</li>)}</ul>
            ) : <div className="muted">No services.</div>}
          </Card>
        </div>
      )}
    </View>
  );
}

/** A VM's console in a browser window of its own (double-click the VM in the tree), as Proxmox opens one. While the
 *  VM is off, the window offers to start it and connects once it runs. */
// ConsoleWindow: the pop-out console. While the VM is off it offers to start it and connects once it runs OwO
export function ConsoleWindow({ range, host, kind, canOperate }: {
  range: string; host: string; kind: "vnc" | "serial"; canOperate: boolean;
}) {
  // Refresh every 5 s (to notice the VM coming up); the window title names the VM ^w^
  const { data, error } = useApi<Detail>(canOperate ? `/api/ranges/${encodeURIComponent(range)}` : null, 5000);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const vm = data?.vms[host];
  const on = vm?.status === "running";
  useEffect(() => { document.title = `${vm ? `VM ${vm.vmid} (${host})` : host} - VALOR console`; }, [vm?.vmid, host]);
  useEffect(() => { if (on) setStarting(false); }, [on]);
  // start(): ask for a start job; offer the button again after 90 s in case the job failed UwU
  const start = async () => {
    setErr(null);
    setStarting(true);
    try {
      await post(`/api/ranges/${range}/power`, { action: "start", hosts: [host] });
      window.setTimeout(() => setStarting(false), 90000);          // offer it again if the start job failed
    } catch (e) {
      setErr(e as ApiError);
      setStarting(false);
    }
  };

  // What the window shows: a role warning, loading/error, the console, or the "it's off" panel >w<
  let body: ReactNode;
  if (!canOperate) body = <div className="alert warn">Opening a console needs the operator role.</div>;
  else if (!data) body = error ? <ErrorBox error={error} /> : <Loading />;
  else if (on) {
    body = (
      <Suspense fallback={<Loading label="Loading the console…" />}>
        <ConsolePage range={range} host={host} kind={kind} windowed />
      </Suspense>
    );
  } else {
    body = (
      <div className="console-off">
        {/* The state, a Start button (when built), and any error */}
        <div>{vm ? `${host} is ${vm.status}.` : `${host} is not built yet.`}</div>
        {vm && <button className="btn primary" disabled={starting} onClick={start}>{starting ? "Starting…" : "Start now"}</button>}
        <ErrorBox error={err} />
      </div>
    );
  }
  // The whole window OwO *opens the little window*
  return <div className="console-window">{body}</div>;
}
