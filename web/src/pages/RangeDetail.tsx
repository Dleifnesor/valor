import { useState } from "react";
import { ApiError } from "../api";
import { ago, go, openTask, useApi, when } from "../hooks";
import { Topology } from "../types";
import { Card, DropButton, ErrorBox, Icon, Loading, MenuEntry, StateBadge, ToolButton, View } from "../components/ui";
import { RangeMapTab } from "../components/RangeEditing";
import { Markdown } from "../components/Markdown";
import { TopologyMap } from "../components/Topology";
import { HostPower, SnapshotsTab, rangeActions, vmMenu } from "../components/RangeActions";
import { showMenu } from "../components/ContextMenu";
import { AccessTab } from "../components/Access";
import { type ComplianceReport, Verification } from "../components/Verification";
import { RangeEditor } from "./RangeEditor";

// One range: its title-bar actions (start, shut down, verify, plan, ...) and its sections - summary, the editable
// map with the range chat, verification results, snapshots, access (VPN and logins), the spec, the build journal
// and the build history. The data comes from GET /api/ranges/<name>, refreshed every 20 s.

/** A range as GET /api/ranges/<name> returns it: the spec, the VMs on the cluster, and what VALOR recorded. */
export interface Detail {
  name: string;
  yaml: string;
  spec: any;
  version: string;
  vms: Record<string, { vmid: number; status: string; converged: boolean; spec: string }>;
  cluster_error: string | null;
  topology: Topology;
  tests: { name: string; from: string; to: string; proto: string; port: number | null; expect: string; origin: string }[];
  history: { kind: string; started: string; ok: boolean; seconds: number; error?: string; message?: string; job?: string }[];
  journal: string | null;
  verify: null | {
    ok: boolean;
    finished?: string;
    summary: Record<string, any>;
    tests: { name: string; from: string; target: string; expect: string; observed: string; detail: string; pass: boolean }[];
    baseline: Record<string, { control: string; title: string; area: string; after: string }[]>;
    compliance?: ComplianceReport | { error: string };
  };
  last_apply: null | { ok: boolean; started: string; seconds: number; error?: string; message?: string };
  logins: boolean;
}

const SECTIONS: Omit<MenuEntry, "href">[] = [
  { id: "summary", label: "Summary", icon: "info" },
  { id: "map", label: "Map", icon: "map" },
  { id: "verification", label: "Verification", icon: "check" },
  { id: "snapshots", label: "Snapshots", icon: "camera" },
  { id: "access", label: "Access", icon: "key" },
  { id: "spec", label: "Spec", icon: "file" },
  { id: "journal", label: "Journal", icon: "book" },
  { id: "history", label: "History", icon: "clock" },
];
// older links and the routes that open inside a section
const ALIAS: Record<string, string> = { hosts: "summary", tests: "verification", edit: "spec", chat: "map" };

/** A range, as Proxmox shows a node: its actions in the title bar, its sections on the left. */
export function RangeView({ name, section, canOperate, chatId }: { name: string; section?: string; canOperate: boolean; chatId?: number }) {
  const { data, error, loading } = useApi<Detail>(`/api/ranges/${encodeURIComponent(name)}`, 20000);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const active = SECTIONS.some((entry) => entry.id === section) ? section! : ALIAS[section ?? ""] ?? "summary";
  const menu = SECTIONS.map((entry) => ({ ...entry, href: `ranges/${name}${entry.id === "summary" ? "" : `/${entry.id}`}` }));
  const title = `Range '${name}'`;
  if (!data) return <View title={title} menu={menu} active={active}>{loading ? <Loading /> : <ErrorBox error={error} />}</View>;

  const vmCount = Object.keys(data.vms).length;
  const running = Object.values(data.vms).filter((vm) => vm.status === "running").length;
  const actions = rangeActions({ name, vms: vmCount, running }, setActionError);
  const tools = canOperate && (
    <>
      {actions.start && <ToolButton action={actions.start} label="Start" />}
      {actions.shutdown && <DropButton label="Shutdown" icon="power" disabled={!running} items={[actions.shutdown, actions.reboot, actions.stop]} />}
      {actions.login && data.logins && <ToolButton action={actions.login} />}
      {actions.verify && <ToolButton action={actions.verify} />}
      <ToolButton action={actions.plan} primary />
      <DropButton label="More" items={[actions.edit, actions.destroy, actions.remove]} />
    </>
  );

  return (
    <View title={title} tools={tools} menu={menu} active={active} fill={active === "map"}>
      <ErrorBox error={actionError} />
      {data.cluster_error && <div className="alert warn">Live VM status unavailable: {data.cluster_error}</div>}
      {active === "summary" && <Summary data={data} canOperate={canOperate} onError={setActionError} />}
      {active === "map" && (
        <div className="map-page">
          <RangeMapTab name={name} canOperate={canOperate} chatId={chatId}
            onConsole={canOperate ? (host) => go(`ranges/${name}/vm/${host}/console`) : undefined} />
        </div>
      )}
      {active === "verification" && <section className="card"><Verification data={data} /></section>}
      {active === "snapshots" && <section className="card"><SnapshotsTab range={name} canOperate={canOperate} /></section>}
      {active === "access" && <section className="card"><AccessTab range={name} canOperate={canOperate} /></section>}
      {active === "spec" && (section === "edit" ? <RangeEditor name={name} canEdit={canOperate} /> : (
        <Card title={`Spec, version ${data.version}`} actions={canOperate &&
          <button className="btn small" onClick={() => go(`ranges/${name}/edit`)}><Icon name="edit" size={14} /> Edit</button>}>
          <pre className="mono">{data.yaml}</pre>
        </Card>
      ))}
      {active === "journal" && <Journal data={data} />}
      {active === "history" && <section className="card"><History data={data} /></section>}
    </View>
  );
}

