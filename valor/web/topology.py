# *draws little boxes and arrows with paws* OwO valor/web/topology.py turns a range spec into a GRAPH for the web
# UI's map: nodes (Internet, router, segments, VPN, VMs) and edges (uplink, gateways, internet access, firewall
# rules). Given a plan, it also marks each node with what the plan will do to it (create/update/replace/remove) ^w^
"""Topology graph of a range for the web UI's map: nodes, edges and, given a plan, what each change does."""

# Lazy type hints UwU
from __future__ import annotations

# ROUTER is the router's fixed host name ("rtr"); RangeSpec is the parsed spec model >w<
from ..spec import ROUTER, RangeSpec

# CHANGE maps the engine's plan actions onto the four colors the map knows: create, replace, update, keep (plus
# remove). "converge" (re-run roles) and "start" both count as update; "restamp" (only tags change) counts as keep OwO
# Plan actions as the map shows them.
CHANGE = {"create": "create", "replace": "replace", "update": "update", "converge": "update", "start": "update",
          "restamp": "keep", "keep": "keep", "remove": "remove"}


# _ports: a list of ports as text, like [80, 443] -> "80,443", for edge labels ^w^
def _ports(ports: list) -> str:
    return ",".join(str(port) for port in ports)


# build(): make the graph *wags tail*
#   spec  the range spec; vms = live VM info per host (vmid + running/stopped); plan = the engine's plan or None
# Returns {"range", "nodes", "edges", "changes"} where changes counts nodes per change kind (only with a plan) UwU
def build(spec: RangeSpec, vms: dict[str, dict] | None = None, plan: dict | None = None) -> dict:
    """vms: host -> {"vmid", "status"}; plan: the engine's plan (adds a `change` to every node).

    Node ids: "internet", "rtr", "seg:<name>", "host:<name>". Hosts carry `parent` = their segment node."""
    # No live info = an empty dict; index the plan's actions by host name for quick lookups >w<
    vms = vms or {}
    actions = {action["host"]: action for action in (plan or {}).get("actions", [])}

    # state(): the extra fields for one VM's node: its vmid and status (when it exists) and, with a plan, its
    # `change` (and the plan's reasons for it, e.g. "memory 1024 -> 2048") ^w^
    def state(host: str) -> dict:
        """The VM's live state and, with a plan, what the plan does to it."""
        out = {}
        if host in vms:
            out.update(vmid=vms[host].get("vmid"), status=vms[host].get("status"))
        if plan is not None:
            action = actions.get(host)
            # Hosts the plan doesn't mention are "keep" (nothing happens to them) OwO
            out["change"] = CHANGE.get(action["action"], "keep") if action else "keep"
            if action and action.get("reasons"):
                out["reasons"] = action["reasons"]
        return out

    # The first two nodes are always the Internet and the router. The router node also lists its gateways (one
    # address per segment) so the map can show them *boops the router*
    router = spec.router
    nodes = [
        {"id": "internet", "kind": "internet", "label": "Internet"},
        {"id": ROUTER, "kind": "router", "label": f"{spec.name}-{ROUTER}", "os": router.os, "cores": router.cores,
         "memory": router.memory, "disk": router.disk,
         "gateways": [str(segment.gateway) for segment in spec.segments], **state(ROUTER)},
    ]
    # The router's uplink to the Internet (NAT = the range shares the router's outside address) >w<
    edges = [{"id": "uplink", "source": "internet", "target": ROUTER, "kind": "uplink", "label": "NAT uplink"}]
    # One node per segment (a VLAN with its own subnet), connected to the router by a "gateway" edge labeled with the
    # gateway address. Segments allowed on the internet also get an "egress" edge to the Internet node ^w^
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
    # WireGuard VPN access (if the spec has it): a "vpn" node with its port, peers, network and which segments it can
    # reach (all of them when `reach` isn't set), connected to the router UwU
    wireguard = spec.access.wireguard if spec.access else None
    if wireguard:
        nodes.append({"id": "vpn", "kind": "vpn", "label": "WireGuard", "port": wireguard.port,
                      "peers": list(wireguard.peers), "cidr": str(wireguard.network),
                      "reach": wireguard.reach or [segment.name for segment in spec.segments],
                      "endpoint": wireguard.endpoint or ""})
        edges.append({"id": "vpn", "source": "vpn", "target": ROUTER, "kind": "vpn", "label": f"udp/{wireguard.port}"})
    # One node per VM, inside (parent =) its segment, with its address, OS, roles, size and live/plan state *nuzzles*
    for host in spec.hosts:
        nodes.append({"id": f"host:{host.name}", "kind": "host", "parent": f"seg:{host.segment}", "label": host.name,
                      "address": str(host.address), "os": host.os, "roles": [ref.name for ref in host.roles],
                      "cores": host.cores, "memory": host.memory, "disk": host.disk, **state(host.name)})
    # The names of all hosts, to tell host endpoints from segment endpoints below OwO
    host_names = {host.name for host in spec.hosts}

    # node_of: a firewall rule's endpoint is either a host name or a segment name; return the matching node id >w<
    def node_of(endpoint: str) -> str:
        """A policy endpoint's node: the host's box, or the whole segment."""
        return f"host:{endpoint}" if endpoint in host_names else f"seg:{endpoint}"

    # One "policy" edge per firewall rule. The id is "rule:<index>" so the UI can find the rule in the spec again;
    # the label is "tcp 80,443" (or just the protocol when the rule has no ports) ^w^
    for i, rule in enumerate(spec.policy):
        label = f"{rule.proto} {_ports(rule.ports)}" if rule.ports else rule.proto
        edges.append({"id": f"rule:{i}", "source": node_of(rule.from_), "target": node_of(rule.to), "kind": "policy",
                      "label": label, "description": rule.description or ""})
    # VMs the plan removes are no longer in the spec: show them so the overlay can mark them.
    for host, action in actions.items():
        if action["action"] == "remove" and host != ROUTER and host not in host_names:
            nodes.append({"id": f"host:{host}", "kind": "host", "parent": None, "label": host, "address": "",
                          "os": action.get("os") or "", "roles": [], "change": "remove", "vmid": action.get("vmid")})
    # With a plan: count the nodes per change kind for the summary bar ("2 create, 1 update") UwU *counts on paws*
    summary = {}
    if plan is not None:
        for node in nodes:
            if "change" in node:
                summary[node["change"]] = summary.get(node["change"], 0) + 1
    return {"range": spec.name, "nodes": nodes, "edges": edges, "changes": summary if plan is not None else None}
