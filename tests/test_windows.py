"""Windows hosts: planning, dependency order, scripts (no cluster needed)."""

import pytest

from valor import windows
from valor.apply import converge_waves
from valor.errors import ValorError
from valor.plan import desired_state
from valor.roles import load_role
from valor.spec import normalize, parse_spec

SPEC = """
apiVersion: valor/v1
name: corp
segments:
  - {name: srv, vlan: 610, cidr: 10.61.0.0/24, internet: true}
  - {name: users, vlan: 620, cidr: 10.62.0.0/24}
hosts:
  - name: dc1
    segment: srv
    address: 10.61.0.10
    os: windows-server-2022-core
    roles: [{name: ad-dc, params: {domain: corp.lab}}]
  - name: dc2
    segment: srv
    address: 10.61.0.11
    os: windows-server-2022-core
    roles: [{name: ad-dc-replica, params: {domain: corp.lab, dc: dc1}}]
  - name: pc1
    segment: users
    address: 10.62.0.10
    os: windows-11
    roles: [{name: ad-member, params: {domain: corp.lab, dc: dc1, dc2: dc2}}]
  - name: web
    segment: srv
    address: 10.61.0.20
policy:
  - {from: users, to: srv, proto: any}
"""


@pytest.fixture
def corp():
    return normalize(parse_spec(SPEC), "ubuntu-24.04")


def test_windows_desired_state(cfg, corp):
    ds = {d.host: d for d in desired_state(cfg, corp)}
    dc1, pc1, web = ds["dc1"], ds["pc1"], ds["web"]
    assert dc1.family == "windows" and dc1.nics[0]["model"] == "e1000e" and dc1.hw["family"] == "windows"
    assert dc1.disk == 64 and dc1.memory == 2048 and pc1.memory == 4096          # catalog floors
    assert pc1.cores == 2 and pc1.hw["cores"] == 2 and dc1.cores == 1            # Windows 11 needs two cores
    assert web.family == "debian" and "model" not in web.nics[0] and "family" not in web.hw   # Linux hashes unchanged
    assert ds["rtr"].family == "debian"


def test_dependency_waves(cfg, corp):
    ds = desired_state(cfg, corp)
    waves = [[d.host for d in w] for w in converge_waves(corp, ds[1:], cfg)]
    assert waves[0] == ["dc1", "web"] and waves[1] == ["dc2"] and waves[2] == ["pc1"]
    # a host that is already converged is not waited for
    assert [[d.host for d in w] for w in converge_waves(corp, [x for x in ds[1:] if x.host != "dc1"], cfg)][0] == ["dc2", "web"]


def test_dependency_cycle(cfg):
    spec = normalize(parse_spec(SPEC.replace("ad-dc, params: {domain: corp.lab}",
                                             "ad-member, params: {domain: corp.lab, dc: pc1}")), "ubuntu-24.04")
    ds = desired_state(cfg, spec)
    with pytest.raises(ValorError) as e:
        converge_waves(spec, ds[1:], cfg)
    assert e.value.code == "dependency_cycle"


def test_scripts_quote_and_wrap():
    s = windows.prep_script("dc1", "10.61.0.10", 24, "10.61.0.1", ["1.1.1.1"], "it's-a-p4ss")
    assert "'it''s-a-p4ss'" in s and "Rename-Computer" in s and windows.PREP_OK in s
    r = windows.role_script("x", "Changed", {"VALOR_PARAM_DOMAIN": "corp.lab", "VALOR_HOST": "o'neil"})
    assert "$VALOR_PARAM_DOMAIN = 'corp.lab'" in r and "$VALOR_HOST = 'o''neil'" in r and "VALOR-ROLE-CHANGED" in r
    assert "TcpClient" in windows.probe_script("tcp", "10.0.0.1", 389)


