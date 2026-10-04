"""Drafts: pending changes to a built range, made on the map or by the range chat, applied only through plan ->
approve. A draft is a complete range spec kept next to the range's state (draft.yaml + draft.json); the spec file
itself changes only when the approved plan starts.
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
    d = state.range_dir(cfg, name)
    return d / "draft.yaml", d / "draft.json"


def load(cfg, name: str) -> tuple[str, dict] | None:
    y, m = _paths(cfg, name)
    if not y.is_file():
        return None
    meta = json.loads(m.read_text()) if m.is_file() else {}
    return y.read_text(), meta


def save(cfg, name: str, text: str, by: str, source: str) -> RangeSpec:
    spec = parse_spec(text)
    if spec.name != name:
        raise ValorError("name_mismatch", f"the draft is for range '{spec.name}', not '{name}'")
    y, m = _paths(cfg, name)
    y.write_text(text if text.endswith("\n") else text + "\n")
    m.write_text(json.dumps({"by": by, "source": source, "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    return spec


def discard(cfg, name: str) -> None:
    for p in _paths(cfg, name):
        p.unlink(missing_ok=True)


class _Dumper(yaml.SafeDumper):
    """Hand-written style: lists indented under their key; short all-scalar items (segments, rules, roles) on
    one line, hosts as blocks."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _flow_ok(d: dict) -> bool:
    simple = all(not isinstance(v, (dict, list)) or (isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v))
                 or (isinstance(v, dict) and not v) for v in d.values())
    return simple and len(repr(d)) <= 110


def _repr_dict(dumper, d):
    return dumper.represent_mapping("tag:yaml.org,2002:map", d.items(), flow_style=bool(d) and _flow_ok(d))


def _repr_list(dumper, items):
    scalars = all(not isinstance(x, (dict, list)) for x in items)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", items, flow_style=scalars)


_Dumper.add_representer(dict, _repr_dict)
_Dumper.add_representer(list, _repr_list)


def to_yaml(data: dict) -> str:
    return yaml.dump(_tidy(data), Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=110)


def spec_yaml(spec: RangeSpec) -> str:
    """A spec in the canonical format: diffs between two of them show only real changes."""
    return to_yaml(spec.model_dump(mode="json", by_alias=True, exclude_none=True))


# ---------------------------------------------------------------------- map edits
def _free_address(data: dict, segment: dict) -> str:
    net = ipaddress.IPv4Network(segment["cidr"])
    used = {h["address"] for h in data["hosts"]}
    hosts = list(net.hosts())
    gateway = hosts[0]
    for a in hosts[9:] + hosts[1:9]:                    # .10 upwards first, like hand-written specs
        if a != gateway and str(a) not in used:
            return str(a)
    raise ValorError("segment_full", f"segment {segment['name']} has no free address")


