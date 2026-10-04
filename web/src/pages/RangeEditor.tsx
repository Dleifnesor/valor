import { useEffect, useState } from "react";
import { ApiError, get, post, put } from "../api";
import { Topology } from "../types";
import { go, useApi } from "../hooks";
import { Card, ErrorBox, Loading } from "../components/ui";
import { TopologyMap } from "../components/Topology";

const EXAMPLE = `apiVersion: valor/v1
name: demo
description: Two segments - a web server in a DMZ that may reach a database in a LAN.
baseline: ubuntu-l1

segments:
  - {name: dmz, vlan: 410, cidr: 10.41.0.0/24, internet: true}
  - {name: lan, vlan: 420, cidr: 10.42.0.0/24}

hosts:
  - name: web
    segment: dmz
    address: 10.41.0.10
    roles: [{name: nginx}]
  - name: db
    segment: lan
    address: 10.42.0.10
    memory: 2048
    roles:
      - name: postgresql
        params: {allow_from: [web]}

policy:
  - {from: web, to: db, proto: tcp, ports: [5432], description: app to database}
`;

interface CheckResult {
  ok: boolean;
  errors: { location?: string; message: string; hint?: string; error?: string; details?: any }[];
  warnings?: { location?: string; message: string }[];
  name?: string;
  version?: string;
  topology: Topology | null;
}

interface Catalog {
  default_os: string;
  os: { name: string; description: string; template: boolean }[];
  roles: { name: string; description: string; params: Record<string, { default?: any; description?: string }> }[];
  baselines: { name: string; controls: number }[];
  limits: { vlan_min: number; vlan_max: number; reserved_networks: string[] };
}

export function RangeEditor({ name, canEdit }: { name?: string; canEdit: boolean }) {
  const [text, setText] = useState<string | null>(name ? null : EXAMPLE);
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const catalog = useApi<Catalog>("/api/catalog");

  useEffect(() => {
    if (name) get<{ yaml: string }>(`/api/ranges/${name}`).then((d) => setText(d.yaml)).catch((e) => setErr(e));
  }, [name]);

  if (!canEdit) return <div className="alert warn">You need the operator role to change ranges.</div>;
  if (text === null) return err ? <ErrorBox error={err} /> : <Loading />;

  const runCheck = async () => {
    setBusy(true);
    setErr(null);
    try {
      setCheck(await post<CheckResult>("/api/specs/check", { yaml: text }));
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await post<CheckResult>("/api/specs/check", { yaml: text });
      setCheck(res);
      if (!res.name) return;
      if (name && res.name !== name) throw new ApiError(400, "rename", `The spec's name must stay '${name}'.`, null);
      await put(`/api/ranges/${res.name}/spec`, { yaml: text });
      go(`ranges/${res.name}`);
    } catch (e) {
      setErr(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <Card title={name ? `Spec for ${name}` : "Describe the range"} actions={
        <div className="row">
          <button className="btn" onClick={runCheck} disabled={busy}>Check</button>
          <button className="btn primary" onClick={save} disabled={busy}>Save spec</button>
        </div>
      }>
        <div className="stack">
          <div className="muted small">
            A range spec lists segments (VLANs), hosts and the traffic allowed between segments; everything else is
            blocked. Saving does not build anything - you review and approve a plan first. In milestone 2 the chat
            builder writes this for you.
          </div>
          <textarea className="code" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)}
            aria-label="Range spec (YAML)" />
          <ErrorBox error={err} />
        </div>
      </Card>
      <div className="stack">
        {check && (
          <Card title={check.ok ? "Looks good" : "Needs changes"}>
            <div className="stack">
              {check.ok && <div className="alert ok">The spec is valid{check.warnings?.length ? " (see the notes below)" : ""} - version {check.version}.</div>}
              {!check.ok && (
                <div className="alert error">
                  <ul style={{ margin: 0 }}>
                    {check.errors.map((e, i) => (
                      <li key={i}>
                        {e.location ? <b>{e.location}: </b> : null}{e.message}{e.hint ? ` - ${e.hint}` : ""}
                        {Array.isArray(e.details) && <ul>{e.details.map((d: any, j: number) => <li key={j}>{d.location}: {d.message}</li>)}</ul>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {check.warnings?.map((w, i) => <div key={i} className="alert warn">{w.message}</div>)}
            </div>
          </Card>
        )}
        {check?.topology && (
          <section className="card" style={{ overflow: "hidden" }}>
            <div className="card-head"><h2>Preview</h2></div>
            <TopologyMap topology={check.topology} />
          </section>
        )}
        {catalog.data && (
          <Card title="What you can use">
            <dl className="kv">
              <dt>Operating systems</dt>
              <dd>{catalog.data.os.map((o) => `${o.name}${o.template ? "" : " (template not built)"}`).join(", ")} - default {catalog.data.default_os}</dd>
              <dt>Roles</dt>
              <dd>{catalog.data.roles.map((r) => <div key={r.name}><b>{r.name}</b> <span className="muted">{r.description}</span>
                {Object.keys(r.params || {}).length > 0 && <span className="muted small"> · params: {Object.keys(r.params).join(", ")}</span>}</div>)}</dd>
              <dt>Baselines</dt><dd>{catalog.data.baselines.map((b) => `${b.name} (${b.controls} controls)`).join(", ")}, or none</dd>
              <dt>VLANs</dt><dd>{catalog.data.limits.vlan_min}-{catalog.data.limits.vlan_max}</dd>
              <dt>Avoid networks</dt><dd className="mono">{catalog.data.limits.reserved_networks.join(", ") || "–"}</dd>
            </dl>
          </Card>
        )}
      </div>
    </div>
  );
}
