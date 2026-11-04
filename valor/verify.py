"""Verification (FR-08): policy tests from inside the guests and per-host baseline checks.

verify(pve, spec) proves that the running range matches its spec:

  - connectivity tests (spec.effective_tests) run from inside the source VM over the guest agent: allowed
    traffic must connect, everything else must be blocked, internet egress must match each segment's setting;
  - WireGuard (if configured): the router listens on the port and knows every peer;
  - the baseline's checks run again on every VM (check only, no fixes);
  - the compliance report maps those results to the spec's frameworks.

`ok` is true only if every test and every baseline control passes.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import compliance, windows
from .baseline import baseline_for, bundle, load_baseline, parse_results
from .cluster import VMState, load_catalog, range_vms
from .errors import ValorError
from .pve import PVE
from .spec import INTERNET_PROBE, ROUTER, RangeSpec, effective_tests, spec_hash

OPEN_RETRIES, OPEN_RETRY_DELAY = 2, 15


def _target(spec: RangeSpec, to: str, port: int | None, probe: tuple[str, int] = INTERNET_PROBE) -> tuple[str, int | None]:
    """Where a test connects to: "internet" is the probe address, a host name its address, anything else as is."""
    if to == "internet":
        return probe[0], port or probe[1]
    if any(host.name == to for host in spec.hosts):
        return str(spec.host(to).address), port
    return to, port


def _probe(pve: PVE, vmid: int, proto: str, ip: str, port: int | None, family: str = "debian") -> tuple[str, str]:
    """Try one connection from inside a VM: ("open" | "closed", what was seen)."""
    if family == "windows":
        result = pve.ps(vmid, windows.probe_script(proto, ip, port), timeout=60)
    else:
        if proto == "icmp":
            command = f"ping -c1 -W3 {ip} >/dev/null 2>&1; echo $?"
        else:
            command = f"timeout 5 bash -c '</dev/tcp/{ip}/{port}' >/dev/null 2>&1; echo $?"
        result = pve.exec(vmid, ["/bin/bash", "-c", command], timeout=30)
    code = (result.out.strip().splitlines() or ["?"])[-1]
    if code == "0":
        return "open", "connected" if proto == "tcp" else "reply"
    if proto == "tcp" and code == "124":                # `timeout` killed it: packets were dropped
        return "closed", "filtered (timed out)"
    if proto == "tcp" and code == "1":
        return "closed", "refused (nothing listening, or rejected)"
    return "closed", f"no reply (exit {code})"


def _wireguard_check(pve: PVE, router_vmid: int, wg) -> dict:
    """The router's wg0 listens on the spec's port and knows every peer."""
    port = pve.exec(router_vmid, ["wg", "show", "wg0", "listen-port"], timeout=30)
    peers = pve.exec(router_vmid, ["wg", "show", "wg0", "peers"], timeout=30)
    listening = port.ok and port.out.strip() == str(wg.port)
    known = len(peers.out.split()) if peers.ok else 0
    ok = listening and known == len(wg.peers)
    return {"name": f"WireGuard access: router listens on udp/{wg.port} for {len(wg.peers)} peer(s)",
            "from": ROUTER, "to": "wireguard", "proto": "udp", "port": wg.port, "expect": "open",
            "origin": "access", "target": f"wg0 udp/{wg.port}", "observed": "open" if ok else "closed",
            "detail": f"listening: {listening}; peers configured: {known}/{len(wg.peers)}", "pass": ok}


def _aligned(spec: RangeSpec) -> str:
    """What the baseline claim in the summary refers to (honest wording: aligned with, not certified)."""
    frameworks = [name for name in (spec.compliance.frameworks if spec.compliance else []) if name != "cis-l1"]
    if not frameworks:
        return "common CIS Level 1 themes"
    return f"the technical requirements VALOR covers of {', '.join(frameworks)}"


def verify(pve: PVE, spec: RangeSpec, emit=lambda *a, **k: None) -> dict:
    started = time.time()
    vms = _running_vms(pve, spec)
    wanted = [ROUTER, *(host.name for host in spec.hosts)]
    stale = [host for host in wanted if vms[host].meta.get("spec") != spec_hash(spec)]
    catalog = load_catalog(pve.cfg)
    family = {ROUTER: catalog.get(spec.router.os or "", {}).get("family", "debian"),
              **{host.name: catalog.get(host.os or "", {}).get("family", "debian") for host in spec.hosts}}

    results = _run_tests(pve, spec, vms, family, emit)
    wireguard = spec.access.wireguard if spec.access else None
    if wireguard:
        results.append(_wireguard_check(pve, vms[ROUTER].vmid, wireguard))
        emit("test", name=results[-1]["name"], passed=results[-1]["pass"])

    baseline = load_baseline(pve.cfg.baselines_dir, spec.baseline)
    baseline_rows, baselines_used = ({}, set())
    if baseline:
        baseline_rows, baselines_used = _check_baselines(pve, spec, vms, wanted, family, emit)

    try:
        report = compliance.report(pve.cfg.baselines_dir, spec, baseline_rows, results, family)
    except ValorError as e:
        report = {"error": e.message}

    tests_passed = sum(result["pass"] for result in results)
    all_rows = [row for rows in baseline_rows.values() for row in rows]
    rows_passed = sum(row["after"] == "pass" for row in all_rows)
    summary = _summary(spec, results, tests_passed, all_rows, rows_passed, baseline, baselines_used, report)
    return {"range": spec.name, "spec": spec_hash(spec)[:12],
            "ok": tests_passed == len(results) and rows_passed == len(all_rows),
            "summary": summary, "tests": results, "baseline": baseline_rows, "stale_hosts": stale,
            **({"compliance": report} if report else {}),
            "seconds": round(time.time() - started, 1)}


def _running_vms(pve: PVE, spec: RangeSpec) -> dict[str, VMState]:
    """The range's VMs by host; every VM of the spec must exist and run."""
    vms = {vm.host: vm for vm in range_vms(pve, spec.name)}
    wanted = [ROUTER, *(host.name for host in spec.hosts)]
    missing = [host for host in wanted if host not in vms]
    if missing:
        raise ValorError("range_incomplete", f"range '{spec.name}' is missing VMs: {', '.join(missing)}",
                         hint="Apply the spec first.")
    stopped = [host for host in wanted if vms[host].status != "running"]
    if stopped:
        raise ValorError("range_not_running", f"VMs not running: {', '.join(stopped)}",
                         hint="Re-apply the spec to start them.")
    return vms


