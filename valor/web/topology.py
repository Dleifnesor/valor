"""Topology graph of a range for the web UI's map: nodes, edges and, given a plan, what each change does."""

from __future__ import annotations

from ..spec import ROUTER, RangeSpec

# Plan actions as the map shows them.
CHANGE = {"create": "create", "replace": "replace", "update": "update", "converge": "update", "start": "update",
          "restamp": "keep", "keep": "keep", "remove": "remove"}


def _ports(ports: list) -> str:
    return ",".join(str(port) for port in ports)


def build(spec: RangeSpec, vms: dict[str, dict] | None = None, plan: dict | None = None) -> dict:
    """vms: host -> {"vmid", "status"}; plan: the engine's plan (adds a `change` to every node).

    Node ids: "internet", "rtr", "seg:<name>", "host:<name>". Hosts carry `parent` = their segment node."""
    vms = vms or {}
    actions = {action["host"]: action for action in (plan or {}).get("actions", [])}

    def state(host: str) -> dict:
        """The VM's live state and, with a plan, what the plan does to it."""
        out = {}
        if host in vms:
            out.update(vmid=vms[host].get("vmid"), status=vms[host].get("status"))
        if plan is not None:
            action = actions.get(host)
            out["change"] = CHANGE.get(action["action"], "keep") if action else "keep"
            if action and action.get("reasons"):
                out["reasons"] = action["reasons"]
        return out

    router = spec.router
    nodes = [
        {"id": "internet", "kind": "internet", "label": "Internet"},
        {"id": ROUTER, "kind": "router", "label": f"{spec.name}-{ROUTER}", "os": router.os, "cores": router.cores,
         "memory": router.memory, "disk": router.disk,
         "gateways": [str(segment.gateway) for segment in spec.segments], **state(ROUTER)},
    ]
    edges = [{"id": "uplink", "source": "internet", "target": ROUTER, "kind": "uplink", "label": "NAT uplink"}]
    for segment in spec.segments:
        node_id = f"seg:{segment.name}"
        nodes.append({"id": node_id, "kind": "segment", "label": segment.name, "vlan": segment.vlan,
                      "cidr": str(segment.cidr), "gateway": str(segment.gateway), "internet": segment.internet,
                      "description": segment.description or ""})
        edges.append({"id": f"gw:{segment.name}", "source": ROUTER, "target": node_id, "kind": "gateway",
                      "label": str(segment.gateway)})
        if segment.internet:
            edges.append({"id": f"egress:{segment.name}", "source": node_id, "target": "internet", "kind": "egress",
                          "label": "internet (public addresses only)"})
    wireguard = spec.access.wireguard if spec.access else None
    if wireguard:
        nodes.append({"id": "vpn", "kind": "vpn", "label": "WireGuard", "port": wireguard.port,
                      "peers": list(wireguard.peers), "cidr": str(wireguard.network),
                      "reach": wireguard.reach or [segment.name for segment in spec.segments],
                      "endpoint": wireguard.endpoint or ""})
        edges.append({"id": "vpn", "source": "vpn", "target": ROUTER, "kind": "vpn", "label": f"udp/{wireguard.port}"})
    for host in spec.hosts:
        nodes.append({"id": f"host:{host.name}", "kind": "host", "parent": f"seg:{host.segment}", "label": host.name,
                      "address": str(host.address), "os": host.os, "roles": [ref.name for ref in host.roles],
                      "cores": host.cores, "memory": host.memory, "disk": host.disk, **state(host.name)})
    host_names = {host.name for host in spec.hosts}

    def node_of(endpoint: str) -> str:
        """A policy endpoint's node: the host's box, or the whole segment."""
        return f"host:{endpoint}" if endpoint in host_names else f"seg:{endpoint}"

    for i, rule in enumerate(spec.policy):
        label = f"{rule.proto} {_ports(rule.ports)}" if rule.ports else rule.proto
        edges.append({"id": f"rule:{i}", "source": node_of(rule.from_), "target": node_of(rule.to), "kind": "policy",
                      "label": label, "description": rule.description or ""})
    # VMs the plan removes are no longer in the spec: show them so the overlay can mark them.
    for host, action in actions.items():
        if action["action"] == "remove" and host != ROUTER and host not in host_names:
            nodes.append({"id": f"host:{host}", "kind": "host", "parent": None, "label": host, "address": "",
                          "os": action.get("os") or "", "roles": [], "change": "remove", "vmid": action.get("vmid")})
    summary = {}
    if plan is not None:
        for node in nodes:
            if "change" in node:
                summary[node["change"]] = summary.get(node["change"], 0) + 1
    return {"range": spec.name, "nodes": nodes, "edges": edges, "changes": summary if plan is not None else None}
