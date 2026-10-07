"""Compliance frameworks: profile inheritance, the framework mapping, design checks, validation and the report."""

from pathlib import Path

import pytest
import yaml

from valor import compliance
from valor.baseline import baseline_for, load_baseline
from valor.errors import ValorError
from valor.spec import canonical, normalize, parse_spec

from test_windows import ValidatePVE

ROOT = Path(__file__).resolve().parent.parent
BASELINES = ROOT / "baselines"

SPEC = """
name: cui
baseline: {baseline}
{compliance}
segments:
  - {{name: dmz, vlan: 700, cidr: 10.70.0.0/24, internet: true}}
  - {{name: cui, vlan: 701, cidr: 10.71.0.0/24}}
hosts:
  - name: web
    segment: dmz
    address: 10.70.0.10
    roles: [{{name: syslog-client, params: {{server: 10.71.0.10}}}}]
  - name: files
    segment: cui
    address: 10.71.0.20
  - name: logs
    segment: cui
    address: 10.71.0.10
    roles: [{{name: syslog-server}}]
  - {{name: dc, segment: cui, address: 10.71.0.5, os: windows-server-2022}}
"""


def spec_of(baseline="linux-moderate", comp="compliance: {frameworks: [nist-800-171, pci-dss], scope: [cui]}"):
    return normalize(parse_spec(SPEC.format(baseline=baseline, compliance=comp)), "ubuntu-24.04")


def test_profiles_extend_and_digests_follow_the_parent(tmp_path):
    l1, mod = load_baseline(BASELINES, "ubuntu-l1"), load_baseline(BASELINES, "linux-moderate")
    ids = [c.id for c in mod.controls]
    assert set(c.id for c in l1.controls) < set(ids) and len(ids) == len(set(ids))
    assert {"account-lockout", "password-quality", "audit-rules", "time-sync"} <= set(ids)
    assert mod.windows == "windows-moderate" and mod.digest != l1.digest
    win = load_baseline(BASELINES, "windows-moderate")
    assert {c.id for c in load_baseline(BASELINES, "windows-l1").controls} < {c.id for c in win.controls}

    for f in ("ubuntu-l1", "linux-moderate"):
        (tmp_path / f"{f}.yaml").write_text((BASELINES / f"{f}.yaml").read_text())
    before = load_baseline(tmp_path, "linux-moderate").digest
    (tmp_path / "ubuntu-l1.yaml").write_text((BASELINES / "ubuntu-l1.yaml").read_text() + "\n# changed\n")
    assert load_baseline(tmp_path, "linux-moderate").digest != before        # a parent change re-converges hosts

    (tmp_path / "loop.yaml").write_text("id: loop\ntitle: x\nextends: loop\ncontrols: []\n")
    with pytest.raises(ValorError, match="extends itself"):
        load_baseline(tmp_path, "loop")


def test_windows_hosts_get_the_profiles_windows_counterpart():
    assert baseline_for(BASELINES, "linux-moderate", "windows").id == "windows-moderate"
    assert baseline_for(BASELINES, "ubuntu-l1", "windows").id == "windows-l1"
    assert baseline_for(BASELINES, "linux-moderate", "rhel").id == "linux-moderate"
    assert baseline_for(BASELINES, "none", "windows") is None


def test_mapping_is_consistent_with_the_baselines():
    m = compliance.mapping(BASELINES)
    controls = {c.id for name in ("linux-moderate", "windows-moderate")
                for c in load_baseline(BASELINES, name).controls}
    assert set(m["controls"]) <= controls, set(m["controls"]) - controls
    for cid, fams in m["controls"].items():
        for fam, ids in fams.items():
            assert set(ids) <= set(m["requirements"][fam]), (cid, fam, set(ids) - set(m["requirements"][fam]))
    for check, d in m["design"].items():
        for fam, ids in d.items():
            if fam != "title":
                assert set(ids) <= set(m["requirements"][fam]), (check, fam)
    for fid, fw in m["frameworks"].items():
        assert (BASELINES / f"{fw['profile']}.yaml").is_file() and fid in compliance.spec_frameworks()
        assert set(fw.get("only") or []) <= set(m["requirements"].get(fw["family"], {})), fid
    assert compliance.required_profile(BASELINES, ["cis-l1"]) == "ubuntu-l1"
    assert compliance.required_profile(BASELINES, ["cis-l1", "hipaa"]) == "linux-moderate"


def test_spec_field_and_hash_stability():
    s = spec_of()
    assert s.compliance.frameworks == ["nist-800-171", "pci-dss"] and s.compliance.scope == ["cui"]
    assert "compliance" not in canonical(spec_of(comp=""))       # older specs keep their version hash
    with pytest.raises(ValorError):
        spec_of(comp="compliance: {frameworks: [iso-27001]}")
    with pytest.raises(ValorError):
        spec_of(comp="compliance: {frameworks: [hipaa, hipaa]}")
    with pytest.raises(ValorError) as e:
        spec_of(comp="compliance: {frameworks: [hipaa], scope: [nowhere]}")
    assert "unknown segment 'nowhere'" in str(e.value.details)


FAMILIES = {"web": "debian", "files": "debian", "logs": "rhel", "dc": "windows"}


