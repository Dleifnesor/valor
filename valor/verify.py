# *grabs a tiny magnifying glass* OwO valor/verify.py PROVES a built range really matches its spec! ^w^
# It doesn't just trust the build. From INSIDE the VMs (over the guest agent) it tries real connections: allowed
# traffic must connect, everything else must be blocked, and internet access must match each segment's setting.
# It checks WireGuard (if any), re-runs the hardening checks on every VM (check only, no fixes), and builds the
# compliance report. `ok` is only true when EVERY test and EVERY baseline control passes *wags tail*
"""Verification (FR-08): policy tests from inside the guests and per-host baseline checks.

verify(pve, spec) proves that the running range matches its spec:

  - connectivity tests (spec.effective_tests) run from inside the source VM over the guest agent: allowed
    traffic must connect, everything else must be blocked, internet egress must match each segment's setting;
  - WireGuard (if configured): the router listens on the port and knows every peer;
  - the baseline's checks run again on every VM (check only, no fixes);
  - the compliance report maps those results to the spec's frameworks.

`ok` is true only if every test and every baseline control passes.
"""

# Lazy type hints UwU
from __future__ import annotations

# time measures and waits; ThreadPoolExecutor runs tests from several VMs at once >w<
import time
from concurrent.futures import ThreadPoolExecutor

# compliance (the report), windows (PowerShell probe/baseline scripts), the baseline helpers, cluster helpers,
# ValorError, PVE, and from spec: the default internet probe, ROUTER, RangeSpec, the test list and the spec hash OwO
from . import compliance, windows
from .baseline import baseline_for, bundle, load_baseline, parse_results
from .cluster import VMState, load_catalog, range_vms
from .errors import ValorError
from .pve import PVE
from .spec import INTERNET_PROBE, ROUTER, RangeSpec, effective_tests, spec_hash

# A test expecting "open" that finds it closed is retried 2 more times, 15 s apart (a service may still be starting) ^w^
OPEN_RETRIES, OPEN_RETRY_DELAY = 2, 15


# _target: where a test connects: "internet" = the probe address (and its port unless the test gives one), a host
# name = that host's address, anything else = used as is UwU
def _target(spec: RangeSpec, to: str, port: int | None, probe: tuple[str, int] = INTERNET_PROBE) -> tuple[str, int | None]:
    """Where a test connects to: "internet" is the probe address, a host name its address, anything else as is."""
    if to == "internet":
        return probe[0], port or probe[1]
    if any(host.name == to for host in spec.hosts):
        return str(spec.host(to).address), port
    return to, port


# _probe: try ONE connection from inside a VM. Returns ("open" or "closed", what was seen) >w<
def _probe(pve: PVE, vmid: int, proto: str, ip: str, port: int | None, family: str = "debian") -> tuple[str, str]:
    """Try one connection from inside a VM: ("open" | "closed", what was seen)."""
    # Windows VMs run the PowerShell probe OwO
    if family == "windows":
        result = pve.ps(vmid, windows.probe_script(proto, ip, port), timeout=60)
    else:
        # Linux: one ping with a 3 s wait, or bash's built-in /dev/tcp to open a TCP connection, killed by `timeout` after
        # 5 s. Either way the exit code is printed ^w^
        if proto == "icmp":
            command = f"ping -c1 -W3 {ip} >/dev/null 2>&1; echo $?"
        else:
            command = f"timeout 5 bash -c '</dev/tcp/{ip}/{port}' >/dev/null 2>&1; echo $?"
        result = pve.exec(vmid, ["/bin/bash", "-c", command], timeout=30)
    # The last line of the output is the exit code UwU
    code = (result.out.strip().splitlines() or ["?"])[-1]
    # 0 = connected / got a reply >w<
    if code == "0":
        return "open", "connected" if proto == "tcp" else "reply"
    # 124 = timed out: the packets were silently dropped (the firewall's "drop" policy) OwO
    if proto == "tcp" and code == "124":                # `timeout` killed it: packets were dropped
        return "closed", "filtered (timed out)"
    # 1 = refused: the packets got there but nothing listens (or a reject) ^w^
    if proto == "tcp" and code == "1":
        return "closed", "refused (nothing listening, or rejected)"
    return "closed", f"no reply (exit {code})"


