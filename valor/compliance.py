"""Compliance frameworks: which baseline profile a framework needs, design checks of a range, and the report that
maps verification evidence (baseline controls on each host, design checks) to the frameworks' requirement ids.
Everything is reported as "aligned with" - VALOR builds and checks technical settings, it does not assess or
certify (most requirements of every framework are organizational and outside a lab build)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .errors import ValorError
from .spec import ROUTER, RangeSpec

CLAIM = ("aligned with the selected frameworks' technical requirements that VALOR covers; "
         "not an assessment or certification")
STRENGTH = {"none": 0, "ubuntu-l1": 1, "linux-moderate": 2}     # profiles, weakest first


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text())


def mapping(baselines_dir: Path) -> dict:
    path = baselines_dir / "frameworks" / "frameworks.yaml"
    if not path.is_file():
        raise ValorError("frameworks_missing", "the compliance framework mapping is missing",
                         hint=f"Expected {path} (part of every VALOR release).")
    return _load(str(path), path.stat().st_mtime)


def required_profile(baselines_dir: Path, frameworks: list[str]) -> str:
    """The weakest Linux baseline profile that covers every framework (its Windows counterpart comes with it)."""
    fws = mapping(baselines_dir)["frameworks"]
    need = [fws[f]["profile"] for f in frameworks if f in fws]
    return max(need, key=lambda p: STRENGTH.get(p, 0), default="ubuntu-l1")


def design_checks(spec: RangeSpec, tests: list[dict] | None = None, families: dict[str, str] | None = None) -> list[dict]:
    """Checks of the range's design. tests: verification results (isolation evidence); families: host -> OS family."""
    out = []
    closed = [t for t in tests or [] if t.get("expect") == "closed"]
    out.append({"id": "deny-by-default", "pass": all(t.get("pass") for t in closed) if tests is not None else True,
                "detail": (f"{sum(t.get('pass', False) for t in closed)}/{len(closed)} isolation tests passed"
                           if tests is not None else "the router denies by default; checked by the isolation tests")})
    comp = spec.compliance
    if comp and comp.scope:
        open_ = [s for s in comp.scope if spec.segment(s).internet]
        out.append({"id": "scope-no-internet", "pass": not open_,
                    "detail": (f"internet access on {', '.join(open_)}" if open_
                               else f"no internet access on {', '.join(comp.scope)}")})
    servers = [h.name for h in spec.hosts if any(r.name == "syslog-server" for r in h.roles)]
    linux = [h.name for h in spec.hosts if (families or {}).get(h.name, "debian") != "windows"]
    missing = [h for h in linux if h not in servers
               and not any(r.name == "syslog-client" for r in spec.host(h).roles)]
    if servers:
        detail = (f"log server {', '.join(servers)}" + (f"; not forwarding: {', '.join(missing)}" if missing else
                                                        "; every other Linux host forwards to it"))
    else:
        detail = "no host has the syslog-server role"
    out.append({"id": "central-logging", "pass": bool(servers) and not missing, "detail": detail})
    return out


def design_warnings(baselines_dir: Path, spec: RangeSpec, families: dict[str, str]) -> list[dict]:
    """Plan-time warnings: design checks a selected framework relies on that the spec does not meet yet."""
    if not spec.compliance or not spec.compliance.frameworks:
        return []
    m = mapping(baselines_dir)
    out = []
    for d in design_checks(spec, None, families):
        if d["pass"]:
            continue
        reqs = []
        for f in spec.compliance.frameworks:
            fam = m["frameworks"][f]["family"]
            ids = _ids(m, m["design"][d["id"]].get(fam, []), f)
            reqs += [f"{f} {i}" for i in ids]
        if reqs:
            out.append({"location": "compliance", "message": f"{m['design'][d['id']]['title']}: {d['detail']} "
                                                             f"(supports {', '.join(reqs)})"})
    if not spec.compliance.scope and any(f != "cis-l1" for f in spec.compliance.frameworks):
        out.append({"location": "compliance.scope", "message": "compliance.scope names no segment: say which segments "
                    "hold the regulated data (CUI, cardholder data, ePHI) so VALOR can check they stay off the internet"})
    return out


def _ids(m: dict, ids: list[str], framework: str) -> list[str]:
    only = m["frameworks"][framework].get("only")
    return [i for i in ids if only is None or i in only]


def report(baselines_dir: Path, spec: RangeSpec, baseline_rows: dict[str, list[dict]], tests: list[dict],
           families: dict[str, str]) -> dict | None:
    """Per framework: each requirement VALOR has evidence for, with that evidence and a status
    (pass / partial / fail), plus the requirements VALOR does not cover."""
    if not spec.compliance or not spec.compliance.frameworks:
        return None
    m = mapping(baselines_dir)
    design = design_checks(spec, tests, families)
    hosts = [h for h, rows in baseline_rows.items() if rows and h != ROUTER]
    out = {}
    for f in spec.compliance.frameworks:
        fw = m["frameworks"][f]
        fam = fw["family"]
        titles = m["requirements"].get(fam, {})
        reqs: dict[str, dict] = {}

        def add(rid: str, ev: dict) -> None:
            reqs.setdefault(rid, {"id": rid, "title": titles.get(rid, rid), "evidence": []})["evidence"].append(ev)

        for host, rows in baseline_rows.items():
            for r in rows:
                if fam == "cis":
                    ids = [r["theme"]] if r.get("theme") else []
                else:
                    ids = _ids(m, (m["controls"].get(r["control"]) or {}).get(fam, []), f)
                for rid in ids:
                    add(rid, {"kind": "control", "control": r["control"], "title": r["title"], "host": host,
                              "pass": r["after"] == "pass"})
        if fam != "cis":
            for d in design:
                for rid in _ids(m, m["design"][d["id"]].get(fam, []), f):
                    add(rid, {"kind": "design", "check": d["id"], "title": m["design"][d["id"]]["title"],
                              "detail": d["detail"], "pass": d["pass"]})
        for r in reqs.values():
            ev = r["evidence"]
            covered = {e["host"] for e in ev if e["kind"] == "control"}
            host_level = fam != "cis" and bool(covered) and not any(e["kind"] == "design" for e in ev)
            missing = sorted(set(hosts) - covered) if host_level else []
            r["status"] = ("fail" if not all(e["pass"] for e in ev) else "partial" if missing else "pass")
            if missing:
                r["no_evidence_on"] = missing
        ordered = sorted(reqs.values(), key=lambda r: _order(r["id"]))
        not_covered = [{"id": k, "title": v} for k, v in titles.items() if k not in reqs] if fam != "cis" else []
        out[f] = {"title": fw["title"], "about": fw.get("about", ""), "requirements": ordered,
                  "passed": sum(r["status"] == "pass" for r in ordered),
                  "partial": sum(r["status"] == "partial" for r in ordered),
                  "failed": sum(r["status"] == "fail" for r in ordered),
                  "not_covered": not_covered}
    return {"claim": CLAIM, "frameworks": out, "design": design}


def _order(rid: str):
    """Natural order: 3.1.10 after 3.1.9, AC-6(9) after AC-6, 164.308(...) before 164.312(...)."""
    import re
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", rid)]


def reference(baselines_dir: Path) -> str:
    """The frameworks for the model: ids, titles and what each needs."""
    try:
        m = mapping(baselines_dir)
    except ValorError:
        return ""
    lines = [f"- {k}: {v['title']} - {v.get('about', '')} (baseline: {v['profile']})" for k, v in m["frameworks"].items()]
    return "\n".join(lines)
