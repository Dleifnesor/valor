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
    known = mapping(baselines_dir)["frameworks"]
    needed = [known[name]["profile"] for name in frameworks if name in known]
    return max(needed, key=lambda profile: STRENGTH.get(profile, 0), default="ubuntu-l1")


def design_checks(spec: RangeSpec, tests: list[dict] | None = None, families: dict[str, str] | None = None) -> list[dict]:
    """Checks of the range's design. tests: verification results (isolation evidence); families: host -> OS family."""
    out = []
    # 1. Deny by default: before a build it holds by construction; after one, the isolation tests prove it.
    closed = [test for test in tests or [] if test.get("expect") == "closed"]
    if tests is not None:
        passed = sum(test.get("pass", False) for test in closed)
        out.append({"id": "deny-by-default", "pass": all(test.get("pass") for test in closed),
                    "detail": f"{passed}/{len(closed)} isolation tests passed"})
    else:
        out.append({"id": "deny-by-default", "pass": True,
                    "detail": "the router denies by default; checked by the isolation tests"})

    # 2. Segments with regulated data stay off the internet.
    compliance = spec.compliance
    if compliance and compliance.scope:
        open_segments = [name for name in compliance.scope if spec.segment(name).internet]
        out.append({"id": "scope-no-internet", "pass": not open_segments,
                    "detail": (f"internet access on {', '.join(open_segments)}" if open_segments
                               else f"no internet access on {', '.join(compliance.scope)}")})

    # 3. Central logging: a syslog server, and every other Linux host forwarding to it.
    servers = [host.name for host in spec.hosts if any(ref.name == "syslog-server" for ref in host.roles)]
    linux = [host.name for host in spec.hosts if (families or {}).get(host.name, "debian") != "windows"]
    missing = [name for name in linux if name not in servers
               and not any(ref.name == "syslog-client" for ref in spec.host(name).roles)]
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
    maps = mapping(baselines_dir)
    out = []
    for check in design_checks(spec, None, families):
        if check["pass"]:
            continue
        requirements = []
        for framework in spec.compliance.frameworks:
            family = maps["frameworks"][framework]["family"]
            ids = _ids(maps, maps["design"][check["id"]].get(family, []), framework)
            requirements += [f"{framework} {rid}" for rid in ids]
        if requirements:
            out.append({"location": "compliance",
                        "message": f"{maps['design'][check['id']]['title']}: {check['detail']} "
                                   f"(supports {', '.join(requirements)})"})
    if not spec.compliance.scope and any(framework != "cis-l1" for framework in spec.compliance.frameworks):
        out.append({"location": "compliance.scope", "message": "compliance.scope names no segment: say which segments "
                    "hold the regulated data (CUI, cardholder data, ePHI) so VALOR can check they stay off the internet"})
    return out


def _ids(maps: dict, ids: list[str], framework: str) -> list[str]:
    """Requirement ids, limited to the framework's `only` list when it has one (e.g. NIST 800-53 low)."""
    only = maps["frameworks"][framework].get("only")
    return [rid for rid in ids if only is None or rid in only]


def report(baselines_dir: Path, spec: RangeSpec, baseline_rows: dict[str, list[dict]], tests: list[dict],
           families: dict[str, str]) -> dict | None:
    """Per framework: each requirement VALOR has evidence for, with that evidence and a status
    (pass / partial / fail), plus the requirements VALOR does not cover."""
    if not spec.compliance or not spec.compliance.frameworks:
        return None
    maps = mapping(baselines_dir)
    design = design_checks(spec, tests, families)
    hosts = [host for host, rows in baseline_rows.items() if rows and host != ROUTER]
    frameworks = {name: _framework_report(maps, name, design, baseline_rows, hosts)
                  for name in spec.compliance.frameworks}
    return {"claim": CLAIM, "frameworks": frameworks, "design": design}


def _framework_report(maps: dict, name: str, design: list[dict], baseline_rows: dict[str, list[dict]],
                      hosts: list[str]) -> dict:
    """One framework's part of the report. Evidence comes from the baseline controls on each host (mapped to
    requirement ids, or for CIS to themes) and from the design checks."""
    framework = maps["frameworks"][name]
    family = framework["family"]
    titles = maps["requirements"].get(family, {})
    requirements: dict[str, dict] = {}

    def add(rid: str, evidence: dict) -> None:
        entry = requirements.setdefault(rid, {"id": rid, "title": titles.get(rid, rid), "evidence": []})
        entry["evidence"].append(evidence)

    for host, rows in baseline_rows.items():
        for row in rows:
            if family == "cis":
                ids = [row["theme"]] if row.get("theme") else []
            else:
                ids = _ids(maps, (maps["controls"].get(row["control"]) or {}).get(family, []), name)
            for rid in ids:
                add(rid, {"kind": "control", "control": row["control"], "title": row["title"], "host": host,
                          "pass": row["after"] == "pass"})
    if family != "cis":
        for check in design:
            for rid in _ids(maps, maps["design"][check["id"]].get(family, []), name):
                add(rid, {"kind": "design", "check": check["id"], "title": maps["design"][check["id"]]["title"],
                          "detail": check["detail"], "pass": check["pass"]})

    for requirement in requirements.values():
        evidence = requirement["evidence"]
        covered = {item["host"] for item in evidence if item["kind"] == "control"}
        # A requirement met by host settings needs evidence from every host; design evidence covers the range.
        host_level = family != "cis" and bool(covered) and not any(item["kind"] == "design" for item in evidence)
        missing = sorted(set(hosts) - covered) if host_level else []
        if not all(item["pass"] for item in evidence):
            requirement["status"] = "fail"
        else:
            requirement["status"] = "partial" if missing else "pass"
        if missing:
            requirement["no_evidence_on"] = missing

    ordered = sorted(requirements.values(), key=lambda requirement: _order(requirement["id"]))
    not_covered = ([{"id": rid, "title": title} for rid, title in titles.items() if rid not in requirements]
                   if family != "cis" else [])
    return {"title": framework["title"], "about": framework.get("about", ""), "requirements": ordered,
            "passed": sum(r["status"] == "pass" for r in ordered),
            "partial": sum(r["status"] == "partial" for r in ordered),
            "failed": sum(r["status"] == "fail" for r in ordered),
            "not_covered": not_covered}


def _order(rid: str):
    """Natural order: 3.1.10 after 3.1.9, AC-6(9) after AC-6, 164.308(...) before 164.312(...)."""
    import re
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", rid)]


def reference(baselines_dir: Path) -> str:
    """The frameworks for the model: ids, titles and what each needs."""
    try:
        maps = mapping(baselines_dir)
    except ValorError:
        return ""
    lines = [f"- {name}: {framework['title']} - {framework.get('about', '')} (baseline: {framework['profile']})"
             for name, framework in maps["frameworks"].items()]
    return "\n".join(lines)
