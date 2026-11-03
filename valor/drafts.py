"""Drafts: pending changes to a built range, made on the map or by the range chat, applied only through plan ->
approve. A draft is a complete range spec kept next to the range's state (draft.yaml + draft.json); the spec file
itself changes only when the approved plan starts.

Also here: `apply_ops` (the map's edits - add a host, a service, a segment, a rule - as changes to spec data),
`changes` (what a draft would do to the built range, without asking the cluster) and the YAML writer that keeps
drafts looking hand-written.
"""

from __future__ import annotations

import difflib
import ipaddress
import json
import time

import yaml

from . import state
from .blueprints import _tidy
from .errors import ValorError
from .spec import RangeSpec, parse_spec

DEFAULT_SIZE = {"cores": 1, "memory": 1024, "disk": 10}


def _paths(cfg, name: str):
    """(the draft spec, its metadata: who changed it, from where, when)."""
    folder = state.range_dir(cfg, name)
    return folder / "draft.yaml", folder / "draft.json"


def load(cfg, name: str) -> tuple[str, dict] | None:
    spec_file, meta_file = _paths(cfg, name)
    if not spec_file.is_file():
        return None
    meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
    return spec_file.read_text(), meta


def save(cfg, name: str, text: str, by: str, source: str) -> RangeSpec:
    spec = parse_spec(text)
    if spec.name != name:
        raise ValorError("name_mismatch", f"the draft is for range '{spec.name}', not '{name}'")
    spec_file, meta_file = _paths(cfg, name)
    spec_file.write_text(text if text.endswith("\n") else text + "\n")
    meta_file.write_text(json.dumps({"by": by, "source": source, "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    return spec


def discard(cfg, name: str) -> None:
    for path in _paths(cfg, name):
        path.unlink(missing_ok=True)


class _Dumper(yaml.SafeDumper):
    """Hand-written style: lists indented under their key; short all-scalar items (segments, rules, roles) on
    one line, hosts as blocks."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _is_scalar(value) -> bool:
    return not isinstance(value, (dict, list))


def _flow_ok(mapping: dict) -> bool:
    """A mapping goes on one line ({name: dmz, vlan: 110, ...}) when it holds only scalars, lists of scalars or
    empty mappings, and is short."""
    simple = all(_is_scalar(value)
                 or (isinstance(value, list) and all(_is_scalar(item) for item in value))
                 or (isinstance(value, dict) and not value)
                 for value in mapping.values())
    return simple and len(repr(mapping)) <= 110


def _repr_dict(dumper, mapping):
    return dumper.represent_mapping("tag:yaml.org,2002:map", mapping.items(),
                                    flow_style=bool(mapping) and _flow_ok(mapping))


def _repr_list(dumper, items):
    scalars = all(_is_scalar(item) for item in items)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", items, flow_style=scalars)


_Dumper.add_representer(dict, _repr_dict)
_Dumper.add_representer(list, _repr_list)


def to_yaml(data: dict) -> str:
    return yaml.dump(_tidy(data), Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=110)


def spec_yaml(spec: RangeSpec) -> str:
    """A spec in the canonical format: diffs between two of them show only real changes."""
    return to_yaml(spec.model_dump(mode="json", by_alias=True, exclude_none=True))


# ---- Map edits -------------------------------------------------------------------------------------------
def _free_address(data: dict, segment: dict) -> str:
    network = ipaddress.IPv4Network(segment["cidr"])
    used = {host["address"] for host in data["hosts"]}
    addresses = list(network.hosts())
    gateway = addresses[0]
    for address in addresses[9:] + addresses[1:9]:        # .10 upwards first, like hand-written specs
        if address != gateway and str(address) not in used:
            return str(address)
    raise ValorError("segment_full", f"segment {segment['name']} has no free address")


def suggest_segment(data: dict, alloc: dict) -> dict:
    """A free VLAN and a free private /24 for a new segment (not used by this or any other range, nor the cluster)."""
    vlans = set(alloc.get("vlans", ())) | {segment["vlan"] for segment in data["segments"]}
    nets = list(alloc.get("nets", ())) + [ipaddress.IPv4Network(segment["cidr"]) for segment in data["segments"]]
    wireguard = (data.get("access") or {}).get("wireguard")
    if wireguard:
        nets.append(ipaddress.IPv4Network(wireguard.get("network", "10.250.0.0/24")))
    low, high = alloc.get("vlan_range", (100, 3999))
    vlan = next((candidate for candidate in range(low, high + 1) if candidate not in vlans), None)
    if vlan is None:
        raise ValorError("no_free_vlan", "no free VLAN left")
    from .blueprints import NET_START, _free_net
    return {"vlan": vlan, "cidr": str(_free_net(24, NET_START, [(net, "") for net in nets]))}


def _drop_host(data: dict, hosts: dict, name: str) -> None:
    """Remove a host and everything that names it (rules, tests)."""
    data["hosts"] = [host for host in data["hosts"] if host["name"] != name]
    hosts.pop(name, None)
    data["policy"] = [rule for rule in data.get("policy", []) if name not in (rule["from"], rule["to"])]
    data["tests"] = [test for test in data.get("tests", []) if name not in (test["from"], test["to"])]


def _set_vpn_reach(data: dict, segment: str, reach: bool | None, old_names: list[str]) -> None:
    """WireGuard: `reach` lists the segments peers may reach; empty means all of them."""
    wireguard = (data.get("access") or {}).get("wireguard")
    if not wireguard or reach is None:
        return
    current = list(wireguard.get("reach") or [])
    if reach:
        if current and segment not in current:
            current.append(segment)
    else:
        current = [name for name in (current or old_names) if name != segment]
        if not current:
            raise ValorError("vpn_reach_empty", "WireGuard peers would reach no segment at all",
                             hint="Leave this segment reachable, or remove WireGuard access from the spec.")
    wireguard["reach"] = current


def apply_ops(spec: RangeSpec, ops: list[dict], role_ports=None,
              alloc: dict | None = None) -> dict:
    """Map edits on a spec: hosts, services (roles), segments and traffic rules. alloc: what is taken elsewhere
    (VLANs, networks, the VLAN range) for new segments. Returns the new spec as data (validate with parse_spec)."""
    edit = _MapEdit(spec, role_ports, alloc)
    for op in ops:
        kind = op.get("op")
        if kind not in _MapEdit.OPS:
            raise ValorError("bad_op", f"unknown change '{kind}'")
        getattr(edit, kind)(op)
    return edit.data


class _MapEdit:
    """The spec as plain data while map edits are applied to it, one method per kind of edit (`op`)."""

    OPS = ("add_segment", "update_segment", "remove_segment", "add_rule", "remove_rule",
           "add_host", "update_host", "remove_host", "add_role", "remove_role")

    def __init__(self, spec: RangeSpec, role_ports, alloc: dict | None):
        self.data = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.hosts = {host["name"]: host for host in self.data["hosts"]}
        self.segments = {segment["name"]: segment for segment in self.data["segments"]}
        self.role_ports = role_ports          # role name -> ports, or a function (role, params) -> ports
        self.alloc = alloc or {}

    # ---- lookups ----

    def segment(self, name) -> dict:
        segment = self.segments.get(name)
        if segment is None:
            raise ValorError("unknown_segment", f"there is no segment '{name}'")
        return segment

    def host(self, op: dict) -> dict:
        return _host(self.hosts, op)

    def check_free_name(self, name: str) -> None:
        if name in self.segments or name in self.hosts or name == "rtr":
            raise ValorError("name_taken", f"'{name}' is already used in this range")

    def check_endpoint(self, end) -> None:
        if end not in self.segments and end not in self.hosts:
            raise ValorError("unknown_endpoint", f"'{end}' is neither a segment nor a host")

    # ---- segments ----

    def add_segment(self, op: dict) -> None:
        """A new segment; without an explicit VLAN and network it gets a free one."""
        name = op.get("name", "")
        self.check_free_name(name)
        old_names = list(self.segments)
        free = suggest_segment(self.data, self.alloc) if not (op.get("vlan") and op.get("cidr")) else {}
        segment = {"name": name, "vlan": int(op.get("vlan") or free["vlan"]), "cidr": op.get("cidr") or free["cidr"],
                   "internet": bool(op.get("internet"))}
        if op.get("description"):
            segment["description"] = op["description"]
        self.data["segments"].append(segment)
        self.segments[name] = segment
        _set_vpn_reach(self.data, name, op.get("vpn_reach"), old_names)

    def update_segment(self, op: dict) -> None:
        segment = self.segment(op.get("name"))
        for key in ("description", "internet"):
            if key in op:
                segment[key] = op[key]
        if op.get("vlan"):
            segment["vlan"] = int(op["vlan"])
        if op.get("cidr") and op["cidr"] != segment["cidr"]:
            self.renumber(segment, ipaddress.IPv4Network(op["cidr"], strict=False))
        if "vpn_reach" in op:
            _set_vpn_reach(self.data, segment["name"], op["vpn_reach"], list(self.segments))

    def renumber(self, segment: dict, new: ipaddress.IPv4Network) -> None:
        """Move a segment to another network. Hosts (and tests aimed at addresses) keep their offset, so .10
        stays .10."""
        old = ipaddress.IPv4Network(segment["cidr"])
        for host in self.data["hosts"]:
            if host["segment"] == segment["name"]:
                offset = int(ipaddress.IPv4Address(host["address"])) - int(old.network_address)
                if offset >= new.num_addresses - 1:
                    raise ValorError("network_too_small", f"{new} has no room for {host['name']}'s address")
                host["address"] = str(new.network_address + offset)
        for test in self.data.get("tests", []):
            try:
                ip = ipaddress.IPv4Address(test["to"])
            except ValueError:
                continue
            if ip in old:
                test["to"] = str(new.network_address + (int(ip) - int(old.network_address)))
        segment["cidr"] = str(new)

    def remove_segment(self, op: dict) -> None:
        segment = self.segment(op.get("name"))
        name = segment["name"]
        inside = [host["name"] for host in self.data["hosts"] if host["segment"] == name]
        if inside and not op.get("with_hosts"):
            raise ValorError("segment_not_empty", f"{name} still has VMs: {', '.join(inside)}",
                             hint="Remove its VMs too, or move them first.")
        if len(self.segments) == 1:
            raise ValorError("last_segment", "a range needs at least one segment")
        for host in inside:
            _drop_host(self.data, self.hosts, host)
        self.data["policy"] = [rule for rule in self.data.get("policy", []) if name not in (rule["from"], rule["to"])]
        wireguard = (self.data.get("access") or {}).get("wireguard")
        if wireguard and wireguard.get("reach"):
            wireguard["reach"] = [reach for reach in wireguard["reach"] if reach != name]
            if not wireguard["reach"]:
                raise ValorError("vpn_reach_empty", f"WireGuard peers could only reach {name}",
                                 hint="Let them reach another segment first.")
        self.data["segments"] = [other for other in self.data["segments"] if other["name"] != name]
        del self.segments[name]

    # ---- traffic rules ----

    def add_rule(self, op: dict) -> None:
        for end in (op.get("from"), op.get("to")):
            self.check_endpoint(end)
        proto = op.get("proto", "tcp")
        rule = {"from": op["from"], "to": op["to"], "proto": proto}
        if proto in ("tcp", "udp"):
            rule["ports"] = op.get("ports") or []
        if op.get("description"):
            rule["description"] = op["description"]
        self.data.setdefault("policy", []).append(rule)

    def remove_rule(self, op: dict) -> None:
        """Rules have no names: the map sends the rule's index plus its ends, so a stale map can't delete
        the wrong rule."""
        rules = self.data.get("policy", [])
        i = op.get("index")
        if (not isinstance(i, int) or not 0 <= i < len(rules)
                or (rules[i]["from"], rules[i]["to"]) != (op.get("from"), op.get("to"))):
            raise ValorError("rule_changed", "that rule is not there any more; reload the map")
        del rules[i]

    # ---- hosts ----

    def add_host(self, op: dict) -> None:
        name = op.get("name", "")
        self.check_free_name(name)
        segment = self.segment(op.get("segment"))
        host = {"name": name, "segment": segment["name"],
                "address": op.get("address") or _free_address(self.data, segment)}
        for key in ("os", "description"):
            if op.get(key):
                host[key] = op[key]
        for key in ("cores", "memory", "disk"):
            host[key] = int(op.get(key) or DEFAULT_SIZE[key])
        host["roles"] = [{"name": role["name"], "params": role.get("params", {})} for role in op.get("roles", [])]
        self.data["hosts"].append(host)
        self.hosts[name] = host

    def update_host(self, op: dict) -> None:
        host = self.host(op)
        for key in ("os", "description"):
            if key in op:
                host[key] = op[key]
        for key in ("cores", "memory", "disk"):
            if op.get(key):
                host[key] = int(op[key])

    def remove_host(self, op: dict) -> None:
        _drop_host(self.data, self.hosts, self.host(op)["name"])

    # ---- services (roles) ----

    def add_role(self, op: dict) -> None:
        """Add a service; `allow_from` also opens the service's ports to those segments/hosts in the policy."""
        host = self.host(op)
        role = op.get("role", "")
        if any(existing["name"] == role for existing in host.get("roles", [])):
            raise ValorError("role_exists", f"{host['name']} already runs {role}")
        host.setdefault("roles", []).append({"name": role, "params": op.get("params") or {}})
        if callable(self.role_ports):
            ports = self.role_ports(role, op.get("params") or {}) or []
        else:
            ports = (self.role_ports or {}).get(role) or []
        for source in op.get("allow_from") or []:
            self.check_endpoint(source)
            source_segment = source if source in self.segments else self.hosts[source]["segment"]
            if source_segment == host["segment"]:
                continue                    # same segment: never passes the router, nothing to allow
            if ports:
                self.data.setdefault("policy", []).append({"from": source, "to": host["name"], "proto": "tcp",
                                                           "ports": ports,
                                                           "description": f"{source} uses {role} on {host['name']}"})

    def remove_role(self, op: dict) -> None:
        host = self.host(op)
        before = len(host.get("roles", []))
        host["roles"] = [role for role in host.get("roles", []) if role["name"] != op.get("role")]
        if len(host["roles"]) == before:
            raise ValorError("no_role", f"{host['name']} does not run {op.get('role')}")


def _host(hosts: dict, op: dict) -> dict:
    host = hosts.get(op.get("host") or op.get("name"))
    if host is None:
        raise ValorError("unknown_host", f"there is no host '{op.get('host') or op.get('name')}'")
    return host


# ---- What a draft changes --------------------------------------------------------------------------------
def changes(current: RangeSpec, draft: RangeSpec) -> dict:
    """A plan-like overlay without asking the cluster: create / update / replace / remove per host."""
    old_hosts = {host.name: host for host in current.hosts}
    new_hosts = {host.name: host for host in draft.hosts}
    old_vlan = {segment.name: segment.vlan for segment in current.segments}
    new_vlan = {segment.name: segment.vlan for segment in draft.segments}
    actions = []
    for name, new in new_hosts.items():
        old = old_hosts.get(name)
        if old is None:
            actions.append({"host": name, "action": "create", "reasons": ["new VM"]})
            continue
        if ((old.os, old.segment, str(old.address), old.install) != (new.os, new.segment, str(new.address), new.install)
                or old_vlan.get(old.segment) != new_vlan.get(new.segment)):
            actions.append({"host": name, "action": "replace", "reasons": ["OS, segment, VLAN or address changed"]})
            continue
        reasons = [f"{key} {getattr(old, key)} -> {getattr(new, key)}" for key in ("cores", "memory", "disk")
                   if getattr(old, key) != getattr(new, key)]
        if [role.model_dump() for role in old.roles] != [role.model_dump() for role in new.roles]:
            old_names, new_names = {role.name for role in old.roles}, {role.name for role in new.roles}
            added = [role.name for role in new.roles if role.name not in old_names]
            removed = [role.name for role in old.roles if role.name not in new_names]
            reasons += [f"add {', '.join(added)}"] if added else []
            reasons += [f"remove {', '.join(removed)}"] if removed else []
            reasons += [] if added or removed else ["service settings changed"]
        actions.append({"host": name, "action": "update" if reasons else "keep", "reasons": reasons})
    for name in old_hosts:
        if name not in new_hosts:
            actions.append({"host": name, "action": "remove", "reasons": ["removed from the range"],
                            "os": old_hosts[name].os})

    def segment_key(segment):                       # the router has a NIC per segment
        return segment.name, segment.vlan, str(segment.cidr)

    if [segment_key(s) for s in current.segments] != [segment_key(s) for s in draft.segments]:
        actions.append({"host": "rtr", "action": "replace",
                        "reasons": ["segments changed: the router is rebuilt with a NIC per segment (it keeps its LAN address)"]})
    elif ([r.model_dump() for r in current.policy] != [r.model_dump() for r in draft.policy]
          or [s.internet for s in current.segments] != [s.internet for s in draft.segments]
          or current.access != draft.access):
        actions.append({"host": "rtr", "action": "update", "reasons": ["traffic policy changed"]})
    summary: dict[str, int] = {}
    for action in actions:
        if action["action"] != "keep":
            summary[action["action"]] = summary.get(action["action"], 0) + 1
    return {"actions": actions, "summary": summary}


def unified_diff(old_text: str, new_text: str, name: str) -> str:
    return "".join(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                        f"{name}.yaml (built)", f"{name}.yaml (draft)", n=2))
