"""valor - command-line interface to the VALOR engine (headless, CI/CD friendly; FR-12).

  valor info                         cluster state relevant to authoring a spec
  valor validate SPEC                schema + cluster checks
  valor plan SPEC [--show-policy]    what would be created / updated / removed
  valor apply SPEC [--verify]        build or converge the range
  valor verify SPEC                  connectivity tests + baseline checks
  valor destroy RANGE --yes          remove the range's VMs (only those)
  valor journal SPEC                 regenerate journals/<range>.md
  valor guest-run RANGE HOST -- CMD  diagnostic command inside a range VM
  valor job status ID | job list

Exit codes: 0 ok, 1 spec/validation invalid, 2 build/destroy failed, 3 verification failed,
4 engine busy, 5 other error. Add --json for machine-readable output, --background to return a job id.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import jobs, journal
from .cluster import cluster_info, range_vms
from .config import load_config
from .errors import LockBusy, SpecError, ValorError
from .netpolicy import render
from .ops import execute, start
from .plan import make_plan
from .pve import PVE
from .spec import effective_tests, load_spec, spec_hash
from .validate import validate_cluster

EXIT = {"ok": 0, "invalid": 1, "failed": 2, "verify": 3, "busy": 4, "other": 5}


def out(data, as_json: bool, human=None) -> None:
    if as_json or human is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        human(data)


def echo_event(rec: dict) -> None:
    ev = rec["event"]
    host = f" [{rec['host']}]" if rec.get("host") else ""
    if ev == "step_start":
        print(f"{rec['t']}  ...  {rec['step']}{host}", flush=True)
    elif ev == "step_done":
        print(f"{rec['t']}  ok   {rec['step']}{host} ({rec.get('seconds', '?')} s)", flush=True)
    elif ev == "step_failed":
        print(f"{rec['t']}  FAIL {rec['step']}{host}: {rec.get('error')}", flush=True)
    elif ev == "test":
        print(f"{rec['t']}  {'pass' if rec['passed'] else 'FAIL'} {rec['name']}", flush=True)
    elif ev == "baseline":
        print(f"{rec['t']}  baseline {rec['host']}: {rec['passed']}/{rec['total']} controls pass", flush=True)


def print_plan(p: dict) -> None:
    print(f"Plan for range '{p['range']}' (spec {p['spec']}): " +
          ", ".join(f"{v} {k}" for k, v in sorted(p["summary"].items())))
    for a in p["actions"]:
        svc = f" services={','.join(a['services'])}" if a.get("services") else ""
        where = f" {a.get('segment') or 'all'} {a.get('address') or ''}" if a["action"] != "remove" else ""
        print(f"  {a['action']:9} {a['host']:10}{where} {a.get('os', '')} "
              f"{a.get('cores', '')}c/{a.get('memory_mib', '')}MiB{svc}  {'; '.join(a['reasons'])}")
    t, n = p["totals"], p["node"]
    print(f"Totals: {t['vms']} VMs, {t['cores']} vCPU, {t['memory_mib']} MiB RAM, {t['disk_gib']} GiB disk. "
          f"Node {n['name']}: {n['memory_free_mib']} MiB free; within limit: {n['within_limit']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="valor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    for name in ("validate", "plan", "apply", "verify", "journal"):
        p = sub.add_parser(name)
        p.add_argument("spec")
        if name == "plan":
            p.add_argument("--show-policy", action="store_true")
        if name == "apply":
            p.add_argument("--verify", action="store_true")
        if name in ("apply", "verify"):
            p.add_argument("--background", action="store_true")
    d = sub.add_parser("destroy")
    d.add_argument("range")
    d.add_argument("--yes", action="store_true")
    d.add_argument("--background", action="store_true")
    g = sub.add_parser("guest-run")
    g.add_argument("range")
    g.add_argument("host")
    g.add_argument("--timeout", type=int, default=60)
    g.add_argument("command", nargs=argparse.REMAINDER)
    j = sub.add_parser("job")
    js = j.add_subparsers(dest="jcmd", required=True)
    s = js.add_parser("status")
    s.add_argument("id")
    js.add_parser("list")
    r = sub.add_parser("_run-job")
    r.add_argument("id")
    args = ap.parse_args(argv)
    cfg = load_config()
    try:
        return dispatch(cfg, args)
    except SpecError as e:
        out(e.to_dict(), True)
        return EXIT["invalid"]
    except LockBusy as e:
        out(e.to_dict(), True)
        return EXIT["busy"]
    except ValorError as e:
        out(e.to_dict(), True)
        return EXIT["other"]


def dispatch(cfg, args) -> int:
    J = args.json
    if args.cmd == "_run-job":
        res = execute(cfg, args.id)
        return 0 if res.get("ok") else 2
    if args.cmd == "info":
        out(cluster_info(PVE(cfg)), True)
        return 0
    if args.cmd == "job":
        if args.jcmd == "list":
            out(jobs.list_jobs(cfg), True)
        else:
            out(jobs.status(cfg, args.id), True)
        return 0
    if args.cmd == "destroy":
        if not args.yes:
            print("refusing to destroy without --yes", file=sys.stderr)
            return EXIT["invalid"]
        res = start(cfg, "destroy", {"range": args.range}, "cli", args.background, None if J else echo_event)
        out(res, J, lambda r: print(json.dumps(r, indent=2, default=str)))
        return 0 if res.get("ok", True) else EXIT["failed"]
    if args.cmd == "guest-run":
        cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
        pve = PVE(cfg)
        vm = next((v for v in range_vms(pve, args.range) if v.host == args.host), None)
        if not vm:
            raise ValorError("vm_unknown", f"no host '{args.host}' in range '{args.range}'")
        res = pve.exec(vm.vmid, ["/bin/bash", "-c", " ".join(cmd)], timeout=args.timeout)
        out({"exitcode": res.exitcode, "stdout": res.out[-20000:], "stderr": res.err[-5000:],
             "timed_out": res.timed_out}, True)
        return 0 if res.ok else EXIT["failed"]

    spec, path = load_spec(args.spec, cfg.ranges_dir, cfg.default_os)
    if args.cmd == "validate":
        res = validate_cluster(PVE(cfg), spec)
        res.update(range=spec.name, spec=spec_hash(spec)[:12], tests=len(effective_tests(spec, cfg.probe)))
        out(res, True)
        return 0 if res["ok"] else EXIT["invalid"]
    if args.cmd == "plan":
        pve = PVE(cfg)
        v = validate_cluster(pve, spec)
        if not v["ok"]:
            out(v, True)
            return EXIT["invalid"]
        p = make_plan(pve, spec)
        p["warnings"] = v["warnings"]
        p["tests"] = [t["name"] for t in effective_tests(spec, cfg.probe)]
        if args.show_policy:
            ifmap = {s.name: f"eth{i + 1}" for i, s in enumerate(spec.segments)}
            p["policy_preview"] = render(spec, ifmap, "eth0", build_egress=False, spec_id=spec_hash(spec)[:12],
                                        reserved=cfg.reserved_networks)
        out(p, J, print_plan)
        return 0
    if args.cmd == "journal":
        print(journal.write(cfg, PVE(cfg), spec, path))
        return 0
    if args.cmd in ("apply", "verify"):
        target = {"spec": str(path), **({"verify": args.verify} if args.cmd == "apply" else {})}
        res = start(cfg, args.cmd, target, "cli", args.background, None if J else echo_event)
        if args.background:
            out(res, True)
            return 0
        summary = {k: res.get(k) for k in ("ok", "range", "spec", "changed", "seconds", "summary", "verify",
                                            "plan_summary", "journal", "job", "error", "message", "step", "host",
                                            "details", "hint", "completed_steps") if res.get(k) is not None}
        out(res if J else summary, True)
        if res.get("ok"):
            return 0
        if res.get("error") == "engine_busy":
            return EXIT["busy"]
        if res.get("error") in ("spec_invalid", "validation_failed"):
            return EXIT["invalid"]
        if args.cmd == "verify" or ("verify" in res and not res.get("error")):
            return EXIT["verify"]
        return EXIT["failed"]
    return EXIT["other"]


if __name__ == "__main__":
    sys.exit(main())
