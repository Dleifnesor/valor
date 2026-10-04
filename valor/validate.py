"""Validation: schema (spec.py) plus checks against the live cluster, before any change (FR-02)."""

from __future__ import annotations

import ipaddress

from .baseline import load_baseline
from .cluster import load_catalog, range_vms, templates, vlans_in_use
from .errors import ValorError
from .pve import PVE
from .roles import load_role, role_env
from .spec import RangeSpec


def validate_cluster(pve: PVE, spec: RangeSpec) -> dict:
    cfg = pve.cfg
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(loc, msg, hint=None):
        errors.append({"location": loc, "message": msg, **({"hint": hint} if hint else {})})

    catalog = load_catalog(cfg)
    tpls = templates(pve, catalog)
    used_os = {spec.router.os, *(h.os for h in spec.hosts)}
    for os_name in sorted(used_os):
        if os_name not in catalog:
            err("os", f"unknown OS '{os_name}'", f"Known: {', '.join(catalog)}")
        elif not tpls[os_name]["present"]:
            err("os", f"no template built for '{os_name}' yet",
                f"An administrator can build it on a Proxmox node: ./install.sh --template {os_name}")
        elif catalog[os_name].get("family") != "debian":
            err("os", f"'{os_name}' is not an apt-based OS; MVP roles and baselines support the debian family only")

    bridges = {i["iface"]: i for i in pve.network() if i.get("type") == "bridge"}
    if cfg.segment_bridge not in bridges:
        err("network", f"segment bridge {cfg.segment_bridge} is missing or not usable by the engine",
            "Re-run the installer on the range node to recreate it")
    elif not bridges[cfg.segment_bridge].get("bridge_vlan_aware"):
        err("network", f"{cfg.segment_bridge} is not VLAN-aware")
    if cfg.uplink_bridge not in bridges:
        err("network", f"uplink bridge {cfg.uplink_bridge} is missing or not usable by the engine")

    used = vlans_in_use(pve)
    for i, s in enumerate(spec.segments):
        if not cfg.vlan_min <= s.vlan <= cfg.vlan_max:
            err(f"segments.{i}.vlan", f"VLAN {s.vlan} is outside the range VALOR may use ({cfg.vlan_min}-{cfg.vlan_max})")
        owner = used.get(s.vlan)
        if owner and owner != spec.name:
            free = [v for v in range(cfg.vlan_min, cfg.vlan_max + 1) if v not in used][:5]
            err(f"segments.{i}.vlan", f"VLAN {s.vlan} is already used by range '{owner}'",
                f"Free VLANs include {free}")
        for net in cfg.reserved_networks:
            if s.cidr.overlaps(ipaddress.IPv4Network(net, strict=False)):
                err(f"segments.{i}.cidr", f"{s.cidr} overlaps reserved network {net} (a network of the cluster itself)",
                    "Pick another private range, e.g. 10.1xx.0.0/24")

    try:
        baseline = load_baseline(cfg.baselines_dir, spec.baseline)
        if baseline is None:
            warnings.append({"location": "baseline", "message": "baseline 'none': no compliance controls will be applied"})
    except ValorError as e:
        err("baseline", e.message, e.hint)

    for hi, h in enumerate(spec.hosts):
        for ri, ref in enumerate(h.roles):
            try:
                role = load_role(cfg.roles_dir, ref.name)
                role_env(spec, h, role, ref.params)
            except ValorError as e:
                err(f"hosts.{hi}.roles.{ri}", e.message, e.hint)

    others = {vm.name for vm in range_vms(pve, with_config=False) if f"valor-range-{spec.name}" not in vm.tags}
    for name in ["rtr", *(h.name for h in spec.hosts)]:
        if f"{spec.name}-{name}" in others:
            err("name", f"VM name {spec.name}-{name} is already used by another range")

    need = spec.router.memory + sum(h.memory for h in spec.hosts)
    ns = pve.node_status()
    free = ns["memory"]["free"] // 2**20
    if need > free:
        err("resources", f"range needs {need} MiB RAM but only {free} MiB is free on {cfg.node}")
    st = pve.storage_status()
    if st.get("avail", 0) < 5 * 2**30:
        warnings.append({"location": "resources", "message": f"storage {cfg.storage} has under 5 GiB free"})
    return {"ok": not errors, "errors": errors, "warnings": warnings}
