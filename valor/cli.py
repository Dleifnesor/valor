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
from .ops import start
from .plan import make_plan
from .pve import PVE
from .spec import effective_tests, load_spec, spec_hash
from .validate import validate_cluster

EXIT = {"ok": 0, "invalid": 1, "failed": 2, "verify": 3, "busy": 4, "other": 5}

# The fields of a job result the CLI prints without --json.
SUMMARY_FIELDS = ("ok", "range", "spec", "changed", "seconds", "summary", "verify", "plan_summary", "journal", "job",
                  "error", "message", "step", "host", "details", "hint", "completed_steps")


def out(data, as_json: bool, human=None) -> None:
    """Print data as JSON, or with the human-readable printer when there is one and --json was not given."""
    if as_json or human is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        human(data)


def echo_event(record: dict) -> None:
    """Progress events of a job running in this terminal, one line each."""
    event = record["event"]
    host = f" [{record['host']}]" if record.get("host") else ""
    if event == "step_start":
        print(f"{record['t']}  ...  {record['step']}{host}", flush=True)
    elif event == "step_done":
        print(f"{record['t']}  ok   {record['step']}{host} ({record.get('seconds', '?')} s)", flush=True)
    elif event == "step_failed":
        print(f"{record['t']}  FAIL {record['step']}{host}: {record.get('error')}", flush=True)
    elif event == "test":
        print(f"{record['t']}  {'pass' if record['passed'] else 'FAIL'} {record['name']}", flush=True)
    elif event == "baseline":
        print(f"{record['t']}  baseline {record['host']}: {record['passed']}/{record['total']} controls pass",
              flush=True)


