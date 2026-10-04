"""Verification (FR-08): policy tests from inside the guests and per-host baseline checks."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import windows
from .baseline import baseline_for, bundle, load_baseline, parse_results
from .cluster import load_catalog, range_vms
from .errors import ValorError
from .pve import PVE
from .spec import INTERNET_PROBE, ROUTER, RangeSpec, effective_tests, spec_hash


def _target(spec: RangeSpec, to: str, port: int | None, probe: tuple[str, int] = INTERNET_PROBE) -> tuple[str, int | None]:
    if to == "internet":
        return probe[0], port or probe[1]
    if any(h.name == to for h in spec.hosts):
        return str(spec.host(to).address), port
    return to, port


def _probe(pve: PVE, vmid: int, proto: str, ip: str, port: int | None, family: str = "debian") -> tuple[str, str]:
    if family == "windows":
        res = pve.ps(vmid, windows.probe_script(proto, ip, port), timeout=60)
    else:
        if proto == "icmp":
            cmd = f"ping -c1 -W3 {ip} >/dev/null 2>&1; echo $?"
        else:
            cmd = f"timeout 5 bash -c '</dev/tcp/{ip}/{port}' >/dev/null 2>&1; echo $?"
        res = pve.exec(vmid, ["/bin/bash", "-c", cmd], timeout=30)
    code = (res.out.strip().splitlines() or ["?"])[-1]
    if code == "0":
        return "open", "connected" if proto == "tcp" else "reply"
    if proto == "tcp" and code == "124":
        return "closed", "filtered (timed out)"
    if proto == "tcp" and code == "1":
        return "closed", "refused (nothing listening, or rejected)"
    return "closed", f"no reply (exit {code})"


def verify(pve: PVE, spec: RangeSpec, emit=lambda *a, **k: None) -> dict:
    t0 = time.time()
    vms = {vm.host: vm for vm in range_vms(pve, spec.name)}
    wanted = [ROUTER, *(h.name for h in spec.hosts)]
    missing = [h for h in wanted if h not in vms]
    if missing:
        raise ValorError("range_incomplete", f"range '{spec.name}' is missing VMs: {', '.join(missing)}",
                         hint="Apply the spec first.")
    stopped = [h for h in wanted if vms[h].status != "running"]
    if stopped:
        raise ValorError("range_not_running", f"VMs not running: {', '.join(stopped)}", hint="Re-apply the spec to start them.")
    stale = [h for h in wanted if vms[h].meta.get("spec") != spec_hash(spec)]
    catalog = load_catalog(pve.cfg)
    family = {ROUTER: catalog.get(spec.router.os or "", {}).get("family", "debian"),
              **{h.name: catalog.get(h.os or "", {}).get("family", "debian") for h in spec.hosts}}

    tests = effective_tests(spec, pve.cfg.probe)
    by_src: dict[str, list[dict]] = {}
    for t in tests:
        by_src.setdefault(t["from"], []).append(t)

    def run_src(src: str) -> list[dict]:
        out = []
        for t in by_src[src]:
            ip, port = _target(spec, t["to"], t["port"], pve.cfg.probe)
            observed, detail = _probe(pve, vms[src].vmid, t["proto"], ip, port, family[src])
            out.append({**t, "target": f"{ip}{':' + str(port) if port else ''}", "observed": observed,
                        "detail": detail, "pass": observed == t["expect"]})
            emit("test", name=t["name"], passed=observed == t["expect"])
        return out

    results: list[dict] = []
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(run_src, list(by_src)):
            results.extend(r)

    baseline = load_baseline(pve.cfg.baselines_dir, spec.baseline)
    base: dict[str, list[dict]] = {}
    used = set()

    def check(host: str):
        b = baseline_for(pve.cfg.baselines_dir, spec.baseline, family[host])
        if b is None:
            return host, None, []
        if family[host] == "windows":
            res = pve.ps(vms[host].vmid, windows.baseline_bundle(b, False, fix=False), timeout=300)
        else:
            res = pve.script(vms[host].vmid, bundle(b, host == ROUTER, fix=False), timeout=300)
        return host, b.id, parse_results(b, res.out)
    if baseline:
        with ThreadPoolExecutor(4) as ex:
            for host, bid, rows in ex.map(check, wanted):
                base[host] = rows
                used.add(bid)
                emit("baseline", host=host, passed=sum(r["after"] == "pass" for r in rows), total=len(rows))

    t_pass = sum(r["pass"] for r in results)
    b_rows = [r for rows in base.values() for r in rows]
    b_pass = sum(r["after"] == "pass" for r in b_rows)
    closed = [r for r in results if r["expect"] == "closed"]
    summary = {
        "tests_passed": t_pass, "tests_total": len(results),
        "isolation_passed": sum(r["pass"] for r in closed), "isolation_total": len(closed),
        "baseline_passed": b_pass, "baseline_total": len(b_rows),
        "baseline_claim": (f"aligned with common CIS Level 1 themes (baseline {', '.join(sorted(b for b in used if b))}); "
                           "not a certification") if baseline else None,
    }
    return {"range": spec.name, "spec": spec_hash(spec)[:12], "ok": t_pass == len(results) and b_pass == len(b_rows),
            "summary": summary, "tests": results, "baseline": base, "stale_hosts": stale,
            "seconds": round(time.time() - t0, 1)}
