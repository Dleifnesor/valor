import { FormEvent, useState } from "react";
import { ApiError, put } from "../api";
import { Topology, TopoNode } from "../types";
import { ErrorBox, Modal } from "./ui";

// The map's forms for VMs and services: add a VM, add a service to a VM, change a VM's OS or size, edit the draft
// spec as YAML. Every form only produces map edits ("ops") for the range's draft; nothing is built until the plan
// is approved.

export interface CatalogRole {
  name: string;
  description: string;
  ports?: number[];
  families?: string[];
  params: Record<string, { default?: any; description?: string; required?: boolean }>;
}
export interface Catalog {
  default_os: string;
  os: { name: string; family?: string; description: string; template: boolean }[];
  roles: CatalogRole[];
}
/** One map edit, as the API's /draft/ops takes it (see valor/drafts.py). */
export interface Op { op: string; [field: string]: any }
/** The range's pending changes: the draft spec, who made it, and what it changes. */
export interface Draft {
  yaml: string;
  valid: boolean;
  error?: string;
  meta: { by?: string; source?: string; updated?: string };
  summary?: Record<string, number>;
  diff?: string;
}
/** The range as the map shows it: its draft (if any) and the topology to draw. */
export interface View { draft: Draft | null; topology: Topology }

function familyOf(catalog: Catalog, os: string) {
  return catalog.os.find((entry) => entry.name === os)?.family ?? "debian";
}

/** The roles that support an OS (by its family). */
function rolesFor(catalog: Catalog, os: string) {
  const family = familyOf(catalog, os);
  return catalog.roles.filter((role) => (role.families ?? ["debian"]).includes(family));
}

/** One input per parameter of a role; empty inputs show the default as a placeholder. */
function RoleParams({ role, values, onChange }: {
  role: CatalogRole; values: Record<string, string>; onChange: (values: Record<string, string>) => void;
}) {
  const entries = Object.entries(role.params ?? {});
  if (!entries.length) return null;
  return (
    <div className="params">
      {entries.map(([key, param]) => (
        <label className="field" key={key}>{role.name}: {key}{param.required ? " (required)" : ""}
          {param.description && <span className="hint">{param.description}</span>}
          <input name={`param-${key}`} value={values[key] ?? ""} required={!!param.required}
            placeholder={param.default !== undefined ? String(param.default) : ""}
            onChange={(e) => onChange({ ...values, [key]: e.target.value })} />
        </label>
      ))}
    </div>
  );
}

/** Which other segments may reach the new services (the API adds router rules for the services' ports). */
function AllowFrom({ segments, own, value, onChange }: {
  segments: string[]; own?: string; value: string[]; onChange: (segments: string[]) => void;
}) {
  const others = segments.filter((segment) => segment !== own);
  if (!others.length) return null;
  return (
    <div className="field">Who may reach the new services <span className="hint">adds router rules for their ports; the VM's own segment always can</span>
      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        {others.map((segment) => (
          <label key={segment} className="checkbox">
            <input name="allow-from" type="checkbox" checked={value.includes(segment)}
              onChange={(e) => onChange(e.target.checked ? [...value, segment] : value.filter((x) => x !== segment))} /> {segment}
          </label>
        ))}
      </div>
    </div>
  );
}

/** add_role edits for the chosen roles; empty parameters are left out (the role's default applies). */
function roleOps(host: string, selected: string[], params: Record<string, Record<string, string>>, allow: string[]): Op[] {
  return selected.map((role) => ({
    op: "add_role", host, role, allow_from: allow,
    params: Object.fromEntries(Object.entries(params[role] ?? {}).filter(([, value]) => value !== "")),
  }));
}

/** vCPU / memory / disk inputs, shared by the add and edit forms. */
function SizeInputs({ size, minDisk, onChange }: {
  size: { cores: number; memory: number; disk: number }; minDisk: number;
  onChange: (size: { cores: number; memory: number; disk: number }) => void;
}) {
  return (
    <div className="size-row">
      <label className="field">vCPU<input name="cores" type="number" min={1} max={16} value={size.cores} onChange={(e) => onChange({ ...size, cores: Number(e.target.value) })} /></label>
      <label className="field">MiB<input name="memory" type="number" min={512} step={512} value={size.memory} onChange={(e) => onChange({ ...size, memory: Number(e.target.value) })} /></label>
      <label className="field">GiB<input name="disk" type="number" min={minDisk} value={size.disk} onChange={(e) => onChange({ ...size, disk: Number(e.target.value) })} /></label>
    </div>
  );
}

