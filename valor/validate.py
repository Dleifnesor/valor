# *checks everything twice with a tiny clipboard* OwO valor/validate.py checks a spec against the REAL cluster! ^w^
# spec.py already makes sure a spec makes sense on its own. This file asks the cluster: are the templates built?
# Is the VLAN free? Is there enough memory? Do the roles support the OS? Does the baseline fit? Can a role reach the
# host it waits for? Every problem gets a location in the spec and, when possible, a hint to fix it. The UI shows
# them, and the AI builder reads them to fix its own spec *wags tail*
"""Validation: schema (spec.py) plus checks against the live cluster, before any change (FR-02).

spec.py already guarantees the spec is consistent with itself. Here VALOR checks it against what actually
exists: are the templates built, is the VLAN free, does the node have the memory, do the roles support the OS,
does the baseline match, can a role reach the host it waits for. The result lists every problem with a location
in the spec and, where possible, a hint on how to fix it - the UI shows it and the AI builder reads it.
"""

# Lazy type hints UwU
from __future__ import annotations

# ipaddress for overlap checks >w<
import ipaddress

# compliance, baseline loaders, ISO lookups, cluster helpers, ValorError, PVE, role loading/env, RangeSpec OwO
from . import compliance
from .baseline import baseline_for, load_baseline
from .isos import installer_iso, list_isos
from .cluster import load_catalog, range_vms, templates, vlans_in_use
from .errors import ValorError
from .pve import PVE
from .roles import load_role, role_env
from .spec import RangeSpec

# HOST_FAMILIES: OS families range hosts may use. NO_BASELINE: the warning shown for `baseline: none` ^w^
HOST_FAMILIES = ("debian", "rhel", "windows")
NO_BASELINE = "baseline 'none': no hardening (compliance controls) on any host"


# validate_cluster: run every check in a fixed order and return {"ok", "errors", "warnings"} UwU
def validate_cluster(pve: PVE, spec: RangeSpec) -> dict:
    """{"ok": bool, "errors": [...], "warnings": [...]}; each entry has a location, a message and maybe a hint."""
    checks = _ClusterChecks(pve, spec)
    checks.install_from_iso()
    checks.operating_systems()
    checks.bridges()
    checks.segments()
    baseline = checks.load_baseline()
    checks.nested_virtualization()
    checks.isos_and_roles()
    checks.baseline_fits_hosts(baseline)
    checks.compliance()
    checks.vm_names()
    checks.resources()
    return {"ok": not checks.errors, "errors": checks.errors, "warnings": checks.warnings}


