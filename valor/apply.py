"""Idempotent build (FR-04 .. FR-07, FR-09, FR-10, FR-14)."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

from . import credentials, windows
from .baseline import baseline_for, bundle, load_baseline, parse_results
from .cluster import range_tag, range_vms, render_description, template_ids
from .errors import ValorError
from .netpolicy import render, router_script
from .plan import Desired, desired_state, make_plan
from .pve import PVE
from .roles import build_script, load_role, role_env
from .spec import ROUTER, RangeSpec, spec_hash
from .validate import validate_cluster

TEMPLATE_DISK_GIB = 8
WINDOWS_TEMPLATE_DISK_GIB = 64
PARALLEL_HOSTS = 4


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Steps:
    """Records every step (for structured errors and the build journal) and emits progress events."""

    def __init__(self, emit):
        self.emit = emit
        self.records: list[dict] = []
        self.lock = threading.Lock()

    @contextmanager
    def step(self, name: str, host: str | None = None):
        rec = {"step": name, "host": host, "status": "running", "started": now()}
        with self.lock:
            self.records.append(rec)
        self.emit("step_start", step=name, host=host)
        t0 = time.time()
        try:
            yield rec
        except Exception as e:
            rec.update(status="failed", seconds=round(time.time() - t0, 1),
                       error=getattr(e, "message", str(e)))
            self.emit("step_failed", step=name, host=host, error=rec["error"])
            raise
        rec.update(status="done", seconds=round(time.time() - t0, 1))
        self.emit("step_done", step=name, host=host, seconds=rec["seconds"])

    def completed(self) -> list[str]:
        return [f"{r['step']}{' [' + r['host'] + ']' if r['host'] else ''}" for r in self.records if r["status"] == "done"]


def vm_tags(spec: RangeSpec, d: Desired) -> str:
    return ";".join(["valor", range_tag(spec.name), f"valor-spec-{spec_hash(spec)[:12]}",
                     f"valor-os-{d.os.replace('.', '-')}"] + (["valor-router"] if d.is_router else []))


def vm_meta(spec: RangeSpec, d: Desired, conv: str | None) -> dict:
    return {"range": spec.name, "host": d.host, "role": "router" if d.is_router else "host",
            "spec": spec_hash(spec), "hw": d.hw_hash, "hw_spec": d.hw, "conv": conv, "applied": now()}


def create_vm(pve: PVE, spec: RangeSpec, d: Desired, taken: set[int]) -> int:
    cfg = pve.cfg
    vmid = pve.allocate_vmid(taken)
    taken.add(vmid)
    name = f"{spec.name}-{d.host}"
    pve.clone(d.template, vmid, name, render_description(vm_meta(spec, d, None), spec_hash(spec)[:12]))
    if d.family == "windows":
        nic = d.nics[0]
        pve.update_config(vmid, cores=d.cores, memory=d.memory, onboot=0, tags=vm_tags(spec, d), vga="std",
                          net0=f"{nic.get('model', 'e1000e')},bridge={nic['bridge']}" + (f",tag={nic['vlan']}" if nic["vlan"] else ""))
        if d.disk > WINDOWS_TEMPLATE_DISK_GIB:
            pve.resize(vmid, "sata0", d.disk)
        pve.power(vmid, "start")
        return vmid
    params: dict = {
        "cores": d.cores, "memory": d.memory, "onboot": 0, "tags": vm_tags(spec, d),
        "nameserver": " ".join(cfg.nameservers), "ciuser": cfg.guest_user, "ciupgrade": 0,
        "vga": d.hw.get("display", "std"), "serial0": "socket",
    }
    pub = Path(cfg.ssh_public_key)
    if pub.exists():
        params["sshkeys"] = pub.read_text().strip() + "\n"
    for i, nic in enumerate(d.nics):
        params[f"net{i}"] = f"virtio,bridge={nic['bridge']}" + (f",tag={nic['vlan']}" if nic["vlan"] else "")
        ip = "ip=dhcp" if nic["ip"] == "dhcp" else f"ip={nic['ip']}" + (f",gw={nic['gw']}" if nic["gw"] else "")
        params[f"ipconfig{i}"] = ip
    pve.update_config(vmid, **params)
    if d.disk > TEMPLATE_DISK_GIB:
        pve.resize(vmid, "scsi0", d.disk)
    pve.power(vmid, "start")
    return vmid


def stamp(pve: PVE, spec: RangeSpec, d: Desired, vmid: int, conv: str | None) -> None:
    pve.update_config(vmid, description=render_description(vm_meta(spec, d, conv), spec_hash(spec)[:12]),
                      tags=vm_tags(spec, d))


def router_interfaces(pve: PVE, vmid: int, n_segments: int, spec: RangeSpec) -> tuple[dict[str, str], str]:
    """Map router NICs to interface names inside the guest by MAC address."""
    cfg = pve.vm_config(vmid)
    macs = {}
    for i in range(n_segments + 1):
        net = cfg.get(f"net{i}", "")
        mac = next((p.split("=")[1].lower() for p in net.split(",") if p.lower().startswith("virtio=")), None)
        if not mac:
            raise ValorError("router_nic_missing", f"router NIC net{i} has no MAC", host=ROUTER)
        macs[i] = mac
    res = pve.exec(vmid, ["ip", "-j", "link"], timeout=30)
    if not res.ok:
        raise ValorError("router_inspect_failed", "could not list router interfaces", host=ROUTER, details=res.tail())
    by_mac = {l.get("address", "").lower(): l["ifname"] for l in json.loads(res.out)}
    names = {}
    for i, mac in macs.items():
        if mac not in by_mac:
            raise ValorError("router_nic_missing", f"interface with MAC {mac} (net{i}) not found in the router", host=ROUTER)
        names[i] = by_mac[mac]
    ifmap = {s.name: names[i + 1] for i, s in enumerate(spec.segments)}
    return ifmap, names[0]


def load_router_policy(pve: PVE, spec: RangeSpec, vmid: int, ifmap, uplink, build_egress: bool) -> None:
    rules = render(spec, ifmap, uplink, build_egress=build_egress, spec_id=spec_hash(spec)[:12],
                   reserved=pve.cfg.reserved_networks)
    res = pve.script(vmid, router_script(rules), timeout=600)
    if not res.ok:
        raise ValorError("router_policy_failed", "loading the router's nftables policy failed", host=ROUTER,
                         details=res.tail(), hint="Check the generated ruleset (valor plan --show-policy).")


def set_login(pve: PVE, d: Desired, vmid: int, login: dict | None, steps: Steps) -> None:
    """Console password for the guest user, passed on stdin (never on a command line or in the VM config)."""
    if not login:
        return
    with steps.step("console login", d.host):
        res = pve.exec(vmid, ["/usr/sbin/chpasswd"], input_data=f"{login['username']}:{login['password']}\n", timeout=60)
        if not res.ok:
            raise ValorError("login_failed", f"could not set the console password on {d.host}", host=d.host,
                             details=res.err[-300:])


WINDOWS_READY = ("$s = (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Setup\\State' "
                 "-ErrorAction SilentlyContinue).ImageState; if ($s -eq 'IMAGE_STATE_COMPLETE') { 'READY' } else { $s }")


def windows_wait_ready(pve: PVE, vmid: int, timeout: int = 1500) -> None:
    """After sysprep a clone runs specialize and OOBE (with a reboot) before it is usable; the agent answers
    earlier than that, so wait for the setup state, not only the agent."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            pve.wait_agent(vmid, 600)
            if "READY" in pve.ps(vmid, WINDOWS_READY, timeout=60).out:
                return
        except ValorError:
            pass
        time.sleep(10)
    raise ValorError("windows_not_ready", f"Windows in VM {vmid} did not finish its first-boot setup",
                     hint="Open the VM console in VALOR or Proxmox to see where setup stopped.")