def apply_ops(spec: RangeSpec, ops: list[dict], role_ports: dict[str, list] | None = None) -> dict:
    """Map edits on a spec: add/update/remove a host, add/remove a service (role), optionally allowing access to a
    new service's ports. Returns the new spec as data (validate with parse_spec)."""
    data = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
    hosts = {h["name"]: h for h in data["hosts"]}
    segs = {s["name"]: s for s in data["segments"]}
    for op in ops:
        kind = op.get("op")
        if kind == "add_host":
            name = op.get("name", "")
            if name in hosts or name in segs or name == "rtr":
                raise ValorError("name_taken", f"'{name}' is already used in this range")
            seg = segs.get(op.get("segment"))
            if seg is None:
                raise ValorError("unknown_segment", f"there is no segment '{op.get('segment')}'")
            host = {"name": name, "segment": seg["name"], "address": op.get("address") or _free_address(data, seg)}
            for k in ("os", "description"):
                if op.get(k):
                    host[k] = op[k]
            for k in ("cores", "memory", "disk"):
                host[k] = int(op.get(k) or DEFAULT_SIZE[k])
            host["roles"] = [{"name": r["name"], "params": r.get("params", {})} for r in op.get("roles", [])]
            data["hosts"].append(host)
            hosts[name] = host
        elif kind == "update_host":
            host = _host(hosts, op)
            for k in ("os", "description"):
                if k in op:
                    host[k] = op[k]
            for k in ("cores", "memory", "disk"):
                if op.get(k):
                    host[k] = int(op[k])
        elif kind == "remove_host":
            host = _host(hosts, op)
            data["hosts"].remove(host)
            del hosts[host["name"]]
            data["policy"] = [r for r in data.get("policy", []) if host["name"] not in (r["from"], r["to"])]
            data["tests"] = [t for t in data.get("tests", []) if host["name"] not in (t["from"], t["to"])]
        elif kind == "add_role":
            host = _host(hosts, op)
            role = op.get("role", "")
            if any(r["name"] == role for r in host.get("roles", [])):
                raise ValorError("role_exists", f"{host['name']} already runs {role}")
            host.setdefault("roles", []).append({"name": role, "params": op.get("params") or {}})
            ports = (role_ports or {}).get(role) or []
            for src in op.get("allow_from") or []:
                if src not in segs and src not in hosts:
                    raise ValorError("unknown_endpoint", f"'{src}' is neither a segment nor a host")
                src_segment = src if src in segs else hosts[src]["segment"]
                if src_segment == host["segment"]:
                    continue                    # same segment: never passes the router, nothing to allow
                if ports:
                    data.setdefault("policy", []).append({"from": src, "to": host["name"], "proto": "tcp",
                                                          "ports": ports, "description": f"{src} uses {role} on {host['name']}"})
        elif kind == "remove_role":
            host = _host(hosts, op)
            before = len(host.get("roles", []))
            host["roles"] = [r for r in host.get("roles", []) if r["name"] != op.get("role")]
            if len(host["roles"]) == before:
                raise ValorError("no_role", f"{host['name']} does not run {op.get('role')}")
        else:
            raise ValorError("bad_op", f"unknown change '{kind}'")
    return data


def _host(hosts: dict, op: dict) -> dict:
    host = hosts.get(op.get("host") or op.get("name"))
    if host is None:
        raise ValorError("unknown_host", f"there is no host '{op.get('host') or op.get('name')}'")
    return host


# ---------------------------------------------------------------------- what a draft changes
def changes(current: RangeSpec, draft: RangeSpec) -> dict:
    """A plan-like overlay without asking the cluster: create / update / replace / remove per host."""
    old = {h.name: h for h in current.hosts}
    new = {h.name: h for h in draft.hosts}
    actions = []
    for name, h in new.items():
        o = old.get(name)
        if o is None:
            actions.append({"host": name, "action": "create", "reasons": ["new VM"]})
            continue
        reasons = []
        if (o.os, o.segment, str(o.address), o.install) != (h.os, h.segment, str(h.address), h.install):
            actions.append({"host": name, "action": "replace", "reasons": ["OS, segment or address changed"]})
            continue
        for k in ("cores", "memory", "disk"):
            if getattr(o, k) != getattr(h, k):
                reasons.append(f"{k} {getattr(o, k)} -> {getattr(h, k)}")
        if [r.model_dump() for r in o.roles] != [r.model_dump() for r in h.roles]:
            added = [r.name for r in h.roles if r.name not in {x.name for x in o.roles}]
            removed = [r.name for r in o.roles if r.name not in {x.name for x in h.roles}]
            reasons += [f"add {', '.join(added)}"] if added else []
            reasons += [f"remove {', '.join(removed)}"] if removed else []
            reasons += [] if added or removed else ["service settings changed"]
        actions.append({"host": name, "action": "update" if reasons else "keep", "reasons": reasons})
    for name in old:
        if name not in new:
            actions.append({"host": name, "action": "remove", "reasons": ["removed from the range"], "os": old[name].os})
    if [r.model_dump() for r in current.policy] != [r.model_dump() for r in draft.policy]:
        actions.append({"host": "rtr", "action": "update", "reasons": ["traffic policy changed"]})
    summary: dict[str, int] = {}
    for a in actions:
        if a["action"] != "keep":
            summary[a["action"]] = summary.get(a["action"], 0) + 1
    return {"actions": actions, "summary": summary}


def unified_diff(old_text: str, new_text: str, name: str) -> str:
    return "".join(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                        f"{name}.yaml (built)", f"{name}.yaml (draft)", n=2))
