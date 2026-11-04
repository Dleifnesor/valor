// *writes YAML with tiny careful paws* OwO web/src/pages/RangeEditor.tsx is the SPEC EDITOR! ^w^
// For a new range or an existing one: YAML on the left, VALOR's validation (and a map preview) and the catalog of what
// you can use on the right. Saving only STORES the spec; building it goes through Plan *wags tail*
import { useEffect, useState } from "react";
// API calls, the map type, hooks, UI pieces and the map UwU
import { ApiError, get, post, put } from "../api";
import { Topology } from "../types";
import { go, useApi } from "../hooks";
import { Card, ErrorBox, Loading } from "../components/ui";
import { TopologyMap } from "../components/Topology";

// The spec editor for a new range or an existing one: YAML on the left, VALOR's validation and the catalog (OSes,
// roles, baselines) on the right. Saving only stores the spec; building it goes through Plan.

// EXAMPLE: the spec a new range starts with: two segments, a web server and a database, and one rule (a template
// literal, so these lines are text) >w<
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

// CheckResult: what POST /api/specs/check returns OwO
interface CheckResult {
  ok: boolean;
  errors: { location?: string; message: string; hint?: string; error?: string; details?: any }[];
  warnings?: { location?: string; message: string }[];
  name?: string;
  version?: string;
  topology: Topology | null;
}

// Catalog: what GET /api/catalog returns (OSes, roles, baselines, limits) ^w^
interface Catalog {
  default_os: string;
  os: { name: string; description: string; template: boolean }[];
  roles: { name: string; description: string; params: Record<string, { default?: any; description?: string }> }[];
  baselines: { name: string; controls: number }[];
  limits: { vlan_min: number; vlan_max: number; reserved_networks: string[] };
}

// RangeEditor: `name` = an existing range (none = new); canEdit = operators only UwU
export function RangeEditor({ name, canEdit }: { name?: string; canEdit: boolean }) {
  // The text: loaded later for an existing range; for a new one, a spec handed over by the chat builder, or the
  // example >w<
  const [text, setText] = useState<string | null>(() => {
    if (name) return null;
    try {                                   // a spec handed over by the chat builder
      const draft = sessionStorage.getItem("valor-editor-draft");
      if (draft) {
        sessionStorage.removeItem("valor-editor-draft");
        return draft;
      }
    } catch {
      /* no draft */
    }
    return EXAMPLE;
  });
  // The last check result, busy, errors, and the catalog OwO
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const catalog = useApi<Catalog>("/api/catalog");

  // An existing range: load its YAML ^w^
  useEffect(() => {
    if (name) get<{ yaml: string }>(`/api/ranges/${name}`).then((d) => setText(d.yaml)).catch((e) => setErr(e));
  }, [name]);

  // Viewers can't edit; wait for the text UwU
  if (!canEdit) return <div className="alert warn">You need the operator role to change ranges.</div>;
  if (text === null) return err ? <ErrorBox error={err} /> : <Loading />;

  // runCheck(): validate without saving >w<
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

  // save(): check, keep the range's name, save, and open the range OwO
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

  // Two columns: the editor, and the results ^w^
  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      {/* The editor card, with Check and Save spec in its header */}
      <Card title={name ? `Spec for ${name}` : "Describe the range"} actions={
        <div className="row">
          <button className="btn" onClick={runCheck} disabled={busy}>Check</button>
          <button className="btn primary" onClick={save} disabled={busy}>Save spec</button>
        </div>
      }>
        {/* A short explanation, the YAML box, and errors */}
        <div className="stack">
          <div className="muted small">
            A range spec lists segments (VLANs), hosts and the traffic allowed between segments; everything else is
            blocked. Saving does not build anything - you review and approve a plan first. In milestone 2 the chat
            builder writes this for you.
          </div>
          <textarea name="spec" className="code" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)}
            aria-label="Range spec (YAML)" />
          <ErrorBox error={err} />
        </div>
      </Card>
      {/* The right column */}
      <div className="stack">
        {/* The check result: valid (with version), or every problem with its location, hint and details; then warnings */}
        {check && (
          <Card title={check.ok ? "Looks good" : "Needs changes"}>
            <div className="stack">
              {check.ok && <div className="alert ok">The spec is valid{check.warnings?.length ? " (see the notes below)" : ""} - version {check.version}.</div>}
              {!check.ok && (
                <div className="alert error">
                  <ul style={{ margin: 0 }}>
                    {check.errors.map((error, i) => (
                      <li key={i}>
                        {error.location ? <b>{error.location}: </b> : null}{error.message}{error.hint ? ` - ${error.hint}` : ""}
                        {Array.isArray(error.details) && <ul>{error.details.map((detail: any, j: number) => <li key={j}>{detail.location}: {detail.message}</li>)}</ul>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {check.warnings?.map((warning, i) => <div key={i} className="alert warn">{warning.message}</div>)}
            </div>
          </Card>
        )}
        {/* The map preview */}
        {check?.topology && (
          <section className="card" style={{ overflow: "hidden" }}>
            <div className="card-head"><h2>Preview</h2></div>
            <TopologyMap topology={check.topology} />
          </section>
        )}
        {/* What you can use here: OSes (and which have templates), roles (with params), baselines, VLANs, networks to avoid */}
        {catalog.data && (
          <Card title="What you can use">
            <dl className="kv">
              <dt>Operating systems</dt>
              <dd>{catalog.data.os.map((os) => `${os.name}${os.template ? "" : " (template not built)"}`).join(", ")} - default {catalog.data.default_os}</dd>
              <dt>Roles</dt>
              <dd>{catalog.data.roles.map((role) => <div key={role.name}><b>{role.name}</b> <span className="muted">{role.description}</span>
                {Object.keys(role.params || {}).length > 0 && <span className="muted small"> · params: {Object.keys(role.params).join(", ")}</span>}</div>)}</dd>
              <dt>Baselines</dt><dd>{catalog.data.baselines.map((baseline) => `${baseline.name} (${baseline.controls} controls)`).join(", ")}, or none</dd>
              <dt>VLANs</dt><dd>{catalog.data.limits.vlan_min}-{catalog.data.limits.vlan_max}</dd>
              <dt>Avoid networks</dt><dd className="mono">{catalog.data.limits.reserved_networks.join(", ") || "–"}</dd>
            </dl>
          </Card>
        )}
      </div>
    </div>
  );
}