def windows_reboot(pve: PVE, vmid: int) -> None:
    pve.power(vmid, "shutdown", wait=660, timeout=600, forceStop=1)
    pve.power(vmid, "start")
    windows_wait_ready(pve, vmid)


def windows_prep(pve: PVE, spec: RangeSpec, d: Desired, vmid: int, login: dict | None, steps: Steps) -> None:
    """What cloud-init does for Linux: Administrator password (the range login), static IP, DNS, hostname."""
    seg = spec.segment(d.segment)
    script = windows.prep_script(d.host, d.address, seg.cidr.prefixlen, str(seg.gateway), list(pve.cfg.nameservers),
                                 login["password"] if login else None)
    with steps.step("windows: name, address, login", d.host):
        for _ in range(3):
            res = pve.ps(vmid, script, timeout=600)
            if not res.ok or windows.PREP_OK not in res.out:
                raise ValorError("windows_prep_failed", f"preparing Windows on {d.host} failed", host=d.host,
                                 details=res.tail().replace(login["password"], "***") if login else res.tail())
            if windows.REBOOT not in res.out:
                return
            windows_reboot(pve, vmid)
        raise ValorError("windows_prep_failed", f"{d.host} still asks for a reboot after renaming", host=d.host)


def run_roles(pve: PVE, spec: RangeSpec, d: Desired, vmid: int, steps: Steps, login: dict | None = None) -> list[dict]:
    cfg = pve.cfg
    host = spec.host(d.host)
    out = []
    for ref in host.roles:
        with steps.step(f"role {ref.name}", d.host):
            role = load_role(cfg.roles_dir, ref.name)
            env = role_env(spec, host, role, ref.params)
            changed = False
            if d.family == "windows":
                env.update(VALOR_LOGIN_PASSWORD=login["password"] if login else "",
                           VALOR_NAMESERVERS=" ".join(cfg.nameservers))
                script = windows.role_script(ref.name, role.ps_script, env)
                for attempt in range(4):                     # e.g. AD promotion: run, reboot, run again
                    res = pve.ps(vmid, script, timeout=3600)
                    if not res.ok:
                        break
                    changed |= "VALOR-ROLE-CHANGED=1" in res.out
                    if windows.REBOOT not in res.out:
                        break
                    windows_reboot(pve, vmid)
            else:
                res = pve.script(vmid, build_script(role, env), timeout=1800)
                changed = "VALOR-ROLE-CHANGED=1" in res.out
            if not res.ok:
                details = res.tail().replace(login["password"], "***") if login else res.tail()
                raise ValorError("role_failed", f"role '{ref.name}' failed on {d.host} (exit {res.exitcode})",
                                 host=d.host, step=f"role {ref.name}", details=details,
                                 hint=f"Fix roles/{ref.name}/ or the role params in the spec, then re-apply.")
            out.append({"role": ref.name, "changed": changed})
    return out