def test_windows_roles_and_baseline(cfg):
    from valor.baseline import baseline_for
    role = load_role(cfg.roles_dir, "ad-dc")
    assert role.families == ["windows"] and role.ps_script and not role.script
    assert load_role(cfg.roles_dir, "nginx").families == ["debian", "rhel"]
    assert baseline_for(cfg.baselines_dir, "ubuntu-l1", "windows").id == "windows-l1"
    assert baseline_for(cfg.baselines_dir, "ubuntu-l1", "rhel").id == "ubuntu-l1"
    assert baseline_for(cfg.baselines_dir, "none", "windows") is None
    b = baseline_for(cfg.baselines_dir, "ubuntu-l1", "windows")
    assert windows.baseline_bundle(b, False, fix=True).count("VALOR-CONTROL") == len(b.controls)


def test_generated_passwords_meet_windows_complexity():
    from valor.credentials import generate
    for _ in range(200):
        pw = generate()
        assert any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw) and "-" in pw and len(pw) >= 14


def test_login_accounts_per_os(cfg, corp):
    from valor.cluster import load_catalog
    from valor.credentials import accounts
    a = {x["username"]: x for x in accounts(corp, load_catalog(cfg), "valor")}
    assert a["valor"]["hosts"] == ["rtr", "web"]
    assert a["Administrator"]["hosts"] == ["pc1"]                       # DCs have no local accounts
    assert a["CORP\\Administrator"]["hosts"] == ["dc1", "dc2", "pc1"] and a["CORP\\Administrator"]["domain"] == "corp.lab"


def test_catalog_lists_windows_roles(cfg):
    from valor.web.ranges import role_list
    roles = {r["name"]: r for r in role_list(cfg)}
    assert roles["ad-dc"]["families"] == ["windows"] and roles["iis"]["families"] == ["windows"]
    assert roles["nginx"]["families"] == ["debian", "rhel"] and "domain" in roles["ad-member"]["params"]


class ValidatePVE:
    """Just enough cluster for validate_cluster: a VLAN-aware bridge, memory and storage."""

    def __init__(self, cfg):
        self.cfg = cfg

    def network(self):
        return [{"iface": b, "type": "bridge", "bridge_vlan_aware": 1}
                for b in (self.cfg.segment_bridge, self.cfg.uplink_bridge)]

    def node_status(self):
        return {"memory": {"free": 64 * 2**30}, "cpuinfo": {"flags": "vmx"}}

    def storage_status(self):
        return {"avail": 100 * 2**30}


@pytest.mark.parametrize("baseline, error, warning", [
    ("ubuntu-l1", None, None),
    ("windows-l1", "is the Windows baseline", None),       # a model once "fixed" a spec by turning hardening off
    ("none", None, "no hardening"),
])
def test_range_baseline_names_the_linux_baseline(cfg, monkeypatch, baseline, error, warning):
    from valor import validate as V
    from valor.spec import normalize, parse_spec
    monkeypatch.setattr(V, "templates", lambda pve, catalog: {k: {"present": True, "vmid": 9000} for k in catalog})
    monkeypatch.setattr(V, "vlans_in_use", lambda pve: {})
    monkeypatch.setattr(V, "range_vms", lambda pve, with_config=True: [])
    spec = normalize(parse_spec(f"""
name: adlab
baseline: {baseline}
segments:
  - {{name: lan, vlan: 700, cidr: 10.70.0.0/24}}
hosts:
  - {{name: dc, segment: lan, address: 10.70.0.10, os: windows-server-2022}}
  - {{name: web, segment: lan, address: 10.70.0.20}}
"""), "ubuntu-24.04")
    v = V.validate_cluster(ValidatePVE(cfg), spec)
    errors = [e for e in v["errors"] if e["location"] == "baseline"]
    if error:
        assert len(errors) == 1 and error in errors[0]["message"]                       # no follow-on errors
        assert "windows-l1 automatically" in errors[0]["hint"]
    else:
        assert not errors
    warned = [w["message"] for w in v["warnings"] if w["location"] == "baseline"]
    assert (warning in warned[0]) if warning else not warned
    assert v["ok"] is (error is None), v["errors"]
