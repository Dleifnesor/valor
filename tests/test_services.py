"""Service roles: every role loads and is valid shell, generic roles (packages, windows-features, custom-script),
parameter handling (raw, pattern, max length), ports from parameters, and custom scripts surfacing in the plan."""

import shutil
import subprocess

import pytest

from valor import ai, drafts
from valor.errors import ValorError
from valor.roles import build_script, load_role, role_env, role_ports
from valor.spec import normalize, parse_spec
from valor.web.ranges import custom_scripts, role_list

from conftest import ROOT

ROLES = sorted(p.name for p in (ROOT / "roles").iterdir() if (p / "role.yaml").is_file())

SPEC = """
name: svc
segments:
  - {name: srv, vlan: 700, cidr: 10.70.0.0/24, internet: true}
  - {name: usr, vlan: 701, cidr: 10.71.0.0/24}
hosts:
  - name: web
    segment: srv
    address: 10.70.0.10
    roles:
      - {name: packages, params: {packages: redis-server, services: redis-server, ports: "6379"}}
      - name: custom-script
        params:
          script: |
            write_file /etc/motd 0644 <<'EOF' || true
            hello
            EOF
          ports: "8080"
  - {name: pc, segment: usr, address: 10.71.0.10}
"""


@pytest.mark.parametrize("name", ROLES)
def test_every_role_is_complete(name):
    role = load_role(ROOT / "roles", name)
    meta = role.meta
    assert meta.get("description") and set(role.families) <= {"debian", "rhel", "windows"}
    for pname, pdef in (meta.get("params") or {}).items():
        assert (pdef or {}).get("description"), f"{name}.{pname} has no description"
    if meta.get("ports_param"):
        assert meta["ports_param"] in meta["params"]
    if set(role.families) & {"debian", "rhel"}:
        assert role.script, f"{name} claims Linux but has no role.sh"
        spec = normalize(parse_spec(SPEC), "ubuntu-24.04")
        sample = {"packages": "curl", "image": "redis:7", "script": "echo hi", "server": "web"}
        params = {k: sample[k] for k, v in (meta.get("params") or {}).items() if v.get("required")}
        env = role_env(spec, spec.host("web"), role, params)
        assert subprocess.run(["bash", "-n"], input=build_script(role, env), text=True).returncode == 0
    if "windows" in role.families:
        assert role.ps_script.strip(), f"{name} claims Windows but has no role.ps1"


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_role_scripts_pass_shellcheck():
    for name in ROLES:
        role = load_role(ROOT / "roles", name)
        if role.script:
            r = subprocess.run(["shellcheck", "-S", "error", "-s", "bash", "-"], input=role.script, text=True,
                               capture_output=True)
            assert r.returncode == 0, f"{name}: {r.stdout}"


def test_parameters_raw_pattern_and_length():
    spec = normalize(parse_spec(SPEC), "ubuntu-24.04")
    web = spec.host("web")
    pk = load_role(ROOT / "roles", "packages")
    env = role_env(spec, web, pk, {"packages": "web pc redis-server"})      # host names stay names (raw)
    assert env["VALOR_PARAM_PACKAGES"] == "web pc redis-server"
    assert env["VALOR_RANGE_HOSTS"] == "web=10.70.0.10 pc=10.71.0.10"
    with pytest.raises(ValorError) as e:
        role_env(spec, web, pk, {"packages": "curl; rm -rf /"})
    assert e.value.code == "role_param_invalid"
    cs = load_role(ROOT / "roles", "custom-script")
    with pytest.raises(ValorError) as e:
        role_env(spec, web, cs, {"script": "x" * 20001})
    assert e.value.code == "role_param_too_long"
    client = load_role(ROOT / "roles", "syslog-client")
    assert role_env(spec, spec.host("pc"), client, {"server": "web"})["VALOR_PARAM_SERVER"] == "10.70.0.10/32"


def test_ports_from_parameters():
    roles = ROOT / "roles"
    assert role_ports(load_role(roles, "nginx")) == [80]
    assert role_ports(load_role(roles, "packages"), {"ports": "6379 8080"}) == [6379, 8080]
    assert role_ports(load_role(roles, "container"), {"publish": "8080:80 53:53/udp"}) == [8080, 53]
    assert role_ports(load_role(roles, "juice-shop")) == [3000]
    assert role_ports(load_role(roles, "apache"), {"port": 8081}) == [8081]
    data = drafts.apply_ops(parse_spec(SPEC), [{"op": "add_role", "host": "web", "role": "container",
                                               "params": {"image": "redis:7", "publish": "6379:6379"},
                                               "allow_from": ["usr"]}],
                            lambda r, p: role_ports(load_role(roles, r), p))
    assert data["policy"][-1]["ports"] == [6379] and data["policy"][-1]["from"] == "usr"


def test_custom_scripts_are_listed_for_approval():
    spec = normalize(parse_spec(SPEC), "ubuntu-24.04")
    plan = {"actions": [{"host": "web", "action": "create"}, {"host": "pc", "action": "keep"}]}
    s = custom_scripts(spec, plan)
    assert len(s) == 1 and s[0]["host"] == "web" and "write_file /etc/motd" in s[0]["script"] and s[0]["ports"] == "8080"
    assert custom_scripts(spec, {"actions": [{"host": "web", "action": "keep"}]}) == []     # nothing runs


def test_roles_reach_the_model(cfg):
    roles = role_list(cfg)
    text = ai.reference(cfg, {}, set(), roles, {})
    assert "- packages [debian, rhel] (generic):" in text and "TCP ports from its 'ports' param" in text
    assert "- nginx [debian, rhel]:" in text and "serves: TCP 80" in text
    assert "- custom-script [debian, rhel, windows] (generic)" in text
