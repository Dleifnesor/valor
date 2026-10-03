"""Build journal (FR-13): one wiki-style Markdown page per range in journals/<range>.md."""

from __future__ import annotations

import time
from pathlib import Path

from .cluster import range_vms
from .pve import PVE
from .spec import ROUTER, RangeSpec, effective_tests, spec_hash
from . import state


def _mermaid(spec: RangeSpec) -> list[str]:
    out = ["```mermaid", "flowchart LR", '  inet(("Internet"))', '  rtr["rtr<br/>router · NAT · nftables"]',
           '  inet --- |"uplink (NAT, private nets blocked)"| rtr']
    for s in spec.segments:
        out.append(f'  subgraph {s.name}["{s.name} · VLAN {s.vlan} · {s.cidr}{" · internet" if s.internet else ""}"]')
        for h in spec.hosts_in(s.name):
            svc = ", ".join(r.name for r in h.roles)
            out.append(f'    {h.name}["{h.name}<br/>{h.address}{"<br/>" + svc if svc else ""}"]')
        out.append("  end")
        out.append(f"  rtr --- |{s.gateway}| {s.name}")
    for i, r in enumerate(spec.policy):
        label = f"{r.proto} {','.join(map(str, r.ports))}" if r.ports else r.proto
        out.append(f'  {r.from_} -. "{label}" .-> {r.to}')
    out.append("```")
    return out


