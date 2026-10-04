export type Role = "admin" | "operator" | "viewer";

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

export interface Me {
  user: User;
  stage: "mfa" | "enroll" | "full";
  csrf: string;
  version: string;
  instance: string;
  recovery_codes?: string[];
}

export type Change = "create" | "update" | "replace" | "remove" | "keep";

export interface TopoNode {
  id: string;
  kind: "internet" | "router" | "segment" | "host";
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
}

export interface TopoEdge {
  id: string;
  source: string;
  target: string;
  kind: "uplink" | "gateway" | "egress" | "policy";
  label?: string;
  description?: string;
}

export interface Topology {
  range: string;
  nodes: TopoNode[];
  edges: TopoEdge[];
  changes: Partial<Record<Change, number>> | null;
}

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

export interface Plan {
  range: string;
  actions: PlanAction[];
  summary: Record<string, number>;
  changes: boolean;
  node?: Record<string, any>;
}

export interface JobSummary {
  id: string;
  kind: string;
  state: "queued" | "running" | "succeeded" | "failed";
  target: Record<string, any>;
  created: string;
  finished?: string;
}

export interface Check {
  name: string;
  status: "ok" | "problem" | "unknown";
  detail: string;
  hint?: string;
}
