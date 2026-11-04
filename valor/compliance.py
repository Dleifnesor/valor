# *adjusts tiny glasses and opens the rulebook* OwO valor/compliance.py maps ranges to compliance frameworks! ^w^
# Frameworks like NIST SP 800-171, 800-53, PCI DSS, HIPAA and CIS have requirements. This file knows:
#   - which baseline profile a framework needs (required_profile)
#   - design checks of a range: deny-by-default, regulated segments off the internet, central logging
#   - the REPORT: for each framework requirement, the evidence (baseline controls on each host, design checks)
#     and a status (pass / partial / fail), plus what VALOR doesn't cover
# Everything is worded "aligned with": VALOR checks technical settings; it doesn't assess or certify (most of any
# framework is about the organization, not a lab) *wags tail honestly*
"""Compliance frameworks: which baseline profile a framework needs, design checks of a range, and the report that
maps verification evidence (baseline controls on each host, design checks) to the frameworks' requirement ids.
Everything is reported as "aligned with" - VALOR builds and checks technical settings, it does not assess or
certify (most requirements of every framework are organizational and outside a lab build)."""

# Lazy type hints UwU
from __future__ import annotations

# lru_cache remembers the parsed mapping file; Path finds it >w<
from functools import lru_cache
from pathlib import Path

# PyYAML reads the mapping OwO
import yaml

# ValorError; ROUTER; RangeSpec (the parsed spec) ^w^
from .errors import ValorError
from .spec import ROUTER, RangeSpec

# CLAIM: the exact sentence the report shows about what it means (and doesn't mean!) UwU
# STRENGTH: the Linux baseline profiles from weakest to strongest, to pick the weakest one that covers everything
CLAIM = ("aligned with the selected frameworks' technical requirements that VALOR covers; "
         "not an assessment or certification")
STRENGTH = {"none": 0, "ubuntu-l1": 1, "linux-moderate": 2}     # profiles, weakest first


# _load: parse the mapping YAML, cached. The file's modification time is part of the cache key, so an edited file
# is read again; maxsize=4 keeps at most 4 versions >w<
@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text())


# mapping: baselines/frameworks/frameworks.yaml (frameworks, their requirements, which controls/design checks
# support which requirement ids), or a clear error if it's missing OwO
def mapping(baselines_dir: Path) -> dict:
    path = baselines_dir / "frameworks" / "frameworks.yaml"
    if not path.is_file():
        raise ValorError("frameworks_missing", "the compliance framework mapping is missing",
                         hint=f"Expected {path} (part of every VALOR release).")
    return _load(str(path), path.stat().st_mtime)


# required_profile: the weakest profile that covers every chosen framework (ubuntu-l1 when none needs more) ^w^
def required_profile(baselines_dir: Path, frameworks: list[str]) -> str:
    """The weakest Linux baseline profile that covers every framework (its Windows counterpart comes with it)."""
    known = mapping(baselines_dir)["frameworks"]
    needed = [known[name]["profile"] for name in frameworks if name in known]
    return max(needed, key=lambda profile: STRENGTH.get(profile, 0), default="ubuntu-l1")