def write(cfg, pve: PVE, spec: RangeSpec, spec_path: Path | None = None) -> Path:
    vms = {vm.host: vm for vm in range_vms(pve, spec.name)}
    ap = state.load(cfg, spec.name, "apply") or {}
    vr = state.load(cfg, spec.name, "verify") or {}
    hist = state.history(cfg, spec.name)
    L: list[str] = []
    w = L.append
    w(f"# Range `{spec.name}`")
    w("")
    w(f"> {spec.description}" if spec.description else "> (no description)")
    w("")
    w("| | |")
    w("|---|---|")
    w(f"| Spec | `{spec_path.name if spec_path else spec.name + '.yaml'}` · version `{spec_hash(spec)[:12]}` |")
    w(f"| Last apply | {ap.get('started', '-')} · {'OK' if ap.get('ok') else 'FAILED' if ap else '-'}"
      f"{' · ' + str(ap.get('seconds')) + ' s' if ap.get('seconds') is not None else ''} |")
    vs = vr.get("summary", {})
    last_verify = "not run"
    if vr:
        last_verify = (f"{vr.get('finished', vr.get('started', '-'))} · {'PASS' if vr.get('ok') else 'FAIL'} · "
                       f"tests {vs.get('tests_passed')}/{vs.get('tests_total')} · "
                       f"baseline {vs.get('baseline_passed')}/{vs.get('baseline_total')}")
    w(f"| Last verification | {last_verify} |")
    w(f"| Baseline | `{spec.baseline}` (results are *aligned with* the baseline's themes, not a certification) |")
    w(f"| Generated | {time.strftime('%Y-%m-%d %H:%M %Z')} by the VALOR engine |")
    w("")
    w("## Topology")
    w("")
    L.extend(_mermaid(spec))
    w("")
    w("## Hosts")
    w("")
    w("| Host | Segment | Address | OS | vCPU / RAM / disk | Services | VMID | State |")
    w("|---|---|---|---|---|---|---|---|")
    r = spec.router
    rv = vms.get(ROUTER)
    w(f"| rtr (router) | all | uplink DHCP; " + ", ".join(str(s.gateway) for s in spec.segments) +
      f" | {r.os} | {r.cores} / {r.memory} MiB / {r.disk} GiB | routing, NAT, policy | {rv.vmid if rv else '-'} | {rv.status if rv else 'absent'} |")
    for h in spec.hosts:
        v = vms.get(h.name)
        svc = ", ".join(x.name for x in h.roles) or "-"
        w(f"| {h.name} | {h.segment} | {h.address} | {h.os} | {h.cores} / {h.memory} MiB / {h.disk} GiB | {svc} | "
          f"{v.vmid if v else '-'} | {v.status if v else 'absent'} |")
    w("")
    w("## Network")
    w("")
    w("| Segment | VLAN | Network | Gateway | Internet egress |")
    w("|---|---|---|---|---|")
    for s in spec.segments:
        w(f"| {s.name} | {s.vlan} | {s.cidr} | {s.gateway} | {'yes (public addresses only)' if s.internet else 'no'} |")
    w("")
    w("## Traffic policy")
    w("")
    w("Everything between segments is **denied** unless listed here. Internet egress never reaches private addresses "
      "(home LAN, cluster network).")
    w("")
    w("| From | To | Protocol | Ports | Note |")
    w("|---|---|---|---|---|")
    for p in spec.policy:
        w(f"| {p.from_} | {p.to} | {p.proto} | {', '.join(map(str, p.ports)) or '-'} | {p.description or ''} |")
    if not spec.policy:
        w("| - | - | - | - | no inter-segment traffic allowed |")
    w("")
    w("## Verification")
    w("")
    if not vr:
        w("Not verified yet. Planned tests:")
        w("")
        w("| Test | Expect |")
        w("|---|---|")
        for t in effective_tests(spec):
            w(f"| {t['name']} | {t['expect']} |")
    else:
        w(f"**{'PASS' if vr.get('ok') else 'FAIL'}** · tests {vs.get('tests_passed')}/{vs.get('tests_total')} · "
          f"isolation {vs.get('isolation_passed')}/{vs.get('isolation_total')} · "
          f"baseline controls {vs.get('baseline_passed')}/{vs.get('baseline_total')} · {vr.get('seconds')} s")
        w("")
        w("| Test | From | Target | Expect | Observed | Result |")
        w("|---|---|---|---|---|---|")
        for t in vr.get("tests", []):
            w(f"| {t['name']} | {t['from']} | {t['target']} | {t['expect']} | {t['observed']} ({t['detail']}) | "
              f"{'✅' if t['pass'] else '❌'} |")
        base = vr.get("baseline", {})
        if base:
            hosts = list(base)
            controls = []
            for rows in base.values():
                for row in rows:
                    if row["control"] not in [c["control"] for c in controls]:
                        controls.append(row)
            w("")
            w("Baseline controls per host (✅ pass · ❌ fail · – not applicable):")
            w("")
            w("| Control | Area | " + " | ".join(hosts) + " |")
            w("|---|---|" + "---|" * len(hosts))
            for c in controls:
                cells = []
                for hname in hosts:
                    row = next((x for x in base[hname] if x["control"] == c["control"]), None)
                    cells.append("–" if row is None else ("✅" if row["after"] == "pass" else "❌"))
                w(f"| {c['title']} | {c['area']} | " + " | ".join(cells) + " |")
    w("")
    w("## Build history, issues and fixes")
    w("")
    if not hist:
        w("No builds recorded yet.")
    else:
        w("| When | Action | Result | Duration | Details |")
        w("|---|---|---|---|---|")
        for h in hist[-30:]:
            res = "OK" if h.get("ok") else "FAILED"
            det = (h.get("error") or "") + (": " + h["message"] if h.get("message") else "")
            if h.get("kind") == "apply" and h.get("ok"):
                det = "no changes" if h.get("changed") is False else "changes applied"
            w(f"| {h.get('started') or '-'} | {h.get('kind')} | {res} | {h.get('seconds') or '-'} s | {det.replace('|', '/')[:200]} |")
    w("")
    w("<!-- Agent notes: add a short 'Fix:' line under an issue when you resolve it; keep this marker. -->")
    notes_path = cfg.journals_dir / f"{spec.name}.notes.md"
    if notes_path.exists():
        w("")
        w("### Notes from the operator / agent")
        w("")
        w(notes_path.read_text().strip())
    out = cfg.journals_dir / f"{spec.name}.md"
    out.write_text("\n".join(L) + "\n")
    return out