def run_baseline(pve: PVE, spec: RangeSpec, d: Desired, vmid: int, steps: Steps) -> list[dict]:
    baseline = baseline_for(pve.cfg.baselines_dir, spec.baseline, d.family)
    if baseline is None:
        return []
    with steps.step(f"baseline {baseline.id}", d.host):
        if d.family == "windows":
            res = pve.ps(vmid, windows.baseline_bundle(baseline, d.is_router, fix=True), timeout=1800)
        else:
            res = pve.script(vmid, bundle(baseline, d.is_router, fix=True), timeout=1800)
        results = parse_results(baseline, res.out)
        failed = [r["control"] for r in results if r["after"] != "pass"]
        if not res.ok or failed:
            raise ValorError("baseline_failed", f"baseline controls failed on {d.host}: {', '.join(failed) or 'script error'}",
                             host=d.host, step=f"baseline {baseline.id}", details=res.tail(),
                             hint=f"Fix the control in baselines/{baseline.id}.yaml (check and fix must agree), then re-apply.")
        return results


def converge_waves(spec: RangeSpec, hosts: list[Desired], cfg) -> list[list[Desired]]:
    """Order hosts so a host converges after the hosts its roles wait for (role.yaml `wait_for: [param]`)."""
    names = {d.host for d in hosts}
    deps: dict[str, set[str]] = {d.host: set() for d in hosts}
    for d in hosts:
        for ref in spec.host(d.host).roles:
            for pname in load_role(cfg.roles_dir, ref.name).meta.get("wait_for") or []:
                value = ref.params.get(pname)
                for target in value if isinstance(value, list) else [value]:
                    if target in names and target != d.host:
                        deps[d.host].add(target)
    waves, done = [], set()
    while len(done) < len(hosts):
        wave = [d for d in hosts if d.host not in done and deps[d.host] <= done]
        if not wave:
            raise ValorError("dependency_cycle", "hosts wait for each other: " +
                             ", ".join(f"{h} -> {sorted(v)}" for h, v in deps.items() if v - done))
        waves.append(wave)
        done |= {d.host for d in wave}
    return waves


