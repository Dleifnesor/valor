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
