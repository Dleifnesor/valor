"""Topology graph of a range for the web UI's map: nodes, edges and, given a plan, what each change does."""

from __future__ import annotations

from ..spec import ROUTER, RangeSpec

# Plan actions as the map shows them.
CHANGE = {"create": "create", "replace": "replace", "update": "update", "converge": "update", "start": "update",
          "restamp": "keep", "keep": "keep", "remove": "remove"}


def _ports(ports: list) -> str:
    return ",".join(str(p) for p in ports)


def build(spec: RangeSpec, vms: dict[str, dict] | None = None, plan: dict | None = None) -> dict:
    """vms: host -> {"vmid", "status"}; plan: the engine's plan (adds a `change` to every node).

    Node ids: "internet", "rtr", "seg:<name>", "host:<name>". Hosts carry `parent` = their segment node."""
    vms = vms or {}
    actions = {a["host"]: a for a in (plan or {}).get("actions", [])}

    def state(host: str) -> dict:
        out = {}
        if host in vms:
            out.update(vmid=vms[host].get("vmid"), status=vms[host].get("status"))
        if plan is not None:
            a = actions.get(host)
            out["change"] = CHANGE.get(a["action"], "keep") if a else "keep"
            if a and a.get("reasons"):
                out["reasons"] = a["reasons"]
        return out

    r = spec.router
    nodes = [
        {"id": "internet", "kind": "internet", "label": "Internet"},
        {"id": ROUTER, "kind": "router", "label": f"{spec.name}-{ROUTER}", "os": r.os, "cores": r.cores,
         "memory": r.memory, "disk": r.disk, "gateways": [str(s.gateway) for s in spec.segments], **state(ROUTER)},
    ]
    edges = [{"id": "uplink", "source": "internet", "target": ROUTER, "kind": "uplink", "label": "NAT uplink"}]
    for s in spec.segments:
        nodes.append({"id": f"seg:{s.name}", "kind": "segment", "label": s.name, "vlan": s.vlan, "cidr": str(s.cidr),
                      "gateway": str(s.gateway), "internet": s.internet, "description": s.description or ""})
        edges.append({"id": f"gw:{s.name}", "source": ROUTER, "target": f"seg:{s.name}", "kind": "gateway",
                      "label": str(s.gateway)})
        if s.internet:
            edges.append({"id": f"egress:{s.name}", "source": f"seg:{s.name}", "target": "internet", "kind": "egress",
                          "label": "internet (public addresses only)"})
    for h in spec.hosts:
        nodes.append({"id": f"host:{h.name}", "kind": "host", "parent": f"seg:{h.segment}", "label": h.name,
                      "address": str(h.address), "os": h.os, "roles": [x.name for x in h.roles], "cores": h.cores,
                      "memory": h.memory, "disk": h.disk, **state(h.name)})
    hosts = {h.name for h in spec.hosts}

    def ref(name: str) -> str:
        return f"host:{name}" if name in hosts else f"seg:{name}"

    for i, p in enumerate(spec.policy):
        label = f"{p.proto} {_ports(p.ports)}" if p.ports else p.proto
        edges.append({"id": f"rule:{i}", "source": ref(p.from_), "target": ref(p.to), "kind": "policy",
                      "label": label, "description": p.description or ""})
    # VMs the plan removes are no longer in the spec: show them so the overlay can mark them.
    for host, a in actions.items():
        if a["action"] == "remove" and host != ROUTER and host not in hosts:
            nodes.append({"id": f"host:{host}", "kind": "host", "parent": None, "label": host, "address": "",
                          "os": a.get("os") or "", "roles": [], "change": "remove", "vmid": a.get("vmid")})
    summary = {}
    if plan is not None:
        for n in nodes:
            if "change" in n:
                summary[n["change"]] = summary.get(n["change"], 0) + 1
    return {"range": spec.name, "nodes": nodes, "edges": edges, "changes": summary if plan is not None else None}
