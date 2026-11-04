"""Build journal (FR-13): one wiki-style Markdown page per range in journals/<range>.md.

The journal is regenerated after every job on the range, from the spec, the VMs on the cluster and the range's
state records (last apply / verify, history). It documents the range for people: overview, topology diagram
(Mermaid), hosts, networks, the traffic policy, the latest verification and the build history. Notes that the
operator or the AI keep in journals/<range>.notes.md are appended at the end, so they survive regeneration.
"""

from __future__ import annotations

import time
from pathlib import Path

from .cluster import range_vms
from .pve import PVE
from .spec import ROUTER, RangeSpec, effective_tests, spec_hash
from . import state


def _mermaid(spec: RangeSpec) -> list[str]:
    """The topology as a Mermaid flowchart: internet - router - one box per segment with its hosts, plus the
    policy rules as dotted arrows."""
    out = ["```mermaid", "flowchart LR", '  inet(("Internet"))', '  rtr["rtr<br/>router · NAT · nftables"]',
           '  inet --- |"uplink (NAT, private nets blocked)"| rtr']
    for segment in spec.segments:
        internet = " · internet" if segment.internet else ""
        out.append(f'  subgraph {segment.name}["{segment.name} · VLAN {segment.vlan} · {segment.cidr}{internet}"]')
        for host in spec.hosts_in(segment.name):
            services = ", ".join(ref.name for ref in host.roles)
            out.append(f'    {host.name}["{host.name}<br/>{host.address}{"<br/>" + services if services else ""}"]')
        out.append("  end")
        out.append(f"  rtr --- |{segment.gateway}| {segment.name}")
    for rule in spec.policy:
        label = f"{rule.proto} {','.join(map(str, rule.ports))}" if rule.ports else rule.proto
        out.append(f'  {rule.from_} -. "{label}" .-> {rule.to}')
    out.append("```")
    return out


def write(cfg, pve: PVE, spec: RangeSpec, spec_path: Path | None = None) -> Path:
    """Regenerate the range's journal page and return its path."""
    vms = {vm.host: vm for vm in range_vms(pve, spec.name)}
    last_apply = state.load(cfg, spec.name, "apply") or {}
    last_verify = state.load(cfg, spec.name, "verify") or {}
    lines = [
        *_overview(spec, spec_path, last_apply, last_verify),
        "## Topology",
        "",
        *_mermaid(spec),
        "",
        *_hosts(spec, vms),
        *_network(spec),
        *_policy(spec),
        *_verification(cfg, spec, last_verify),
        *_history(state.history(cfg, spec.name)),
        "<!-- Agent notes: add a short 'Fix:' line under an issue when you resolve it; keep this marker. -->",
    ]
    notes_path = cfg.journals_dir / f"{spec.name}.notes.md"
    if notes_path.exists():
        lines += ["", "### Notes from the operator / agent", "", notes_path.read_text().strip()]
    out = cfg.journals_dir / f"{spec.name}.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def _overview(spec: RangeSpec, spec_path: Path | None, last_apply: dict, last_verify: dict) -> list[str]:
    spec_file = spec_path.name if spec_path else spec.name + ".yaml"
    apply_result = "OK" if last_apply.get("ok") else "FAILED" if last_apply else "-"
    apply_seconds = f" · {last_apply.get('seconds')} s" if last_apply.get("seconds") is not None else ""
    summary = last_verify.get("summary", {})
    verified = "not run"
    if last_verify:
        verified = (f"{last_verify.get('finished', last_verify.get('started', '-'))} · "
                    f"{'PASS' if last_verify.get('ok') else 'FAIL'} · "
                    f"tests {summary.get('tests_passed')}/{summary.get('tests_total')} · "
                    f"baseline {summary.get('baseline_passed')}/{summary.get('baseline_total')}")
    return [
        f"# Range `{spec.name}`",
        "",
        f"> {spec.description}" if spec.description else "> (no description)",
        "",
        "| | |",
        "|---|---|",
        f"| Spec | `{spec_file}` · version `{spec_hash(spec)[:12]}` |",
        f"| Last apply | {last_apply.get('started', '-')} · {apply_result}{apply_seconds} |",
        f"| Last verification | {verified} |",
        f"| Baseline | `{spec.baseline}` (results are *aligned with* the baseline's themes, not a certification) |",
        f"| Generated | {time.strftime('%Y-%m-%d %H:%M %Z')} by the VALOR engine |",
        "",
    ]