export function AddVmModal({ segments, segment, catalog, onClose, onSubmit }: {
  segments: string[]; segment?: string; catalog: Catalog; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const osList = catalog.os.filter((entry) => entry.template);       // only OSes with a built template
  const [name, setName] = useState("");
  const [vmSegment, setVmSegment] = useState(segment ?? segments[0] ?? "");
  const [os, setOs] = useState(catalog.default_os);
  const [size, setSize] = useState({ cores: 1, memory: 1024, disk: 10 });
  const [roles, setRoles] = useState<string[]>([]);
  const [params, setParams] = useState<Record<string, Record<string, string>>>({});
  const [allow, setAllow] = useState<string[]>([]);
  const windows = familyOf(catalog, os) === "windows";
  const available = rolesFor(catalog, os);
  const valid = /^[a-z][a-z0-9-]{0,13}[a-z0-9]$/.test(name) && !!vmSegment;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const chosen = roles.filter((role) => available.some((option) => option.name === role));
    onSubmit([{ op: "add_host", name, segment: vmSegment, os, ...size }, ...roleOps(name, chosen, params, allow)]);
  };
  return (
    <Modal title="Add a VM" onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="addvm" disabled={!valid}>Add to the draft</button></>
    }>
      <form id="addvm" className="stack" onSubmit={submit}>
        <div className="grid cols-2">
          <label className="field">Name <span className="hint">2-15 lowercase letters, digits, dashes</span>
            <input name="vm-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} autoFocus required placeholder="web2" />
          </label>
          <label className="field">Segment
            <select name="segment" value={vmSegment} onChange={(e) => setVmSegment(e.target.value)}>
              {segments.map((option) => <option key={option}>{option}</option>)}
            </select>
          </label>
          <label className="field">Operating system
            <select name="os" value={os} onChange={(e) => { setOs(e.target.value); setRoles([]); }}>
              {osList.map((entry) => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
            </select>
          </label>
          <SizeInputs size={size} minDisk={8} onChange={setSize} />
        </div>
        {windows && <div className="muted small">Windows gets at least the memory and disk its template needs, whatever you enter here.</div>}
        <div className="field">Services
          <div className="role-pick">
            {available.map((role) => (
              <label key={role.name} className="checkbox" title={role.description}>
                <input name="roles" type="checkbox" checked={roles.includes(role.name)}
                  onChange={(e) => setRoles(e.target.checked ? [...roles, role.name] : roles.filter((x) => x !== role.name))} />
                <span><b>{role.name}</b> <span className="muted small">{role.description}</span></span>
              </label>
            ))}
          </div>
        </div>
        {roles.map((name) => {
          const role = available.find((option) => option.name === name);
          return role ? <RoleParams key={name} role={role} values={params[name] ?? {}} onChange={(values) => setParams({ ...params, [name]: values })} /> : null;
        })}
        {roles.length > 0 && <AllowFrom segments={segments} own={vmSegment} value={allow} onChange={setAllow} />}
        <div className="muted small">The address is the segment's next free one (from .10). Nothing is built until you review and approve the plan.</div>
      </form>
    </Modal>
  );
}

export function AddServiceModal({ host, segments, catalog, onClose, onSubmit }: {
  host: TopoNode; segments: string[]; catalog: Catalog; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const hostName = host.id.slice(5);                                  // node ids are "host:<name>"
  const available = rolesFor(catalog, host.os ?? catalog.default_os).filter((role) => !(host.roles ?? []).includes(role.name));
  const [roleName, setRoleName] = useState(available[0]?.name ?? "");
  const [params, setParams] = useState<Record<string, string>>({});
  const [allow, setAllow] = useState<string[]>([]);
  const role = available.find((option) => option.name === roleName);
  const own = host.parent?.replace(/^seg:/, "");
  return (
    <Modal title={`Add a service to ${hostName}`} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!roleName} onClick={() => onSubmit(roleOps(hostName, [roleName], { [roleName]: params }, allow))}>Add to the draft</button></>
    }>
      {!available.length ? <div className="empty">No other service supports {host.os}.</div> : (
        <div className="stack">
          <label className="field">Service
            <select name="role" value={roleName} onChange={(e) => { setRoleName(e.target.value); setParams({}); }}>
              {available.map((option) => <option key={option.name} value={option.name}>{option.name} - {option.description}</option>)}
            </select>
          </label>
          {role && <RoleParams role={role} values={params} onChange={setParams} />}
          {role?.ports?.length ? <AllowFrom segments={segments} own={own} value={allow} onChange={setAllow} />
            : <div className="muted small">{roleName} listens on no network port.</div>}
        </div>
      )}
    </Modal>
  );
}

export function EditVmModal({ host, catalog, onClose, onSubmit }: {
  host: TopoNode; catalog: Catalog; onClose: () => void; onSubmit: (ops: Op[]) => void;
}) {
  const hostName = host.id.slice(5);
  const [os, setOs] = useState(host.os ?? catalog.default_os);
  const [size, setSize] = useState({ cores: host.cores ?? 1, memory: host.memory ?? 1024, disk: host.disk ?? 10 });
  const osChanged = os !== host.os;
  return (
    <Modal title={`Edit ${hostName}`} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => onSubmit([{ op: "update_host", name: hostName, ...size, ...(osChanged ? { os } : {}) }])}>Add to the draft</button></>
    }>
      <div className="stack">
        <label className="field">Operating system
          <select name="os" value={os} onChange={(e) => setOs(e.target.value)}>
            {catalog.os.filter((entry) => entry.template || entry.name === host.os).map((entry) => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
          </select>
        </label>
        {osChanged && <div className="alert warn">A new OS means a new VM: its disk is replaced when the plan runs.</div>}
        <SizeInputs size={size} minDisk={host.disk ?? 8} onChange={setSize} />
        <div className="muted small">More CPU or memory restarts the VM; disks can grow, never shrink.</div>
      </div>
    </Modal>
  );
}

/** The whole draft spec as YAML, for changes the map's forms don't cover. */
export function YamlModal({ name, yaml, onClose, onSaved }: { name: string; yaml: string; onClose: () => void; onSaved: (view: View) => void }) {
  const [text, setText] = useState(yaml);
  const [err, setErr] = useState<ApiError | null>(null);
  const save = async () => {
    try { onSaved(await put<View>(`/api/ranges/${name}/draft`, { yaml: text })); } catch (e) { setErr(e as ApiError); }
  };
  return (
    <Modal title={`Draft spec of ${name}`} onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save draft</button></>
    }>
      <textarea name="spec" className="mono" rows={24} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} style={{ width: "100%" }} />
      <ErrorBox error={err} />
    </Modal>
  );
}