# design_checks: checks about the range's DESIGN (not one host's settings) UwU
#   tests     verification results (None before a build); families = host -> OS family
def design_checks(spec: RangeSpec, tests: list[dict] | None = None, families: dict[str, str] | None = None) -> list[dict]:
    """Checks of the range's design. tests: verification results (isolation evidence); families: host -> OS family."""
    out = []
    # 1. Deny by default: before a build it holds by construction; after one, the isolation tests prove it.
    # The isolation tests that expect a connection to be BLOCKED >w<
    closed = [test for test in tests or [] if test.get("expect") == "closed"]
    if tests is not None:
        # After a build: deny-by-default passes when every "should be closed" test passed OwO
        passed = sum(test.get("pass", False) for test in closed)
        out.append({"id": "deny-by-default", "pass": all(test.get("pass") for test in closed),
                    "detail": f"{passed}/{len(closed)} isolation tests passed"})
    else:
        # Before a build: it holds by construction (the router denies by default) ^w^
        out.append({"id": "deny-by-default", "pass": True,
                    "detail": "the router denies by default; checked by the isolation tests"})

    # 2. Segments with regulated data stay off the internet.
    # Regulated segments (compliance.scope) must not have internet access UwU
    compliance = spec.compliance
    if compliance and compliance.scope:
        open_segments = [name for name in compliance.scope if spec.segment(name).internet]
        out.append({"id": "scope-no-internet", "pass": not open_segments,
                    "detail": (f"internet access on {', '.join(open_segments)}" if open_segments
                               else f"no internet access on {', '.join(compliance.scope)}")})

    # 3. Central logging: a syslog server, and every other Linux host forwarding to it.
    # The syslog servers, the Linux hosts (anything not Windows), and Linux hosts that neither are a log server nor
    # forward to one >w<
    servers = [host.name for host in spec.hosts if any(ref.name == "syslog-server" for ref in host.roles)]
    linux = [host.name for host in spec.hosts if (families or {}).get(host.name, "debian") != "windows"]
    missing = [name for name in linux if name not in servers
               and not any(ref.name == "syslog-client" for ref in spec.host(name).roles)]
    # Describe the result: the log server(s) and who doesn't forward, or "no log server" OwO
    if servers:
        detail = (f"log server {', '.join(servers)}" + (f"; not forwarding: {', '.join(missing)}" if missing else
                                                        "; every other Linux host forwards to it"))
    else:
        detail = "no host has the syslog-server role"
    out.append({"id": "central-logging", "pass": bool(servers) and not missing, "detail": detail})
    return out


# design_warnings: shown when PLANNING: design checks a chosen framework relies on that the spec doesn't meet
# yet, with the requirement ids they support ^w^
def design_warnings(baselines_dir: Path, spec: RangeSpec, families: dict[str, str]) -> list[dict]:
    """Plan-time warnings: design checks a selected framework relies on that the spec does not meet yet."""
    if not spec.compliance or not spec.compliance.frameworks:
        return []
    maps = mapping(baselines_dir)
    out = []
    # Only failing checks matter UwU
    for check in design_checks(spec, None, families):
        if check["pass"]:
            continue
        # Which requirements of each chosen framework this check supports >w<
        requirements = []
        for framework in spec.compliance.frameworks:
            family = maps["frameworks"][framework]["family"]
            ids = _ids(maps, maps["design"][check["id"]].get(family, []), framework)
            requirements += [f"{framework} {rid}" for rid in ids]
        if requirements:
            out.append({"location": "compliance",
                        "message": f"{maps['design'][check['id']]['title']}: {check['detail']} "
                                   f"(supports {', '.join(requirements)})"})
    # A framework other than CIS with no compliance.scope: remind the person to say where the regulated data lives OwO
    if not spec.compliance.scope and any(framework != "cis-l1" for framework in spec.compliance.frameworks):
        out.append({"location": "compliance.scope", "message": "compliance.scope names no segment: say which segments "
                    "hold the regulated data (CUI, cardholder data, ePHI) so VALOR can check they stay off the internet"})
    return out


# _ids: requirement ids, filtered to a framework's `only` list when it has one (e.g. 800-53 Low uses a subset) ^w^
def _ids(maps: dict, ids: list[str], framework: str) -> list[str]:
    """Requirement ids, limited to the framework's `only` list when it has one (e.g. NIST 800-53 low)."""
    only = maps["frameworks"][framework].get("only")
    return [rid for rid in ids if only is None or rid in only]


# report: the full compliance report for a verified range (None when no framework is chosen) UwU
#   baseline_rows  per host, the baseline control results; tests = verification results; families = OS families
def report(baselines_dir: Path, spec: RangeSpec, baseline_rows: dict[str, list[dict]], tests: list[dict],
           families: dict[str, str]) -> dict | None:
    """Per framework: each requirement VALOR has evidence for, with that evidence and a status
    (pass / partial / fail), plus the requirements VALOR does not cover."""
    if not spec.compliance or not spec.compliance.frameworks:
        return None
    # Run the design checks with the test results; the hosts with baseline evidence (not the router) >w<
    maps = mapping(baselines_dir)
    design = design_checks(spec, tests, families)
    hosts = [host for host, rows in baseline_rows.items() if rows and host != ROUTER]
    # One report per chosen framework OwO
    frameworks = {name: _framework_report(maps, name, design, baseline_rows, hosts)
                  for name in spec.compliance.frameworks}
    return {"claim": CLAIM, "frameworks": frameworks, "design": design}