def apply(pve: PVE, spec: RangeSpec, emit=lambda *a, **k: None) -> dict:
    steps = Steps(emit)
    t0 = time.time()
    cfg = pve.cfg
    result: dict = {"range": spec.name, "spec": spec_hash(spec)[:12], "started": now()}
    try:
        with steps.step("validate"):
            v = validate_cluster(pve, spec)
            if not v["ok"]:
                raise ValorError("validation_failed", f"{len(v['errors'])} cluster check(s) failed", details=v["errors"],
                                 hint="Fix the spec (or ask an administrator for missing templates/bridges) and re-apply.")
        login = credentials.ensure(cfg, spec.name)      # before desired_state: its version is part of the hashes
        desired = desired_state(cfg, spec, template_ids(pve))
        with steps.step("plan"):
            plan = make_plan(pve, spec, desired)
            if not plan["node"]["within_limit"]:
                raise ValorError("insufficient_resources", "the range would exceed the node's memory limit",
                                 details=plan["node"])
        result["plan_summary"] = plan["summary"]
        if not plan["changes"]:
            result.update(changed=False, seconds=round(time.time() - t0, 1), steps=steps.records)
            return result

        actions = {a["host"]: a for a in plan["actions"]}
        for a in plan["actions"]:
            if a["action"] in ("remove", "replace") and a.get("vmid"):
                with steps.step(f"{a['action']}: delete old VM {a['vmid']}", a["host"]):
                    pve.destroy(a["vmid"])

        taken: set[int] = set()
        vmids: dict[str, int] = {}
        for d in desired:
            a = actions[d.host]
            if a["action"] in ("create", "replace"):
                with steps.step("create VM", d.host):
                    vmids[d.host] = create_vm(pve, spec, d, taken)
            else:
                vmids[d.host] = a["vmid"]
                if a["action"] == "update":
                    with steps.step("resize CPU/memory/disk and reboot", d.host):
                        pve.update_config(a["vmid"], cores=d.cores, memory=d.memory, vga=d.hw.get("display", "std"),
                                          serial0="socket")
                        cur = int(str(pve.vm_config(a["vmid"]).get("scsi0", "size=0G")).split("size=")[-1].rstrip("G") or 0)
                        if d.disk > cur:
                            pve.resize(a["vmid"], "scsi0", d.disk)
                        if pve.vm_status(a["vmid"]).get("status") == "running":
                            pve.power(a["vmid"], "reboot")
                        else:
                            pve.power(a["vmid"], "start")
                elif a["action"] == "start" or pve.vm_status(a["vmid"]).get("status") != "running":
                    with steps.step("start VM", d.host):
                        pve.power(a["vmid"], "start")
        result["vmids"] = vmids

        work = [d for d in desired if actions[d.host]["action"] not in ("keep",) and d.family != "windows"]
        with steps.step("wait for guest agents"):
            for d in desired:
                if d.family != "windows":
                    pve.wait_agent(vmids[d.host], 300)
        win = [d for d in desired if d.family == "windows"]
        if win:
            with steps.step("wait for Windows setup"):
                with ThreadPoolExecutor(PARALLEL_HOSTS) as ex:
                    for f in as_completed([ex.submit(windows_wait_ready, pve, vmids[d.host]) for d in win]):
                        f.result()
        with steps.step("wait for cloud-init"):
            def ci(d):
                r = pve.exec(vmids[d.host], ["cloud-init", "status", "--wait"], timeout=900)
                if r.exitcode not in (0, 2):
                    raise ValorError("cloud_init_failed", f"cloud-init failed on {d.host}", host=d.host, details=r.tail())
            with ThreadPoolExecutor(PARALLEL_HOSTS) as ex:
                for f in as_completed([ex.submit(ci, d) for d in work]):
                    f.result()

        router = desired[0]
        rvmid = vmids[ROUTER]
        with steps.step("map router interfaces", ROUTER):
            ifmap, uplink = router_interfaces(pve, rvmid, len(spec.segments), spec)
        result["router_interfaces"] = {"uplink": uplink, **ifmap}

        hosts = [d for d in desired[1:] if actions[d.host]["action"] in ("create", "replace", "update", "converge")]
        router_conv = actions[ROUTER]["action"] in ("create", "replace", "update", "converge")
        report: dict = {}
        if hosts:
            with steps.step("router: temporary build egress", ROUTER):
                load_router_policy(pve, spec, rvmid, ifmap, uplink, build_egress=True)

            def converge(d: Desired):
                vmid = vmids[d.host]
                if d.family == "windows":
                    windows_prep(pve, spec, d, vmid, login, steps)
                else:
                    set_login(pve, d, vmid, login, steps)
                roles = run_roles(pve, spec, d, vmid, steps, login)
                base = run_baseline(pve, spec, d, vmid, steps)
                stamp(pve, spec, d, vmid, d.conv_hash)
                return d.host, {"roles": roles, "baseline": base}

            errors = []
            for wave in converge_waves(spec, hosts, cfg):      # e.g. domain controllers before their members
                with ThreadPoolExecutor(PARALLEL_HOSTS) as ex:
                    futs = [ex.submit(converge, d) for d in wave]
                    for f in as_completed(futs):
                        try:
                            h, rep = f.result()
                            report[h] = rep
                        except Exception as e:
                            errors.append(e)
                if errors:
                    break
            if errors:
                # restore the final policy before reporting, so a failed build never leaves egress open
                try:
                    load_router_policy(pve, spec, rvmid, ifmap, uplink, build_egress=False)
                except Exception:
                    pass
                raise errors[0]

        if router_conv or hosts:
            if router_conv:
                set_login(pve, router, rvmid, login, steps)
                report[ROUTER] = {"baseline": run_baseline(pve, spec, router, rvmid, steps)}
            with steps.step("router: final policy", ROUTER):
                load_router_policy(pve, spec, rvmid, ifmap, uplink, build_egress=False)
            stamp(pve, spec, router, rvmid, router.conv_hash)

        with steps.step("record spec version on all VMs"):
            for d in desired:
                if actions[d.host]["action"] in ("keep", "start", "restamp"):
                    vm = next((v for v in range_vms(pve, spec.name) if v.host == d.host), None)
                    if vm and vm.meta.get("spec") != spec_hash(spec):
                        stamp(pve, spec, d, vmids[d.host], vm.meta.get("conv"))

        result.update(changed=True, converged=report, seconds=round(time.time() - t0, 1), steps=steps.records)
        return result
    except ValorError as e:
        e.completed_steps = steps.completed()
        if e.step is None:
            failed = next((r for r in reversed(steps.records) if r["status"] == "failed"), None)
            if failed:
                e.step = failed["step"]
                e.host = e.host or failed["host"]
        raise
    except Exception as e:
        raise ValorError("internal_error", f"{e.__class__.__name__}: {e}", completed_steps=steps.completed())