# _wireguard_check: does the router's wg0 listen on the right port and know every peer? UwU
def _wireguard_check(pve: PVE, router_vmid: int, wg) -> dict:
    """The router's wg0 listens on the spec's port and knows every peer."""
    port = pve.exec(router_vmid, ["wg", "show", "wg0", "listen-port"], timeout=30)
    peers = pve.exec(router_vmid, ["wg", "show", "wg0", "peers"], timeout=30)
    # Listening on the expected port, and as many peers as the spec names >w<
    listening = port.ok and port.out.strip() == str(wg.port)
    known = len(peers.out.split()) if peers.ok else 0
    ok = listening and known == len(wg.peers)
    # Shaped like a connectivity test, so it shows up in the same list OwO
    return {"name": f"WireGuard access: router listens on udp/{wg.port} for {len(wg.peers)} peer(s)",
            "from": ROUTER, "to": "wireguard", "proto": "udp", "port": wg.port, "expect": "open",
            "origin": "access", "target": f"wg0 udp/{wg.port}", "observed": "open" if ok else "closed",
            "detail": f"listening: {listening}; peers configured: {known}/{len(wg.peers)}", "pass": ok}


# _aligned: the wording of the baseline claim: "common CIS Level 1 themes", or the chosen frameworks (honest
# wording: aligned with, never certified) ^w^
def _aligned(spec: RangeSpec) -> str:
    """What the baseline claim in the summary refers to (honest wording: aligned with, not certified)."""
    frameworks = [name for name in (spec.compliance.frameworks if spec.compliance else []) if name != "cis-l1"]
    if not frameworks:
        return "common CIS Level 1 themes"
    return f"the technical requirements VALOR covers of {', '.join(frameworks)}"


# verify: the whole verification UwU
def verify(pve: PVE, spec: RangeSpec, emit=lambda *a, **k: None) -> dict:
    started = time.time()
    # Every VM must exist and run; "stale" hosts are ones whose metadata says they were built from another spec
    # version (shown as a warning) >w<
    vms = _running_vms(pve, spec)
    wanted = [ROUTER, *(host.name for host in spec.hosts)]
    stale = [host for host in wanted if vms[host].meta.get("spec") != spec_hash(spec)]
    # Each VM's OS family (debian / rhel / windows), from the catalog OwO
    catalog = load_catalog(pve.cfg)
    family = {ROUTER: catalog.get(spec.router.os or "", {}).get("family", "debian"),
              **{host.name: catalog.get(host.os or "", {}).get("family", "debian") for host in spec.hosts}}

    # 1. Connectivity tests, then the WireGuard check ^w^
    results = _run_tests(pve, spec, vms, family, emit)
    wireguard = spec.access.wireguard if spec.access else None
    if wireguard:
        results.append(_wireguard_check(pve, vms[ROUTER].vmid, wireguard))
        emit("test", name=results[-1]["name"], passed=results[-1]["pass"])

    # 2. The baseline checks on every VM (when the range has a baseline) UwU
    baseline = load_baseline(pve.cfg.baselines_dir, spec.baseline)
    baseline_rows, baselines_used = ({}, set())
    if baseline:
        baseline_rows, baselines_used = _check_baselines(pve, spec, vms, wanted, family, emit)

    # 3. The compliance report (an error message instead, if the mapping is missing) >w<
    try:
        report = compliance.report(pve.cfg.baselines_dir, spec, baseline_rows, results, family)
    except ValorError as e:
        report = {"error": e.message}

    # Count what passed, build the summary, and return everything (ok = all tests AND all controls passed) OwO
    tests_passed = sum(result["pass"] for result in results)
    all_rows = [row for rows in baseline_rows.values() for row in rows]
    rows_passed = sum(row["after"] == "pass" for row in all_rows)
    summary = _summary(spec, results, tests_passed, all_rows, rows_passed, baseline, baselines_used, report)
    return {"range": spec.name, "spec": spec_hash(spec)[:12],
            "ok": tests_passed == len(results) and rows_passed == len(all_rows),
            "summary": summary, "tests": results, "baseline": baseline_rows, "stale_hosts": stale,
            **({"compliance": report} if report else {}),
            "seconds": round(time.time() - started, 1)}


