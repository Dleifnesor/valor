import copy
import subprocess
from pathlib import Path

import pytest

from valor.baseline import bundle, load_baseline, parse_results
from valor.cluster import VMState, parse_meta, render_description
from valor.errors import ValorError
from valor.plan import _classify, desired_state
from valor.roles import build_script, load_role, role_env

ROOT = Path(__file__).resolve().parent.parent


def vm_for(d, conv=True, status="running", spec="S"):
    meta = {"host": d.host, "hw_spec": copy.deepcopy(d.hw), "hw": d.hw_hash, "conv": d.conv_hash if conv else None,
            "spec": spec}
    return VMState(4000, f"x-{d.host}", status, ["valor"], meta)


def test_desired_state(cfg, ref_spec):
    ds = desired_state(cfg, ref_spec)
    assert [d.host for d in ds] == ["rtr", "web", "db"]
    rtr = ds[0]
    assert rtr.is_router and rtr.nics[0]["ip"] == "dhcp" and [n["vlan"] for n in rtr.nics[1:]] == [110, 120]
    assert ds[2].nics[0] == {"bridge": cfg.segment_bridge, "vlan": 120, "ip": "10.120.0.10/24", "gw": "10.120.0.1"}
    assert desired_state(cfg, ref_spec)[1].conv_hash == ds[1].conv_hash    # deterministic


def test_classify(cfg, ref_spec):
    d = desired_state(cfg, ref_spec)[1]
    assert _classify(d, None)[0] == "create"
    assert _classify(d, vm_for(d), "S")[0] == "keep"
    assert _classify(d, vm_for(d), "OTHER")[0] == "restamp"
    assert _classify(d, vm_for(d, conv=False), "S")[0] == "converge"
    assert _classify(d, vm_for(d, status="stopped"), "S")[0] == "start"
    v = vm_for(d)
    v.meta["hw_spec"]["memory"] = 512
    assert _classify(d, v, "S")[0] == "update"
    v = vm_for(d)
    v.meta["hw_spec"]["nics"][0]["vlan"] = 999
    assert _classify(d, v, "S")[0] == "replace"
    v = vm_for(d)
    v.meta["hw_spec"]["disk"] = 50
    assert _classify(d, v, "S")[0] == "replace"            # disks never shrink


def test_metadata_roundtrip():
    meta = {"range": "r", "host": "web", "conv": "abc", "hw_spec": {"nics": []}}
    assert parse_meta(render_description(meta, "123")) == meta


def test_role_env_and_script(ref_spec):
    role = load_role(ROOT / "roles", "postgresql")
    db = ref_spec.host("db")
    env = role_env(ref_spec, db, role, {"allow_from": ["web", "dmz"]})
    assert env["VALOR_PARAM_ALLOW_FROM"] == "10.110.0.10/32 10.110.0.0/24"
    assert env["VALOR_PARAM_DATABASE"] == "app"
    with pytest.raises(ValorError):
        role_env(ref_spec, db, role, {"nonsense": 1})
    script = build_script(role, env)
    assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0


def test_baseline_bundle():
    b = load_baseline(ROOT / "baselines", "ubuntu-l1")
    assert len(b.controls) == 10
    host, router = bundle(b, False, fix=True), bundle(b, True, fix=True)
    assert "check_ip_forwarding_disabled" in host and "check_ip_forwarding_disabled" not in router
    assert "pipefail" not in host.split("\n")[0]
    assert "check_ssh_root_login() (" in host                 # subshell bodies contain `exit`
    for s in (host, router, bundle(b, False, fix=False)):
        assert subprocess.run(["bash", "-n"], input=s, text=True).returncode == 0
    rows = parse_results(b, "VALOR-CONTROL ssh-root-login fail pass\nnoise\nVALOR-CONTROL tmp-sticky-bit pass pass\n")
    assert [(r["control"], r["fixed"]) for r in rows] == [("ssh-root-login", True), ("tmp-sticky-bit", False)]