# _ClusterChecks: one method per kind of check, each adding to errors/warnings >w<
class _ClusterChecks:
    """One method per kind of check; each adds to `errors` / `warnings`. They run in a fixed order, so the list
    reads the same way every time (the AI fixes them top to bottom)."""

    # Keep the client, config, spec, empty lists and the OS catalog OwO
    def __init__(self, pve: PVE, spec: RangeSpec):
        self.pve = pve
        self.cfg = pve.cfg
        self.spec = spec
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        self.catalog = load_catalog(self.cfg)
        # OSes that need a template: the router's and every host that is cloned (not installed from an ISO).
        self.used_os = {spec.router.os, *(host.os for host in spec.hosts if host.install == "template")}

    # error(): add one problem (with a hint when there is one) ^w^
    def error(self, location: str, message: str, hint: str | None = None) -> None:
        self.errors.append({"location": location, "message": message, **({"hint": hint} if hint else {})})

    # family(): an OS's family from the catalog (None for an unknown OS) UwU
    def family(self, os_name: str | None) -> str | None:
        return self.catalog.get(os_name or "", {}).get("family")

    # ---- Operating systems and templates ----

    # install_from_iso: hosts with `install: iso` need an OS that supports it AND its installer ISO in the library >w<
    def install_from_iso(self) -> None:
        """`install: iso` needs an OS with a kickstart installer and that installer ISO in the library."""
        for i, host in enumerate(self.spec.hosts):
            if host.install != "iso":
                continue
            entry = self.catalog.get(host.os or "", {})
            if not entry.get("install_iso"):
                supported = ", ".join(name for name, os_entry in self.catalog.items() if os_entry.get("install_iso"))
                self.error(f"hosts.{i}.install", f"install: iso is not available for {host.os}",
                           f"Supported: {supported} (Windows always installs from its ISO when the template is built)")
            elif installer_iso(self.pve, entry["install_iso"]) is None:
                self.error(f"hosts.{i}.install", f"the installer ISO for {host.os} is not in the ISO library",
                           f"Download '{entry['install_iso']}' from the catalog in the ISO library first.")

    # operating_systems: every OS used must be known, have a built template, and be a supported family; the router
    # must run Ubuntu or Debian OwO
    def operating_systems(self) -> None:
        built = templates(self.pve, self.catalog)
        for os_name in sorted(self.used_os):
            if os_name not in self.catalog:
                self.error("os", f"unknown OS '{os_name}'", f"Known: {', '.join(self.catalog)}")
            elif not built[os_name]["present"]:
                self.error("os", f"no template built for '{os_name}' yet",
                           f"An administrator can build it on a Proxmox node: ./install.sh --template {os_name}")
            elif self.family(os_name) not in HOST_FAMILIES:
                self.error("os", f"'{os_name}' ({self.family(os_name)}) is not supported for range hosts yet")
        router_family = self.family(self.spec.router.os)
        if router_family and router_family != "debian":
            self.error("router.os", f"the range router runs {self.spec.router.os}; routers must be Ubuntu or Debian",
                       "Remove router.os to use the default")

    # ---- Network ----

    # bridges: the segment bridge must exist and be VLAN-aware; the uplink bridge must exist ^w^
    def bridges(self) -> None:
        bridges = {iface["iface"]: iface for iface in self.pve.network() if iface.get("type") == "bridge"}
        segment_bridge, uplink_bridge = self.cfg.segment_bridge, self.cfg.uplink_bridge
        if segment_bridge not in bridges:
            self.error("network", f"segment bridge {segment_bridge} is missing or not usable by the engine",
                       "Re-run the installer on the range node to recreate it")
        elif not bridges[segment_bridge].get("bridge_vlan_aware"):
            self.error("network", f"{segment_bridge} is not VLAN-aware")
        if uplink_bridge not in bridges:
            self.error("network", f"uplink bridge {uplink_bridge} is missing or not usable by the engine")

    def segments(self) -> None:
        """Each segment's VLAN must be in VALOR's range and not taken by another range; its subnet must not
        overlap a network of the cluster itself (reserved_networks), or range traffic could reach it."""
        cfg = self.cfg
        used = vlans_in_use(self.pve)
        # For each segment: the VLAN must be in VALOR's allowed range and not used by another range (with some free VLANs
        # suggested), and its network must not overlap the cluster's own networks UwU
        for i, segment in enumerate(self.spec.segments):
            if not cfg.vlan_min <= segment.vlan <= cfg.vlan_max:
                self.error(f"segments.{i}.vlan",
                           f"VLAN {segment.vlan} is outside the range VALOR may use ({cfg.vlan_min}-{cfg.vlan_max})")
            owner = used.get(segment.vlan)
            if owner and owner != self.spec.name:
                free = [vlan for vlan in range(cfg.vlan_min, cfg.vlan_max + 1) if vlan not in used][:5]
                self.error(f"segments.{i}.vlan", f"VLAN {segment.vlan} is already used by range '{owner}'",
                           f"Free VLANs include {free}")
            for net in cfg.reserved_networks:
                if segment.cidr.overlaps(ipaddress.IPv4Network(net, strict=False)):
                    self.error(f"segments.{i}.cidr",
                               f"{segment.cidr} overlaps reserved network {net} (a network of the cluster itself)",
                               "Pick another private range, e.g. 10.1xx.0.0/24")

    # ---- Hosts: nested virtualization, ISOs, roles ----

    # nested_virtualization: hosts with `nested: true` need a CPU with vmx (Intel) or svm (AMD) >w<
    def nested_virtualization(self) -> None:
        if not any(host.nested for host in self.spec.hosts):
            return
        flags = set(str(self.pve.node_status().get("cpuinfo", {}).get("flags", "")).split())
        if not flags & {"vmx", "svm"}:
            self.error("hosts", f"node {self.cfg.node} does not offer hardware virtualization (vmx/svm), so "
                                "'nested: true' cannot work",
                       "Enable VT-x/AMD-V in the firmware and nested KVM on the node.")

    # isos_and_roles: attached ISOs must be in the library (listed once, lazily), and every role must check out OwO
    def isos_and_roles(self) -> None:
        library = None                  # ISO names, listed once and only when a host attaches an ISO
        for i, host in enumerate(self.spec.hosts):
            if host.iso:
                if library is None:
                    try:
                        library = {iso["name"] for iso in list_isos(self.pve)}
                    except ValorError:
                        library = set()
                if host.iso not in library:
                    self.error(f"hosts.{i}.iso", f"ISO {host.iso} is not in the ISO library",
                               "Download or upload it in the ISO library (or fix the file name).")
            for j, ref in enumerate(host.roles):
                try:
                    self._role(i, host, j, ref)
                except ValorError as e:
                    self.error(f"hosts.{i}.roles.{j}", e.message, e.hint)

    # _role: one role on one host: it must exist, its parameters must be valid, it must support the host's OS family... ^w^
    def _role(self, i: int, host, j: int, ref) -> None:
        role = load_role(self.cfg.roles_dir, ref.name)
        role_env(self.spec, host, role, ref.params)            # raises on missing or invalid parameters
        family = self.family(host.os)
        if family and family not in role.families:
            self.error(f"hosts.{i}.roles.{j}", f"role {ref.name} does not support {host.os} ({family})",
                       f"It supports: {', '.join(role.families)}")
        # A role that waits for another host (e.g. ad-member waits for its DC) would wait until it times out if
        # the policy doesn't let it reach that host - catch that here, before anything is built.
        # ...and every host it waits for (`wait_for` params, e.g. an AD member's domain controller) must exist and be
        # reachable under the firewall policy UwU
        for param in role.meta.get("wait_for") or []:
            target = ref.params.get(param)
            if target and not any(other.name == target for other in self.spec.hosts):
                self.error(f"hosts.{i}.roles.{j}.params.{param}", f"'{target}' is not a host of this range")
            elif target and not self.spec.reaches(host.name, target):
                target_segment = self.spec.host(target).segment
                self.error(f"hosts.{i}.roles.{j}.params.{param}",
                           f"role {ref.name} on {host.name} needs {target}, but no policy rule lets {host.segment} "
                           f"reach {target_segment}",
                           f"Add a policy rule from {host.segment} to {target_segment} (or between the two hosts) for "
                           f"the traffic the role needs - Active Directory needs proto: any.")

    # ---- Baseline and compliance ----

    # load_baseline: the range's baseline; "none" gives a warning, a broken one gives an error >w<
    def load_baseline(self):
        """The range's (Linux) baseline, or None for 'none' or when it can't be loaded (that is an error)."""
        try:
            baseline = load_baseline(self.cfg.baselines_dir, self.spec.baseline)
        except ValorError as e:
            self.error("baseline", e.message, e.hint)
            return None
        if baseline is None:
            self.warnings.append({"location": "baseline", "message": NO_BASELINE})
        return baseline

    # baseline_fits_hosts: `baseline` must be a LINUX profile (Windows hosts get the Windows twin automatically), and it
    # must support every Linux OS used OwO
    def baseline_fits_hosts(self, baseline) -> None:
        """`baseline` names the Linux baseline; Windows hosts get its Windows counterpart automatically."""
        spec = self.spec
        if baseline is not None and (getattr(baseline, "families", None) or ["debian"]) == ["windows"]:
            self.error("baseline",
                       f"'{spec.baseline}' is the Windows baseline; `baseline` names the range's Linux baseline",
                       "Use ubuntu-l1 (the default, or leave the line out): Windows hosts get windows-l1 "
                       "automatically. 'none' would turn hardening off for every host.")
            return
        if baseline is None:
            return
        baseline_families = getattr(baseline, "families", None) or ["debian"]
        for os_name in sorted(self.used_os):
            family = self.family(os_name)
            if family == "windows":
                try:
                    baseline_for(self.cfg.baselines_dir, spec.baseline, "windows")
                except ValorError as e:
                    self.error("baseline", e.message)
            elif family and family not in baseline_families:
                self.error("baseline", f"baseline {spec.baseline} does not support {os_name} ({family})")

    # compliance: chosen frameworks need a baseline at least as strong as they require, plus the design warnings ^w^
    def compliance(self) -> None:
        frameworks = self.spec.compliance.frameworks if self.spec.compliance else []
        if not frameworks:
            return
        try:
            needed = compliance.required_profile(self.cfg.baselines_dir, frameworks)
            current = compliance.STRENGTH.get(self.spec.baseline)
            if current is not None and current < compliance.STRENGTH.get(needed, 0):
                self.error("baseline", f"compliance {', '.join(frameworks)} needs baseline {needed}; the range uses "
                                       f"'{self.spec.baseline}'",
                           f"Set baseline: {needed} (Windows hosts then get its Windows counterpart). It adds account "
                           "lockout, password policy, login notices, idle-session limits, audit rules and time sync.")
            families = {host.name: self.catalog.get(host.os or "", {}).get("family", "debian")
                        for host in self.spec.hosts}
            self.warnings += compliance.design_warnings(self.cfg.baselines_dir, self.spec, families)
        except ValorError as e:
            self.error("compliance", e.message, e.hint)

    # ---- Names and resources ----

    # vm_names: VM names are "<range>-<host>", so two ranges could clash ("a-b" + "c" vs "a" + "b-c"); catch it UwU
    def vm_names(self) -> None:
        """VM names are <range>-<host>; another range must not already use one (e.g. range "a-b" host "c" and
        range "a" host "b-c")."""
        spec = self.spec
        others = {vm.name for vm in range_vms(self.pve, with_config=False) if f"valor-range-{spec.name}" not in vm.tags}
        for name in ["rtr", *(host.name for host in spec.hosts)]:
            if f"{spec.name}-{name}" in others:
                self.error("name", f"VM name {spec.name}-{name} is already used by another range")

    # resources: enough free memory on the node for the whole range (error), and at least 5 GiB free on storage
    # (warning) >w< *ticks the last box*
    def resources(self) -> None:
        needed = self.spec.router.memory + sum(host.memory for host in self.spec.hosts)
        free = self.pve.node_status()["memory"]["free"] // 2**20
        if needed > free:
            self.error("resources", f"range needs {needed} MiB RAM but only {free} MiB is free on {self.cfg.node}")
        if self.pve.storage_status().get("avail", 0) < 5 * 2**30:
            self.warnings.append({"location": "resources", "message": f"storage {self.cfg.storage} has under 5 GiB free"})