# _running_vms: the range's VMs by host name; every VM of the spec must exist AND be running, or there's nothing to
# verify ^w^
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


# _run_tests: run every connectivity test. Tests from DIFFERENT VMs run in parallel; one VM's tests run one after
# another UwU
def _run_tests(pve: PVE, spec: RangeSpec, vms: dict[str, VMState], family: dict[str, str], emit) -> list[dict]:
    """Every connectivity test. Tests from different VMs run in parallel; one VM's tests run one after another."""
    # Group the tests by their source VM >w<
    by_source: dict[str, list[dict]] = {}
    for test in effective_tests(spec, pve.cfg.probe):
        by_source.setdefault(test["from"], []).append(test)

    # run_from(): every test of one source VM OwO
    def run_from(source: str) -> list[dict]:
        out = []
        for test in by_source[source]:
            ip, port = _target(spec, test["to"], test["port"], pve.cfg.probe)
            # Probe once, and retry (only expected-open tests that came back closed) ^w^
            observed, detail = _probe(pve, vms[source].vmid, test["proto"], ip, port, family[source])
            # a service may still be starting (a VM the build just started or rebooted): look again before
            # calling an open port closed. A closed result is never retried - a probe cannot connect by mistake.
            for _ in range(OPEN_RETRIES):
                if observed == test["expect"] or test["expect"] != "open":
                    break
                time.sleep(OPEN_RETRY_DELAY)
                observed, detail = _probe(pve, vms[source].vmid, test["proto"], ip, port, family[source])
            # The test's result: where it connected, what was seen, and pass = observed matches expected UwU
            out.append({**test, "target": f"{ip}{':' + str(port) if port else ''}", "observed": observed,
                        "detail": detail, "pass": observed == test["expect"]})
            emit("test", name=test["name"], passed=observed == test["expect"])
        return out

    # 4 sources at a time; pool.map keeps the results in the same order as the sources >w<
    results: list[dict] = []
    with ThreadPoolExecutor(4) as pool:
        for source_results in pool.map(run_from, list(by_source)):     # map keeps the order of the sources
            results.extend(source_results)
    return results


# _check_baselines: run each host's baseline checks (check only). Returns ({host: rows}, which baselines were used) OwO
def _check_baselines(pve: PVE, spec: RangeSpec, vms: dict[str, VMState], hosts: list[str], family: dict[str, str],
                     emit) -> tuple[dict[str, list[dict]], set]:
    """Run each host's baseline checks (no fixes). Returns ({host: rows}, ids of the baselines used)."""
    # check(): one host: pick its baseline (Windows hosts get the Windows profile), run the right script, parse it ^w^
    def check(host: str):
        baseline = baseline_for(pve.cfg.baselines_dir, spec.baseline, family[host])
        if baseline is None:
            return host, None, []
        if family[host] == "windows":
            result = pve.ps(vms[host].vmid, windows.baseline_bundle(baseline, False, fix=False), timeout=300)
        else:
            result = pve.script(vms[host].vmid, bundle(baseline, host == ROUTER, fix=False), timeout=300)
        return host, baseline.id, parse_results(baseline, result.out)

    # 4 hosts at a time; report each host's pass count as it finishes UwU
    rows_by_host: dict[str, list[dict]] = {}
    used = set()
    with ThreadPoolExecutor(4) as pool:
        for host, baseline_id, rows in pool.map(check, hosts):
            rows_by_host[host] = rows
            used.add(baseline_id)
            emit("baseline", host=host, passed=sum(row["after"] == "pass" for row in rows), total=len(rows))
    return rows_by_host, used


# _summary: the counts the UI shows: tests, isolation tests (those expecting "closed"), baseline controls, the
# baseline claim sentence, and per-framework compliance counts >w< *stamps it VERIFIED*
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
