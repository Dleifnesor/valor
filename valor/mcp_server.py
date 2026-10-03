"""VALOR MCP server: exposes the engine to Claude Code as tools.

Runs as the 'valor' user (it alone can read the API token):
    sudo -u valor /opt/valor/bin/valor-mcp
Long operations (apply / verify / destroy) start background jobs and return a job id at once;
poll job_status. Every tool returns structured errors instead of failing.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, jobs
from .cluster import cluster_info as _cluster_info
from .cluster import range_vms
from .config import load_config
from .errors import ValorError
from .netpolicy import render
from .ops import start
from .plan import make_plan
from .pve import PVE
from .spec import effective_tests, load_spec, spec_hash
from .validate import validate_cluster

INSTRUCTIONS = """VALOR turns a plain-language description of a test environment into a segmented, hardened,
verified set of VMs on Proxmox VE. You never change infrastructure directly: you write a declarative range
spec (YAML) in /srv/valor/ranges/<name>.yaml and the VALOR engine does the rest.
Workflow: cluster_info -> write the spec -> range_validate (fix until ok) -> range_plan (show the user) ->
range_apply after the user approves -> poll job_status -> range_verify -> report results. On a build error,
read the structured error, fix the spec or role script, and re-apply (at most 3 attempts, then report).
Follow the range-build skill for the full procedure."""

server = MCPServer("valor", instructions=INSTRUCTIONS, version=__version__)
cfg = load_config()

RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
BUILD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
DESTROY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def _fail(e: Exception) -> dict:
    if isinstance(e, ValorError):
        return {"ok": False, **e.to_dict()}
    return {"ok": False, "error": "internal_error", "message": f"{e.__class__.__name__}: {e}"}


@server.tool(description="Cluster state for authoring a spec: free memory, storage, templates (OS catalog), "
                         "segment bridge, VLANs already in use, existing ranges, available roles and baselines.",
             annotations=RO)
def cluster_info() -> dict:
    try:
        return {"ok": True, **_cluster_info(PVE(cfg))}
    except Exception as e:
        return _fail(e)


@server.tool(description="Validate a range spec (schema + live cluster checks) without changing anything. "
                         "`spec` is a file name in /srv/valor/ranges, e.g. 'web2tier.yaml'.",
             annotations=RO)
def range_validate(spec: str) -> dict:
    try:
        s, _ = load_spec(spec, cfg.ranges_dir, cfg.default_os)
        res = validate_cluster(PVE(cfg), s)
        return {**res, "range": s.name, "spec_version": spec_hash(s)[:12],
                "tests": [t["name"] for t in effective_tests(s)]}
    except Exception as e:
        return _fail(e)


@server.tool(description="Dry run: which VMs would be created, updated, replaced, kept or removed, with addresses, "
                         "services and CPU/RAM/disk totals compared with free memory. Show this to the user before "
                         "range_apply. Set show_policy to include the router's nftables ruleset.",
             annotations=RO)
def range_plan(spec: str, show_policy: bool = False) -> dict:
    try:
        s, _ = load_spec(spec, cfg.ranges_dir, cfg.default_os)
        pve = PVE(cfg)
        v = validate_cluster(pve, s)
        if not v["ok"]:
            return {"ok": False, "error": "validation_failed", **v}
        p = make_plan(pve, s)
        p["warnings"] = v["warnings"]
        p["tests"] = [t["name"] for t in effective_tests(s)]
        if show_policy:
            ifmap = {seg.name: f"eth{i + 1}" for i, seg in enumerate(s.segments)}
            p["policy_preview"] = render(s, ifmap, "eth0", build_egress=False, spec_id=spec_hash(s)[:12])
        return {"ok": True, **p}
    except Exception as e:
        return _fail(e)


@server.tool(description="Build or converge the range described by the spec (only after the user approved the plan). "
                         "Runs as a background job and returns its id; poll job_status. With verify=true the job "
                         "also runs verification at the end. Re-applying an unchanged spec makes no changes.",
             annotations=BUILD)
def range_apply(spec: str, verify: bool = True) -> dict:
    try:
        s, path = load_spec(spec, cfg.ranges_dir, cfg.default_os)
        return {"ok": True, "range": s.name, **start(cfg, "apply", {"spec": str(path), "verify": verify},
                                                      "mcp", background=True)}
    except Exception as e:
        return _fail(e)


@server.tool(description="Verify a built range: connectivity tests from inside the guests (prove the policy) and "
                         "baseline checks per host. Background job; poll job_status.",
             annotations=RO)
def range_verify(spec: str) -> dict:
    try:
        s, path = load_spec(spec, cfg.ranges_dir, cfg.default_os)
        return {"ok": True, "range": s.name, **start(cfg, "verify", {"spec": str(path)}, "mcp", background=True)}
    except Exception as e:
        return _fail(e)


@server.tool(description="Destroy a range: deletes only the VMs in the VALOR pool tagged with this range name "
                         "(never anything else). Background job; poll job_status. Needs the user's approval.",
             annotations=DESTROY)
def range_destroy(range: str) -> dict:
    try:
        if not range.replace("-", "").isalnum() or not range[:1].isalpha():
            raise ValorError("range_invalid", "invalid range name")
        vms = range_vms(PVE(cfg), range, with_config=False)
        if not vms:
            return {"ok": True, "range": range, "message": "no VMs found for this range; nothing to destroy"}
        return {"ok": True, "range": range, "vms": [v.name for v in vms],
                **start(cfg, "destroy", {"range": range}, "mcp", background=True)}
    except Exception as e:
        return _fail(e)


@server.tool(description="Status of a background job: state (queued/running/succeeded/failed), current step, recent "
                         "events and, when finished, the result. A failed build returns a structured error with the "
                         "failing step, details, a hint and the steps already completed.",
             annotations=RO)
def job_status(job: str) -> dict:
    try:
        st = jobs.status(cfg, job)
        if st.get("state") in ("queued", "running"):
            st["next"] = "poll again in ~20-30 s"
        return {"ok": True, **st}
    except Exception as e:
        return _fail(e)


@server.tool(description="Run a diagnostic shell command inside one VM of a range (via the QEMU guest agent; "
                         "no network access needed). Use for investigating failures, not for configuring hosts - "
                         "configuration belongs in roles and the spec. Needs the user's approval.",
             annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def guest_run(range: str, host: str, command: str, timeout: int = 60) -> dict:
    try:
        pve = PVE(cfg)
        vm = next((v for v in range_vms(pve, range) if v.host == host), None)
        if not vm:
            raise ValorError("vm_unknown", f"no host '{host}' in range '{range}'")
        res = pve.exec(vm.vmid, ["/bin/bash", "-c", command], timeout=max(5, min(timeout, 600)))
        return {"ok": res.ok, "exitcode": res.exitcode, "stdout": res.out[-12000:], "stderr": res.err[-4000:],
                "timed_out": res.timed_out}
    except Exception as e:
        return _fail(e)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
