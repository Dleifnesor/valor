# *photocopies a whole lab with tiny paws* OwO valor/blueprints.py: one spec, MANY ranges! ^w^
# Teachers love this: make one lab, then give every student an identical copy. A blueprint is a normal spec kept in
# <data>/blueprints/<id>.yaml. Deploying writes one range spec per copy with its own name, its own free VLANs and
# networks (hosts keep their position: .10 stays .10), its own VPN tunnel network, and (for named students) a VPN
# peer for that student. After that each copy is just an ordinary range *wags tail*
"""Range blueprints: one spec, many ranges - e.g. an identical lab for every student.

A blueprint is a normal range spec kept in <data>/blueprints/<id>.yaml (its own name is only a label). Deploying it
writes one range spec per copy: its own name, free VLANs and networks (hosts keep their position inside each
network, so .10 stays .10), its own WireGuard tunnel network, and - when copies are made for named students - a
WireGuard peer for that student. Each copy is then an ordinary range: planned, approved, built and verified alone.
"""

# Lazy type hints UwU
from __future__ import annotations

# hashlib (the deploy hash), ipaddress (network math), re (id checks), time (stamps), Path >w<
import hashlib
import ipaddress
import re
import time
from pathlib import Path

# PyYAML writes the copies' specs OwO
import yaml

# ValorError; NAME (the range name pattern), RangeSpec, parse_spec ^w^
from .errors import ValorError
from .spec import NAME, RangeSpec, parse_spec

# BP_ID: blueprint ids are 2-32 lowercase letters/digits/dashes. MAX_COPIES: at most 50 at once. Copies' networks
# are searched from 10.100.0.0 (easy to read), tunnel networks from 10.250.0.0 UwU
BP_ID = r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$"
MAX_COPIES = 50
NET_START = ipaddress.IPv4Address("10.100.0.0")      # copies get readable 10.1xx.y.0 networks first
TUNNEL_START = ipaddress.IPv4Address("10.250.0.0")


# bp_dir: blueprints live next to the ranges folder >w<
def bp_dir(cfg) -> Path:
    return Path(cfg.ranges_dir).parent / "blueprints"


# _path: a blueprint's file, after checking the id (no path tricks) OwO
def _path(cfg, bp_id: str) -> Path:
    if not re.fullmatch(BP_ID, bp_id):
        raise ValorError("invalid_blueprint", "Blueprint ids are 2-32 lowercase letters, digits or dashes.")
    return bp_dir(cfg) / f"{bp_id}.yaml"


# save: check the spec (raises on problems), then write it via a temp file + atomic rename ^w^
def save(cfg, bp_id: str, text: str) -> RangeSpec:
    spec = parse_spec(text)                          # schema + cross checks; raises SpecError
    path = _path(cfg, bp_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n")
    tmp.replace(path)
    return spec


# load: (parsed spec, text) of a blueprint, or a clear error UwU
def load(cfg, bp_id: str) -> tuple[RangeSpec, str]:
    path = _path(cfg, bp_id)
    if not path.is_file():
        raise ValorError("no_blueprint", f"There is no blueprint '{bp_id}'.")
    text = path.read_text()
    return parse_spec(text), text


# delete: remove a blueprint's file (copies already made are untouched) >w<
def delete(cfg, bp_id: str) -> None:
    path = _path(cfg, bp_id)
    if not path.is_file():
        raise ValorError("no_blueprint", f"There is no blueprint '{bp_id}'.")
    path.unlink()


# copies_of: which ranges were made from this blueprint: their spec files start with a marker comment, so reading
# the first 400 bytes is enough OwO
def copies_of(cfg, bp_id: str) -> list[str]:
    """Ranges deployed from a blueprint (their spec files carry a header naming it)."""
    marker = f"# valor-blueprint: {bp_id}\n"
    out = []
    for path in sorted(Path(cfg.ranges_dir).glob("*.yaml")):
        try:
            with path.open() as fh:
                if marker in fh.read(400):
                    out.append(path.stem)
        except OSError:
            continue
    return out


# list_all: every blueprint with a summary for the list (invalid ones are listed too, with their error) ^w^
def list_all(cfg) -> list[dict]:
    out = []
    for path in sorted(bp_dir(cfg).glob("*.yaml")):
        try:
            spec = parse_spec(path.read_text())
        except ValorError as e:
            out.append({"id": path.stem, "valid": False, "error": e.message})
            continue
        out.append({"id": path.stem, "valid": True, "description": spec.description,
                    "segments": len(spec.segments), "hosts": len(spec.hosts),
                    "os": sorted({host.os or "default" for host in spec.hosts}),
                    "wireguard": bool(spec.access and spec.access.wireguard),
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(path.stat().st_mtime)),
                    "copies": copies_of(cfg, path.stem)})
    return out