# _framework_report: one framework's part of the report ^w^
def _framework_report(maps: dict, name: str, design: list[dict], baseline_rows: dict[str, list[dict]],
                      hosts: list[str]) -> dict:
    """One framework's part of the report. Evidence comes from the baseline controls on each host (mapped to
    requirement ids, or for CIS to themes) and from the design checks."""
    # The framework, its family (nist / pci / hipaa / cis...), the requirement titles, and the requirements found UwU
    framework = maps["frameworks"][name]
    family = framework["family"]
    titles = maps["requirements"].get(family, {})
    requirements: dict[str, dict] = {}

    # add(): attach one piece of evidence to a requirement (creating its entry the first time) >w<
    def add(rid: str, evidence: dict) -> None:
        entry = requirements.setdefault(rid, {"id": rid, "title": titles.get(rid, rid), "evidence": []})
        entry["evidence"].append(evidence)

    # Evidence from baseline controls: for CIS by theme, for others by the controls -> requirement ids mapping OwO
    for host, rows in baseline_rows.items():
        for row in rows:
            if family == "cis":
                ids = [row["theme"]] if row.get("theme") else []
            else:
                ids = _ids(maps, (maps["controls"].get(row["control"]) or {}).get(family, []), name)
            for rid in ids:
                add(rid, {"kind": "control", "control": row["control"], "title": row["title"], "host": host,
                          "pass": row["after"] == "pass"})
    # Evidence from the design checks (not for CIS, which is host settings only) ^w^
    if family != "cis":
        for check in design:
            for rid in _ids(maps, maps["design"][check["id"]].get(family, []), name):
                add(rid, {"kind": "design", "check": check["id"], "title": maps["design"][check["id"]]["title"],
                          "detail": check["detail"], "pass": check["pass"]})

    # Decide each requirement's status UwU
    for requirement in requirements.values():
        evidence = requirement["evidence"]
        # Which hosts gave control evidence >w<
        covered = {item["host"] for item in evidence if item["kind"] == "control"}
        # A requirement met by host settings needs evidence from every host; design evidence covers the range.
        host_level = family != "cis" and bool(covered) and not any(item["kind"] == "design" for item in evidence)
        missing = sorted(set(hosts) - covered) if host_level else []
        # Any failing evidence = fail; all passing but some hosts missing = partial; otherwise pass OwO
        if not all(item["pass"] for item in evidence):
            requirement["status"] = "fail"
        else:
            requirement["status"] = "partial" if missing else "pass"
        if missing:
            requirement["no_evidence_on"] = missing

    # Requirements in natural order, the ones VALOR doesn't cover (not for CIS), and counts per status ^w^
    ordered = sorted(requirements.values(), key=lambda requirement: _order(requirement["id"]))
    not_covered = ([{"id": rid, "title": title} for rid, title in titles.items() if rid not in requirements]
                   if family != "cis" else [])
    return {"title": framework["title"], "about": framework.get("about", ""), "requirements": ordered,
            "passed": sum(r["status"] == "pass" for r in ordered),
            "partial": sum(r["status"] == "partial" for r in ordered),
            "failed": sum(r["status"] == "fail" for r in ordered),
            "not_covered": not_covered}


# _order: a sort key for "natural" order: split into digit and non-digit parts and compare digits as numbers UwU
def _order(rid: str):
    """Natural order: 3.1.10 after 3.1.9, AC-6(9) after AC-6, 164.308(...) before 164.312(...)."""
    import re
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", rid)]


# reference: one line per framework for the AI's prompt (empty if the mapping is missing) >w< *closes the rulebook*
def reference(baselines_dir: Path) -> str:
    """The frameworks for the model: ids, titles and what each needs."""
    try:
        maps = mapping(baselines_dir)
    except ValorError:
        return ""
    lines = [f"- {name}: {framework['title']} - {framework.get('about', '')} (baseline: {framework['profile']})"
             for name, framework in maps["frameworks"].items()]
    return "\n".join(lines)
