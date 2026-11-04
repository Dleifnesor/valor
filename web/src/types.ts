// *labels every box neatly* OwO web/src/types.ts: the SHAPES of API data shared by several pages! ^w^
// Pure TypeScript types (they vanish when compiled; they just let the compiler check our code) *wags tail*
// The API's shapes that several pages share: users and the session, the topology the maps draw, plans, jobs and
// the range list. Types used by one page only live in that page.

// Role: the three VALOR roles UwU
export type Role = "admin" | "operator" | "viewer";

// User: an account as the API shows it (never the password hash or MFA secret; temporary_password appears once
// after a reset) >w<
export interface User {
  id: number;
  username: string;
  display_name: string;
  email: string;
  role: Role;
  source: "local" | "ldap";
  mfa: boolean;
  disabled: boolean;
  locked: boolean;
  must_change_password: boolean;
  created_at: number;
  last_login_at: number | null;
  temporary_password?: string;
}

// Me: the signed-in session: the user, the sign-in stage, the CSRF token, VALOR's version and name, and recovery
// codes right after MFA setup OwO
export interface Me {
  user: User;
  stage: "mfa" | "enroll" | "full";
  csrf: string;
  version: string;
  instance: string;
  recovery_codes?: string[];
}

// Change: the map's change colors ^w^
export type Change = "create" | "update" | "replace" | "remove" | "keep";

// TopoNode: one node on the map (internet, router, segment, host or VPN); the fields used depend on its kind UwU
export interface TopoNode {
  id: string;
  kind: "internet" | "router" | "segment" | "host" | "vpn";
  label: string;
  parent?: string | null;
  // segment
  vlan?: number;
  cidr?: string;
  gateway?: string;
  internet?: boolean;
  description?: string;
  // host / router
  address?: string;
  os?: string;
  roles?: string[];
  cores?: number;
  memory?: number;
  disk?: number;
  gateways?: string[];
  vmid?: number;
  status?: string;
  change?: Change;
  reasons?: string[];
  // vpn (WireGuard access on the router)
  port?: number;
  peers?: string[];
  reach?: string[];
  endpoint?: string;
}

// TopoEdge: one line on the map: uplink, gateway, internet egress, a firewall rule, or the VPN >w<
export interface TopoEdge {
  id: string;
  source: string;
  target: string;
  kind: "uplink" | "gateway" | "egress" | "policy" | "vpn";
  label?: string;
  description?: string;
}

// Topology: the whole map, plus change counts when it shows a plan OwO
export interface Topology {
  range: string;
  nodes: TopoNode[];
  edges: TopoEdge[];
  changes: Partial<Record<Change, number>> | null;
}

// PlanAction: one VM's line in a plan ^w^
export interface PlanAction {
  host: string;
  action: string;
  reasons: string[];
  vmid: number | null;
  role: string;
  os: string;
  segment: string | null;
  address: string;
  cores: number;
  memory_mib: number;
  disk_gib: number;
}

// Plan: the whole plan (summary counts, whether anything changes, the node's memory check) UwU
export interface Plan {
  range: string;
  actions: PlanAction[];
  summary: Record<string, number>;
  changes: boolean;
  node?: Record<string, any>;
}

// JobSummary: one job in a list >w<
export interface JobSummary {
  id: string;
  kind: string;
  state: "queued" | "running" | "succeeded" | "failed";
  target: Record<string, any>;
  created: string;
  finished?: string;
  origin?: string;
  error?: string;
}

/** A VM of a range as the cluster reports it. */
export interface Machine {
  host: string;
  vmid: number;
  status: string;
}

/** One entry of GET /api/ranges: what the resource tree and the overview show. */
export interface RangeRow {
  name: string;
  has_spec: boolean;
  description?: string;
  version?: string;
  deployed_version?: string;
  segments?: number;
  hosts?: number;
  vms: number;
  running: number;
  machines?: Machine[];
  error?: string;
  last_apply: null | { ok: boolean; started: string; seconds: number; error?: string };
  last_verify: null | { ok: boolean; finished: string; summary: Record<string, any> };
}

// Check: one health check OwO *ties a bow on the last box*
export interface Check {
  name: string;
  status: "ok" | "problem" | "unknown";
  detail: string;
  hint?: string;
}