# ---- Allocation: finding free VLANs and networks UwU
# ---- Allocation ------------------------------------------------------------------------------------------
# _taken: VLANs and networks already used: by live VMs, the cluster's reserved networks, every saved range spec
# (segments and VPN tunnels) >w<
def _taken(cfg, live_vlans: dict[int, str] | None) -> tuple[dict[int, str], list[tuple[ipaddress.IPv4Network, str]]]:
    """VLANs and networks already claimed by range specs, the cluster itself and live VMs."""
    vlans: dict[int, str] = dict(live_vlans or {})
    nets: list[tuple[ipaddress.IPv4Network, str]] = [
        (ipaddress.IPv4Network(net, strict=False), "the cluster") for net in cfg.reserved_networks
        if ipaddress.ip_network(net, strict=False).version == 4]
    for path in sorted(Path(cfg.ranges_dir).glob("*.yaml")):
        try:
            spec = parse_spec(path.read_text())
        except Exception:
            continue
        for segment in spec.segments:
            vlans.setdefault(segment.vlan, spec.name)
            nets.append((segment.cidr, spec.name))
        if spec.access and spec.access.wireguard:
            nets.append((spec.access.wireguard.network, spec.name))
    return vlans, nets


# _free_net: the first /prefix network in 10.0.0.0/8 that overlaps nothing taken, searching from `start` upward,
# then wrapping around from 10.0.0.0 OwO
def _free_net(prefix: int, start: ipaddress.IPv4Address, nets: list) -> ipaddress.IPv4Network:
    """The first /prefix in 10.0.0.0/8 that overlaps none of `nets` ([(network, owner)]), searching from
    `start` upwards and then from 10.0.0.0 up to `start`."""
    space = ipaddress.IPv4Network("10.0.0.0/8")
    first = ipaddress.IPv4Network(f"{start}/{prefix}", strict=False)
    # Two ranges of candidate start addresses, stepping by the network's size ^w^
    order = (range(int(first.network_address), int(space.broadcast_address) + 1, first.num_addresses),
             range(int(space.network_address), int(first.network_address), first.num_addresses))
    for candidates in order:
        for address in candidates:
            network = ipaddress.IPv4Network(f"{ipaddress.IPv4Address(address)}/{prefix}")
            if not any(network.overlaps(taken) for taken, _ in nets):
                return network
    raise ValorError("no_free_network", f"no free /{prefix} network left in 10.0.0.0/8")


# _move: the address at the same position in the new network (10.1.2.10 in 10.1.2.0/24 -> 10.100.0.10 in
# 10.100.0.0/24) UwU
def _move(addr, old: ipaddress.IPv4Network, new: ipaddress.IPv4Network):
    """The address at the same offset in the new network (.10 stays .10)."""
    return new.network_address + (int(addr) - int(old.network_address))


# plan_copies: one new spec per requested copy (nothing is written yet) >w<
def plan_copies(cfg, bp: RangeSpec, copies: list[dict], live_vlans: dict[int, str] | None = None,
                bp_id: str = "") -> list[dict]:
    """copies: [{"name": ..., "student": optional}] -> one new spec per copy (nothing is written)."""
    # Sanity checks: at least one copy, not too many, valid names that aren't taken, no duplicates OwO
    if not copies:
        raise ValorError("no_copies", "Choose at least one copy.")
    if len(copies) > MAX_COPIES:
        raise ValorError("too_many_copies", f"At most {MAX_COPIES} copies at a time.")
    names = [copy["name"] for copy in copies]
    for name in names:
        if not re.fullmatch(NAME, name):
            raise ValorError("invalid_name",
                             f"'{name}' is not a valid range name (2-15 lowercase letters, digits, dashes)")
        if (Path(cfg.ranges_dir) / f"{name}.yaml").exists():
            raise ValorError("range_exists", f"A range named '{name}' already exists.")
    if len(set(names)) != len(names):
        raise ValorError("duplicate_names", "Every copy needs its own name.")
    # `vlans` and `nets` are shared by all copies: each copy claims its VLANs and networks before the next one picks ^w^
    vlans, nets = _taken(cfg, live_vlans)        # each copy claims its VLANs and networks before the next one
    return [_copy(cfg, bp, copy, vlans, nets, bp_id) for copy in copies]