def print_plan(plan: dict) -> None:
    print(f"Plan for range '{plan['range']}' (spec {plan['spec']}): " +
          ", ".join(f"{count} {action}" for action, count in sorted(plan["summary"].items())))
    for action in plan["actions"]:
        services = f" services={','.join(action['services'])}" if action.get("services") else ""
        where = f" {action.get('segment') or 'all'} {action.get('address') or ''}" if action["action"] != "remove" else ""
        print(f"  {action['action']:9} {action['host']:10}{where} {action.get('os', '')} "
              f"{action.get('cores', '')}c/{action.get('memory_mib', '')}MiB{services}  {'; '.join(action['reasons'])}")
    totals, node = plan["totals"], plan["node"]
    print(f"Totals: {totals['vms']} VMs, {totals['cores']} vCPU, {totals['memory_mib']} MiB RAM, "
          f"{totals['disk_gib']} GiB disk. "
          f"Node {node['name']}: {node['memory_free_mib']} MiB free; within limit: {node['within_limit']}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="valor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("info")
    for name in ("validate", "plan", "apply", "verify", "journal"):
        command = commands.add_parser(name)
        command.add_argument("spec")
        if name == "plan":
            command.add_argument("--show-policy", action="store_true")
        if name == "apply":
            command.add_argument("--verify", action="store_true")
        if name in ("apply", "verify"):
            command.add_argument("--background", action="store_true")
    destroy = commands.add_parser("destroy")
    destroy.add_argument("range")
    destroy.add_argument("--yes", action="store_true")
    destroy.add_argument("--background", action="store_true")
    guest_run = commands.add_parser("guest-run")
    guest_run.add_argument("range")
    guest_run.add_argument("host")
    guest_run.add_argument("--timeout", type=int, default=60)
    guest_run.add_argument("command", nargs=argparse.REMAINDER)
    job = commands.add_parser("job")
    job_commands = job.add_subparsers(dest="jcmd", required=True)
    status = job_commands.add_parser("status")
    status.add_argument("id")
    job_commands.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
    """Run one command; returns the exit code."""
    without_spec = {"info": _info, "job": _job, "destroy": _destroy, "guest-run": _guest_run}
    if args.cmd in without_spec:
        return without_spec[args.cmd](cfg, args)
    spec, path = load_spec(args.spec, cfg.ranges_dir, cfg.default_os)
    with_spec = {"validate": _validate, "plan": _plan, "journal": _journal, "apply": _run_job, "verify": _run_job}
    if args.cmd in with_spec:
        return with_spec[args.cmd](cfg, args, spec, path)
    return EXIT["other"]


# ---- Commands without a spec ----

def _info(cfg, args) -> int:
    out(cluster_info(PVE(cfg)), True)
    return 0


def _job(cfg, args) -> int:
    if args.jcmd == "list":
        out(jobs.list_jobs(cfg), True)
    else:
        out(jobs.status(cfg, args.id), True)
    return 0


def _destroy(cfg, args) -> int:
    if not args.yes:
        print("refusing to destroy without --yes", file=sys.stderr)
        return EXIT["invalid"]
    result = start(cfg, "destroy", {"range": args.range}, "cli", args.background, None if args.json else echo_event)
    out(result, args.json, lambda r: print(json.dumps(r, indent=2, default=str)))
    return 0 if result.get("ok", True) else EXIT["failed"]


def _guest_run(cfg, args) -> int:
    """A diagnostic command inside a range VM, as root over the guest agent."""
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    pve = PVE(cfg)
    vm = next((vm for vm in range_vms(pve, args.range) if vm.host == args.host), None)
    if not vm:
        raise ValorError("vm_unknown", f"no host '{args.host}' in range '{args.range}'")
    result = pve.script(vm.vmid, " ".join(command), timeout=args.timeout)      # on stdin: any text works
    out({"exitcode": result.exitcode, "stdout": result.out[-20000:], "stderr": result.err[-5000:],
         "timed_out": result.timed_out}, True)
    return 0 if result.ok else EXIT["failed"]


# ---- Commands on a spec ----

def _validate(cfg, args, spec, path) -> int:
    result = validate_cluster(PVE(cfg), spec)
    result.update(range=spec.name, spec=spec_hash(spec)[:12], tests=len(effective_tests(spec, cfg.probe)))
    out(result, True)
    return 0 if result["ok"] else EXIT["invalid"]


def _plan(cfg, args, spec, path) -> int:
    pve = PVE(cfg)
    checks = validate_cluster(pve, spec)
    if not checks["ok"]:
        out(checks, True)
        return EXIT["invalid"]
    plan = make_plan(pve, spec)
    plan["warnings"] = checks["warnings"]
    plan["tests"] = [test["name"] for test in effective_tests(spec, cfg.probe)]
    if args.show_policy:
        ifmap = {segment.name: f"eth{i + 1}" for i, segment in enumerate(spec.segments)}
        plan["policy_preview"] = render(spec, ifmap, "eth0", build_egress=False, spec_id=spec_hash(spec)[:12],
                                        reserved=cfg.reserved_networks)
    out(plan, args.json, print_plan)
    return 0


def _journal(cfg, args, spec, path) -> int:
    print(journal.write(cfg, PVE(cfg), spec, path))
    return 0


def _run_job(cfg, args, spec, path) -> int:
    """apply / verify, here (progress in the terminal) or in the background (--background: prints the job id)."""
    target = {"spec": str(path), **({"verify": args.verify} if args.cmd == "apply" else {})}
    result = start(cfg, args.cmd, target, "cli", args.background, None if args.json else echo_event)
    if args.background:
        out(result, True)
        return 0
    summary = {key: result.get(key) for key in SUMMARY_FIELDS if result.get(key) is not None}
    out(result if args.json else summary, True)
    return _exit_code(args.cmd, result)


def _exit_code(command: str, result: dict) -> int:
    if result.get("ok"):
        return 0
    if result.get("error") == "engine_busy":
        return EXIT["busy"]
    if result.get("error") in ("spec_invalid", "validation_failed"):
        return EXIT["invalid"]
    # The build itself worked but the range failed verification.
    if command == "verify" or ("verify" in result and not result.get("error")):
        return EXIT["verify"]
    return EXIT["failed"]


if __name__ == "__main__":
    sys.exit(main())