/** The router and the hosts as rows: the spec's view of each VM next to the cluster's. */
export function machinesOf(data: Detail) {
  return [{ name: "rtr", segment: "all", address: data.spec.segments.map((segment: any) => segment.cidr).join(", "), os: data.spec.router.os,
    cores: data.spec.router.cores, memory: data.spec.router.memory, disk: data.spec.router.disk, roles: [{ name: "router" }] },
    ...data.spec.hosts] as { name: string; segment: string; address: string; os: string; cores: number; memory: number; disk: number; roles?: { name: string }[] }[];
}

/** Status and notes, then the VMs as a table (a click opens one, right-click for its menu). */
function Summary({ data, canOperate, onError }: { data: Detail; canOperate: boolean; onError: (e: ApiError) => void }) {
  const vmCount = Object.keys(data.vms).length;
  const running = Object.values(data.vms).filter((vm) => vm.status === "running").length;
  const deployed = Object.values(data.vms)[0]?.spec;          // the spec version the VMs were last built from
  return (
    <>
      <div className="summary-row two">
        <Card title="Status">
          <dl className="kv">
            <dt>Status</dt>
            <dd>{vmCount ? <StateBadge state={running === vmCount ? "ok" : running ? "warn" : "unknown"} label={`${running} of ${vmCount} VMs running`} />
              : "not built"}</dd>
            <dt>Spec version</dt>
            <dd><span className="mono">{data.version}</span>{vmCount > 0 && deployed && deployed !== data.version &&
              <> · <StateBadge state="warn" label="changed since the last build" /></>}</dd>
            <dt>Layout</dt><dd>{data.spec.segments.length} segments · {data.spec.hosts.length} hosts and the router</dd>
            <dt>Last build</dt>
            <dd>{data.last_apply ? <><StateBadge state={data.last_apply.ok ? "ok" : "failed"} label={data.last_apply.ok ? "succeeded" : "failed"} />
              {" "}· {ago(data.last_apply.started)}</> : "–"}</dd>
            <dt>Verification</dt>
            <dd>{data.verify ? <><StateBadge state={data.verify.ok ? "ok" : "failed"}
              label={`${data.verify.ok ? "passed" : "failed"} ${data.verify.summary?.tests_passed ?? ""}/${data.verify.summary?.tests_total ?? ""} tests`} />
              {" "}· {ago(data.verify.finished)}</> : "–"}</dd>
          </dl>
        </Card>
        <Card title="Notes">
          <div className="notes">{data.spec.description || <span className="muted">No description.</span>}</div>
        </Card>
      </div>
      <Card title="Virtual machines" bodyClass="">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>VMID</th><th>Status</th><th>Segment</th><th>Address</th><th>OS</th><th>Size</th><th>Services</th><th /></tr></thead>
            <tbody>
              {machinesOf(data).map((machine) => {
                const vm = data.vms[machine.name];
                return (
                  <tr key={machine.name} className="clickable" onClick={() => go(`ranges/${data.name}/vm/${machine.name}`)}
                    onContextMenu={(e) => showMenu(e, vmMenu(data.name, machine.name, vm, canOperate))}>
                    <td className="nowrap"><b>{machine.name}</b></td>
                    <td>{vm ? vm.vmid : "–"}</td>
                    <td className="nowrap">{vm ? <StateBadge state={vm.status === "running" ? "ok" : "unknown"} label={vm.status} /> : <span className="muted">not built</span>}</td>
                    <td>{machine.segment}</td>
                    <td className="mono">{machine.address}</td>
                    <td>{machine.os}</td>
                    <td className="nowrap">{machine.cores} vCPU · {machine.memory} MiB · {machine.disk} GiB</td>
                    <td>{(machine.roles || []).map((role) => role.name).join(", ") || "–"}</td>
                    <td onClick={(e) => e.stopPropagation()}>{vm && canOperate && <HostPower range={data.name} host={machine.name} status={vm.status} onError={onError} />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

/** The build journal (journals/<range>.md) rendered; its Mermaid topology is shown as the range map. */
function Journal({ data }: { data: Detail }) {
  if (!data.journal) return <Card title="Journal"><div className="empty">No journal yet (written after the first build).</div></Card>;
  const text = data.journal;
  const download = () => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    link.download = `${data.name}.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };
  const fence = (lang: string, code: string) => lang === "mermaid" ? (
    <>
      <div className="card md-map"><TopologyMap topology={data.topology} /></div>
      <details className="check-notes"><summary>Mermaid source (as in the file)</summary><pre className="md-code">{code}</pre></details>
    </>
  ) : undefined;
  return (
    <Card title="Journal" actions={<button className="btn small" onClick={download}><Icon name="download" size={14} /> Download .md</button>}>
      <Markdown text={text} fence={fence} />
    </Card>
  );
}

/** Every build, verification and destroy, newest first; a row with a job opens its task log. */
function History({ data }: { data: Detail }) {
  if (!data.history.length) return <div className="empty">No builds recorded yet.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>When</th><th>Action</th><th>Result</th><th>Duration</th><th>Details</th></tr></thead>
        <tbody>
          {[...data.history].reverse().map((entry, i) => (
            <tr key={i} className={entry.job ? "clickable" : ""} onClick={() => entry.job && openTask(entry.job)}>
              <td className="nowrap">{when(entry.started)}</td><td>{entry.kind}</td>
              <td><StateBadge state={entry.ok ? "ok" : "failed"} label={entry.ok ? "succeeded" : "failed"} /></td>
              <td>{entry.seconds ? `${entry.seconds} s` : "–"}</td>
              <td className="small">{entry.error ? `${entry.error}: ${entry.message ?? ""}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