def test_design_checks():
    checks = {d["id"]: d for d in compliance.design_checks(spec_of(), None, FAMILIES)}
    assert checks["deny-by-default"]["pass"] and checks["scope-no-internet"]["pass"]
    assert not checks["central-logging"]["pass"] and "files" in checks["central-logging"]["detail"]
    assert "dc" not in checks["central-logging"]["detail"]                     # Windows hosts are not syslog clients
    tests = [{"expect": "closed", "pass": True}, {"expect": "closed", "pass": False}, {"expect": "open", "pass": False}]
    d = {x["id"]: x for x in compliance.design_checks(spec_of(), tests, FAMILIES)}["deny-by-default"]
    assert not d["pass"] and d["detail"] == "1/2 isolation tests passed"
    leak = spec_of(comp="compliance: {frameworks: [hipaa], scope: [dmz]}")
    assert not {x["id"]: x for x in compliance.design_checks(leak, None, FAMILIES)}["scope-no-internet"]["pass"]


def test_design_warnings_name_the_requirements():
    w = compliance.design_warnings(BASELINES, spec_of(), FAMILIES)
    assert len(w) == 1 and "nist-800-171 3.3.1" in w[0]["message"] and "pci-dss 10.3.3" in w[0]["message"]
    w = compliance.design_warnings(BASELINES, spec_of(comp="compliance: {frameworks: [hipaa]}"), FAMILIES)
    assert any(x["location"] == "compliance.scope" for x in w)
    assert compliance.design_warnings(BASELINES, spec_of(comp=""), FAMILIES) == []


def rows(*controls, fail=()):
    return [{"control": c, "title": c, "theme": "CIS Level 1 - SSH" if c.startswith("ssh") else None,
             "after": "fail" if c in fail else "pass"} for c in controls]


def test_report_statuses_and_coverage():
    s = spec_of(comp="compliance: {frameworks: [nist-800-171, nist-800-53-low, cis-l1], scope: [cui]}")
    base = {"rtr": rows("ssh-root-login"),
            "web": rows("ssh-root-login", "account-lockout", "time-sync"),
            "files": rows("ssh-root-login", "account-lockout", fail=("account-lockout",)),
            "logs": rows("ssh-root-login", "account-lockout", "time-sync")}
    tests = [{"expect": "closed", "pass": True}]
    r = compliance.report(BASELINES, s, base, tests, FAMILIES)
    assert "not an assessment or certification" in r["claim"]
    nist = {q["id"]: q for q in r["frameworks"]["nist-800-171"]["requirements"]}
    assert nist["3.1.8"]["status"] == "fail"                                   # lockout failed on files
    assert nist["3.3.7"]["status"] == "partial" and nist["3.3.7"]["no_evidence_on"] == ["files"]
    assert nist["3.13.6"]["status"] == "pass" and nist["3.13.6"]["evidence"][0]["kind"] == "design"
    assert nist["3.3.1"]["status"] == "fail"                                   # central logging gap
    assert {"id": "3.5.3", "title": nist_title("3.5.3")} in r["frameworks"]["nist-800-171"]["not_covered"]
    ids = [q["id"] for q in r["frameworks"]["nist-800-171"]["requirements"]]
    assert ids == sorted(ids, key=compliance._order) and ids.index("3.1.8") < ids.index("3.13.6")
    low = {q["id"] for q in r["frameworks"]["nist-800-53-low"]["requirements"]}
    assert "AC-7" in low and "IA-2" in low and "AC-6" not in low               # only the Low baseline's ids
    cis = r["frameworks"]["cis-l1"]
    assert [q["id"] for q in cis["requirements"]] == ["CIS Level 1 - SSH"] and cis["not_covered"] == []
    assert compliance.report(BASELINES, spec_of(comp=""), base, tests, FAMILIES) is None


def nist_title(rid):
    return yaml.safe_load((BASELINES / "frameworks" / "frameworks.yaml").read_text())["requirements"]["nist-800-171"][rid]


@pytest.mark.parametrize("baseline, comp, error", [
    ("ubuntu-l1", "compliance: {frameworks: [nist-800-171], scope: [cui]}", "needs baseline linux-moderate"),
    ("linux-moderate", "compliance: {frameworks: [nist-800-171], scope: [cui]}", None),
    ("ubuntu-l1", "compliance: {frameworks: [cis-l1]}", None),
    ("windows-moderate", "", "Windows baseline"),
])
def test_validation_requires_the_framework_profile(cfg, monkeypatch, baseline, comp, error):
    from valor import validate as V
    monkeypatch.setattr(V, "templates", lambda pve, catalog: {k: {"present": True, "vmid": 9000} for k in catalog})
    monkeypatch.setattr(V, "vlans_in_use", lambda pve: {})
    monkeypatch.setattr(V, "range_vms", lambda pve, with_config=True: [])
    v = V.validate_cluster(ValidatePVE(cfg), spec_of(baseline, comp))
    errors = [e["message"] for e in v["errors"]]
    if error:
        assert any(error in e for e in errors), errors
    else:
        assert not errors, errors
    if "nist" in comp:
        assert any("3.3.1" in w["message"] for w in v["warnings"])            # the logging gap is a warning