def _run_tests(pve: PVE, spec: RangeSpec, vms: dict[str, VMState], family: dict[str, str], emit) -> list[dict]:
    """Every connectivity test. Tests from different VMs run in parallel; one VM's tests run one after another."""
    by_source: dict[str, list[dict]] = {}
    for test in effective_tests(spec, pve.cfg.probe):
        by_source.setdefault(test["from"], []).append(test)

    def run_from(source: str) -> list[dict]:
        out = []
        for test in by_source[source]:
            ip, port = _target(spec, test["to"], test["port"], pve.cfg.probe)
            observed, detail = _probe(pve, vms[source].vmid, test["proto"], ip, port, family[source])
            # a service may still be starting (a VM the build just started or rebooted): look again before
            # calling an open port closed. A closed result is never retried - a probe cannot connect by mistake.
            for _ in range(OPEN_RETRIES):
                if observed == test["expect"] or test["expect"] != "open":
                    break
                time.sleep(OPEN_RETRY_DELAY)
                observed, detail = _probe(pve, vms[source].vmid, test["proto"], ip, port, family[source])
            out.append({**test, "target": f"{ip}{':' + str(port) if port else ''}", "observed": observed,
                        "detail": detail, "pass": observed == test["expect"]})
            emit("test", name=test["name"], passed=observed == test["expect"])
        return out

    results: list[dict] = []
    with ThreadPoolExecutor(4) as pool:
        for source_results in pool.map(run_from, list(by_source)):     # map keeps the order of the sources
            results.extend(source_results)
    return results


def _check_baselines(pve: PVE, spec: RangeSpec, vms: dict[str, VMState], hosts: list[str], family: dict[str, str],
                     emit) -> tuple[dict[str, list[dict]], set]:
    """Run each host's baseline checks (no fixes). Returns ({host: rows}, ids of the baselines used)."""
    def check(host: str):
        baseline = baseline_for(pve.cfg.baselines_dir, spec.baseline, family[host])
        if baseline is None:
            return host, None, []
        if family[host] == "windows":
            result = pve.ps(vms[host].vmid, windows.baseline_bundle(baseline, False, fix=False), timeout=300)
        else:
            result = pve.script(vms[host].vmid, bundle(baseline, host == ROUTER, fix=False), timeout=300)
        return host, baseline.id, parse_results(baseline, result.out)

    rows_by_host: dict[str, list[dict]] = {}
    used = set()
    with ThreadPoolExecutor(4) as pool:
        for host, baseline_id, rows in pool.map(check, hosts):
            rows_by_host[host] = rows
            used.add(baseline_id)
            emit("baseline", host=host, passed=sum(row["after"] == "pass" for row in rows), total=len(rows))
    return rows_by_host, used


def _summary(spec: RangeSpec, results: list[dict], tests_passed: int, rows: list[dict], rows_passed: int,
             baseline, baselines_used: set, report: dict | None) -> dict:
    closed = [result for result in results if result["expect"] == "closed"]
    claim = None
    if baseline:
        names = ", ".join(sorted(name for name in baselines_used if name))
        claim = f"aligned with {_aligned(spec)} (baseline {names}); not a certification"
    summary = {
        "tests_passed": tests_passed, "tests_total": len(results),
        "isolation_passed": sum(result["pass"] for result in closed), "isolation_total": len(closed),
        "baseline_passed": rows_passed, "baseline_total": len(rows),
        "baseline_claim": claim,
    }
    if report and "frameworks" in report:
        summary["compliance"] = {name: {key: framework[key] for key in ("passed", "partial", "failed")}
                                 for name, framework in report["frameworks"].items()}
    return summary
