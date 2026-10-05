"""Validation: schema (spec.py) plus checks against the live cluster, before any change (FR-02)."""

from __future__ import annotations

import ipaddress

from .baseline import baseline_for, load_baseline
from .isos import installer_iso, list_isos
from .cluster import load_catalog, range_vms, templates, vlans_in_use
from .errors import ValorError
from .pve import PVE
from .roles import load_role, role_env
from .spec import RangeSpec

HOST_FAMILIES = ("debian", "rhel", "windows")
NO_BASELINE = "baseline 'none': no hardening (compliance controls) on any host"


def validate_cluster(pve: PVE, spec: RangeSpec) -> dict:
    cfg = pve.cfg
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(loc, msg, hint=None):
        errors.append({"location": loc, "message": msg, **({"hint": hint} if hint else {})})

    catalog = load_catalog(cfg)
    tpls = templates(pve, catalog)
    used_os = {spec.router.os, *(h.os for h in spec.hosts if h.install == "template")}
    for hi, h in enumerate(spec.hosts):
        if h.install != "iso":
            continue
        entry = catalog.get(h.os or "", {})
        if not entry.get("install_iso"):
            err(f"hosts.{hi}.install", f"install: iso is not available for {h.os}",
                "Supported: " + ", ".join(k for k, v in catalog.items() if v.get("install_iso")) +
                " (Windows always installs from its ISO when the template is built)")
        elif installer_iso(pve, entry["install_iso"]) is None:
            err(f"hosts.{hi}.install", f"the installer ISO for {h.os} is not in the ISO library",
                f"Download '{entry['install_iso']}' from the catalog in the ISO library first.")
    for os_name in sorted(used_os):
        if os_name not in catalog:
            err("os", f"unknown OS '{os_name}'", f"Known: {', '.join(catalog)}")
        elif not tpls[os_name]["present"]:
            err("os", f"no template built for '{os_name}' yet",
                f"An administrator can build it on a Proxmox node: ./install.sh --template {os_name}")
        elif catalog[os_name].get("family") not in HOST_FAMILIES:
            err("os", f"'{os_name}' ({catalog[os_name].get('family')}) is not supported for range hosts yet")
    router_family = catalog.get(spec.router.os or "", {}).get("family")
    if router_family and router_family != "debian":
        err("router.os", f"the range router runs {spec.router.os}; routers must be Ubuntu or Debian",
            "Remove router.os to use the default")

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

    baseline = None
    try:
        baseline = load_baseline(cfg.baselines_dir, spec.baseline)
        if baseline is None:
            warnings.append({"location": "baseline", "message": NO_BASELINE})
    except ValorError as e:
        err("baseline", e.message, e.hint)

    if any(h.nested for h in spec.hosts):
        flags = set(str(pve.node_status().get("cpuinfo", {}).get("flags", "")).split())
        if not flags & {"vmx", "svm"}:
            err("hosts", f"node {cfg.node} does not offer hardware virtualization (vmx/svm), so 'nested: true' "
                         "cannot work", "Enable VT-x/AMD-V in the firmware and nested KVM on the node.")
    library = None
    for hi, h in enumerate(spec.hosts):
        family = catalog.get(h.os or "", {}).get("family")
        if h.iso:
            if library is None:
                try:
                    library = {i["name"] for i in list_isos(pve)}
                except ValorError:
                    library = set()
            if h.iso not in library:
                err(f"hosts.{hi}.iso", f"ISO {h.iso} is not in the ISO library",
                    "Download or upload it in the ISO library (or fix the file name).")
        for ri, ref in enumerate(h.roles):
            try:
                role = load_role(cfg.roles_dir, ref.name)
                role_env(spec, h, role, ref.params)
                if family and family not in role.families:
                    err(f"hosts.{hi}.roles.{ri}", f"role {ref.name} does not support {h.os} ({family})",
                        f"It supports: {', '.join(role.families)}")
                for pname in role.meta.get("wait_for") or []:
                    target = ref.params.get(pname)
                    if target and not any(x.name == target for x in spec.hosts):
                        err(f"hosts.{hi}.roles.{ri}.params.{pname}", f"'{target}' is not a host of this range")
            except ValorError as e:
                err(f"hosts.{hi}.roles.{ri}", e.message, e.hint)
    if baseline is not None and (getattr(baseline, "families", None) or ["debian"]) == ["windows"]:
        err("baseline", f"'{spec.baseline}' is the Windows baseline; `baseline` names the range's Linux baseline",
            "Use ubuntu-l1 (the default, or leave the line out): Windows hosts get windows-l1 automatically. "
            "'none' would turn hardening off for every host.")
        baseline = None
    if baseline is not None:
        bfam = getattr(baseline, "families", None) or ["debian"]
        for os_name in sorted(used_os):
            fam = catalog.get(os_name, {}).get("family")
            if fam == "windows":
                try:
                    baseline_for(cfg.baselines_dir, spec.baseline, "windows")     # Windows hosts use windows-l1
                except ValorError as e:
                    err("baseline", e.message)
            elif fam and fam not in bfam:
                err("baseline", f"baseline {spec.baseline} does not support {os_name} ({fam})")

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