def _hosts(spec: RangeSpec, vms: dict) -> list[str]:
    router, router_vm = spec.router, vms.get(ROUTER)
    gateways = ", ".join(str(segment.gateway) for segment in spec.segments)
    lines = [
        "## Hosts",
        "",
        "| Host | Segment | Address | OS | vCPU / RAM / disk | Services | VMID | State |",
        "|---|---|---|---|---|---|---|---|",
        f"| rtr (router) | all | uplink DHCP; {gateways} | {router.os} | "
        f"{router.cores} / {router.memory} MiB / {router.disk} GiB | routing, NAT, policy | "
        f"{router_vm.vmid if router_vm else '-'} | {router_vm.status if router_vm else 'absent'} |",
    ]
    for host in spec.hosts:
        vm = vms.get(host.name)
        services = ", ".join(ref.name for ref in host.roles) or "-"
        lines.append(f"| {host.name} | {host.segment} | {host.address} | {host.os} | "
                     f"{host.cores} / {host.memory} MiB / {host.disk} GiB | {services} | "
                     f"{vm.vmid if vm else '-'} | {vm.status if vm else 'absent'} |")
    return lines + [""]


def _network(spec: RangeSpec) -> list[str]:
    lines = [
        "## Network",
        "",
        "| Segment | VLAN | Network | Gateway | Internet egress |",
        "|---|---|---|---|---|",
    ]
    for segment in spec.segments:
        egress = "yes (public addresses only)" if segment.internet else "no"
        lines.append(f"| {segment.name} | {segment.vlan} | {segment.cidr} | {segment.gateway} | {egress} |")
    return lines + [""]


def _policy(spec: RangeSpec) -> list[str]:
    lines = [
        "## Traffic policy",
        "",
        "Everything between segments is **denied** unless listed here. Internet egress never reaches private "
        "addresses (home LAN, cluster network).",
        "",
        "| From | To | Protocol | Ports | Note |",
        "|---|---|---|---|---|",
    ]
    for rule in spec.policy:
        lines.append(f"| {rule.from_} | {rule.to} | {rule.proto} | {', '.join(map(str, rule.ports)) or '-'} | "
                     f"{rule.description or ''} |")
    if not spec.policy:
        lines.append("| - | - | - | - | no inter-segment traffic allowed |")
    return lines + [""]


def _verification(cfg, spec: RangeSpec, last_verify: dict) -> list[str]:
    lines = ["## Verification", ""]
    if not last_verify:
        lines += ["Not verified yet. Planned tests:", "", "| Test | Expect |", "|---|---|"]
        lines += [f"| {test['name']} | {test['expect']} |" for test in effective_tests(spec, cfg.probe)]
        return lines + [""]

    summary = last_verify.get("summary", {})
    lines += [
        f"**{'PASS' if last_verify.get('ok') else 'FAIL'}** · "
        f"tests {summary.get('tests_passed')}/{summary.get('tests_total')} · "
        f"isolation {summary.get('isolation_passed')}/{summary.get('isolation_total')} · "
        f"baseline controls {summary.get('baseline_passed')}/{summary.get('baseline_total')} · "
        f"{last_verify.get('seconds')} s",
        "",
        "| Test | From | Target | Expect | Observed | Result |",
        "|---|---|---|---|---|---|",
    ]
    for test in last_verify.get("tests", []):
        lines.append(f"| {test['name']} | {test['from']} | {test['target']} | {test['expect']} | "
                     f"{test['observed']} ({test['detail']}) | {'✅' if test['pass'] else '❌'} |")
    lines += _baseline_matrix(last_verify.get("baseline", {}))
    return lines + [""]


def _baseline_matrix(rows_by_host: dict[str, list[dict]]) -> list[str]:
    """One row per control, one column per host: ✅ / ❌, or – where the control doesn't apply to the host."""
    if not rows_by_host:
        return []
    hosts = list(rows_by_host)
    controls = []                          # every control once, in the order first seen
    for rows in rows_by_host.values():
        for row in rows:
            if row["control"] not in [control["control"] for control in controls]:
                controls.append(row)
    lines = [
        "",
        "Baseline controls per host (✅ pass · ❌ fail · – not applicable):",
        "",
        "| Control | Area | " + " | ".join(hosts) + " |",
        "|---|---|" + "---|" * len(hosts),
    ]
    for control in controls:
        cells = []
        for host in hosts:
            row = next((r for r in rows_by_host[host] if r["control"] == control["control"]), None)
            cells.append("–" if row is None else ("✅" if row["after"] == "pass" else "❌"))
        lines.append(f"| {control['title']} | {control['area']} | " + " | ".join(cells) + " |")
    return lines


def _history(history: list[dict]) -> list[str]:
    lines = ["## Build history, issues and fixes", ""]
    if not history:
        return lines + ["No builds recorded yet.", ""]
    lines += ["| When | Action | Result | Duration | Details |", "|---|---|---|---|---|"]
    for entry in history[-30:]:
        result = "OK" if entry.get("ok") else "FAILED"
        details = (entry.get("error") or "") + (": " + entry["message"] if entry.get("message") else "")
        if entry.get("kind") == "apply" and entry.get("ok"):
            details = "no changes" if entry.get("changed") is False else "changes applied"
        lines.append(f"| {entry.get('started') or '-'} | {entry.get('kind')} | {result} | "
                     f"{entry.get('seconds') or '-'} s | {details.replace('|', '/')[:200]} |")
    return lines + [""]