# _copy: build ONE copy UwU
def _copy(cfg, bp: RangeSpec, copy: dict, vlans: dict[int, str], nets: list, bp_id: str) -> dict:
    """One copy: the blueprint with its own name, VLANs, networks and tunnel network (claimed in vlans/nets)."""
    name, student = copy["name"], copy.get("student")
    # Start from the blueprint as plain data, with the new name and a description mentioning the student >w<
    data = bp.model_dump(mode="json", by_alias=True, exclude_none=True)
    data["name"] = name
    note = f" Copy for {student}." if student else ""
    data["description"] = (bp.description + note).strip() or f"Copy of blueprint {bp_id}."

    moved: dict[str, tuple[ipaddress.IPv4Network, ipaddress.IPv4Network]] = {}     # segment -> (old, new)
    allocation = {}
    # For each segment: the first free VLAN in VALOR's range, and a free network of the same size; remember old -> new OwO
    for segment in data["segments"]:
        vlan = next((candidate for candidate in range(cfg.vlan_min, cfg.vlan_max + 1) if candidate not in vlans), None)
        if vlan is None:
            raise ValorError("no_free_vlan", "no free VLAN left for the copies")
        vlans[vlan] = name
        old = ipaddress.IPv4Network(segment["cidr"])
        new = _free_net(old.prefixlen, NET_START, nets)
        nets.append((new, name))
        moved[segment["name"]] = (old, new)
        segment.update(vlan=vlan, cidr=str(new))
        allocation[segment["name"]] = {"vlan": vlan, "cidr": str(new)}
    # Move every host's address into its segment's new network ^w^
    for host in data["hosts"]:
        old, new = moved[host["segment"]]
        host["address"] = str(_move(ipaddress.IPv4Address(host["address"]), old, new))
    # Tests that target an IP address get moved too (tests that target a host name need nothing) UwU
    for test in data.get("tests", []):
        try:
            ip = ipaddress.IPv4Address(test["to"])
        except ValueError:
            continue
        for old, new in moved.values():
            if ip in old:
                test["to"] = str(_move(ip, old, new))

    # The VPN: a free tunnel network, and the student as a peer >w<
    wireguard = (data.get("access") or {}).get("wireguard")
    if wireguard:
        old = ipaddress.IPv4Network(wireguard.get("network", "10.250.0.0/24"))
        new = _free_net(old.prefixlen, TUNNEL_START, nets)
        nets.append((new, name))
        wireguard["network"] = str(new)
        if student and student not in wireguard["peers"]:
            wireguard["peers"] = [*wireguard["peers"], student]
        allocation["wireguard"] = {"network": str(new), "peers": wireguard["peers"]}

    # The copy's YAML: the marker line (for copies_of), a friendly comment, then the tidied spec OwO
    header = (f"# valor-blueprint: {bp_id}\n"
              f"# Range {name}: copy of blueprint '{bp_id}'" + (f" for {student}" if student else "") +
              f", created {time.strftime('%Y-%m-%d')}. It is an ordinary range now: edit it freely.\n")
    text = header + yaml.safe_dump(_tidy(data), sort_keys=False, default_flow_style=None, width=110)
    # Parse it once more: every copy must be a valid spec on its own ^w^
    spec = parse_spec(text)                     # the copy must be a valid spec on its own
    return {"name": name, "student": student, "allocation": allocation, "yaml": text, "spec": spec}


# _tidy: drop values that equal the defaults, so a copy reads like a hand-written spec UwU
def _tidy(data: dict) -> dict:
    """Drop values equal to the spec defaults so a copy reads like a hand-written spec."""
    defaults = {"internet": False, "description": "", "cores": 1, "memory": 1024, "disk": 10, "roles": [],
                "params": {}, "ports": [], "tests": [], "policy": [], "auto_tests": True, "apiVersion": None,
                "install": "template", "nested": False, "baseline": "ubuntu-l1"}

    # clean(): recursively drop default-valued keys (but never touch role params, and keep the top-level name) >w<
    def clean(obj, top=False):
        if isinstance(obj, dict):
            return {key: (value if key == "params" else clean(value))     # role params stay as written
                    for key, value in obj.items()
                    if not (key in defaults and value == defaults[key] and not (top and key in ("name",)))}
        if isinstance(obj, list):
            return [clean(value) for value in obj]
        return obj

    out = clean(data, top=True)
    # apiVersion always first, like a hand-written spec
    out = {"apiVersion": data.get("apiVersion", "valor/v1"), **{key: value for key, value in out.items()
                                                                if key != "apiVersion"}}
    # An empty router section isn't needed OwO
    if out.get("router") == {}:
        out.pop("router")
    return out


# deploy_hash: a fingerprint of exactly what was previewed (names, YAML and plan summaries). Deploy must send it
# back unchanged ^w^ *stamps the photocopies*
def deploy_hash(copies: list[dict]) -> str:
    """What the operator approved: the copies' specs (and plans, when the cluster was asked)."""
    key = [(copy["name"], copy["yaml"], copy.get("plan_summary")) for copy in copies]
    return hashlib.sha256(repr(key).encode()).hexdigest()[:32]
