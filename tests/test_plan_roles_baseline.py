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


def test_iso_attachment_plans_without_reboot(cfg):
    from valor.plan import _classify, desired_state
    from valor.spec import canonical, normalize, parse_spec
    from valor.cluster import VMState
    base = """
name: isolab
segments: [{name: lan, vlan: 650, cidr: 10.66.0.0/24}]
hosts:
  - {name: box, segment: lan, address: 10.66.0.10}
"""
    plain = normalize(parse_spec(base), "ubuntu-24.04")
    with_iso = normalize(parse_spec(base.replace("address: 10.66.0.10}", "address: 10.66.0.10, iso: tails-7.0.iso}")),
                         "ubuntu-24.04")
    assert '"iso"' not in canonical(plain)                         # older specs keep their hashes
    a, b = desired_state(cfg, plain)[1], desired_state(cfg, with_iso)[1]
    assert "iso" not in a.hw and b.hw["iso"] == "tails-7.0.iso"
    vm = VMState(vmid=5100, name="isolab-box", status="running", tags=[], meta={"hw_spec": a.hw, "conv": a.conv_hash})
    action, reasons = _classify(b, vm)
    assert action == "update" and reasons == ["ISO none -> tails-7.0.iso"]          # no "reboot required"
    with pytest.raises(Exception):
        parse_spec(base.replace("address: 10.66.0.10}", "address: 10.66.0.10, iso: ../../etc/passwd}"))


def test_install_from_iso_plan_and_kickstart(cfg, tmp_path):
    from valor import kickstart
    from valor.plan import desired_state
    from valor.spec import canonical, normalize, parse_spec
    text = """
name: kslab
segments: [{name: lan, vlan: 660, cidr: 10.67.0.0/24}]
hosts:
  - {name: rk, segment: lan, address: 10.67.0.10, os: rocky-10, install: iso}
  - {name: rc, segment: lan, address: 10.67.0.11, os: rocky-10}
"""
    spec = normalize(parse_spec(text), "ubuntu-24.04")
    assert '"install"' in canonical(spec) and canonical(spec).count('"install"') == 1       # default omitted
    rk, rc = desired_state(cfg, spec)[1:]
    assert rk.hw["install"] == "iso" and rk.hw["template"] == "iso" and "install" not in rc.hw
    ks = kickstart.kickstart("rk", "10.67.0.10", 24, "10.67.0.1", ["1.1.1.1", "9.9.9.9"], "valor",
                             'ssh-ed25519 AAAA valor-engine')
    assert "--device=link --bootproto=static --ip=10.67.0.10 --netmask=255.255.255.0 --gateway=10.67.0.1 --nameserver=1.1.1.1,9.9.9.9" in ks
    assert "rootpw --lock" in ks and "qemu-guest-agent" in ks and kickstart.MARKER in ks and "typepermissive" in ks
    ks2 = kickstart.kickstart("rk", "10.67.0.10", 24, "10.67.0.1", ["1.1.1.1"], "valor", "k", device="BC:24:11:00:00:01",
                              repos={"AppStream": "https://dl.rockylinux.org/pub/rocky/10/AppStream/x86_64/os/"})
    assert "--device=BC:24:11:00:00:01" in ks2
    assert "repo --name=AppStream --baseurl=https://dl.rockylinux.org/pub/rocky/10/AppStream/x86_64/os/" in ks2
    assert "password" not in ks.lower().replace("--lock", "")                  # no secrets in the kickstart
    out = tmp_path / "ks.iso"
    kickstart.build_iso(ks, out)
    assert out.stat().st_size > 10_000 and b"OEMDRV" in out.read_bytes()[:40_000]


def test_nested_virtualization_option(cfg):
    from valor.cluster import VMState
    from valor.plan import _classify, desired_state
    from valor.spec import canonical, normalize, parse_spec
    base = """
name: nestlab
segments: [{name: lab, vlan: 670, cidr: 10.0.0.0/24, internet: true}]
hosts:
  - {name: pve, segment: lab, address: 10.0.0.10, os: debian-13}
"""
    plain = normalize(parse_spec(base), "ubuntu-24.04")
    nested = normalize(parse_spec(base.replace("os: debian-13}", "os: debian-13, nested: true}")), "ubuntu-24.04")
    assert '"nested"' not in canonical(plain) and '"nested":true' in canonical(nested)
    a, b = desired_state(cfg, plain)[1], desired_state(cfg, nested)[1]
    assert "nested" not in a.hw and b.hw["nested"] is True
    vm = VMState(vmid=5200, name="nestlab-pve", status="running", tags=[], meta={"hw_spec": a.hw, "conv": a.conv_hash})
    action, reasons = _classify(b, vm)
    assert action == "update" and "reboot required" in reasons


def test_iso9660_writer(tmp_path):
    import shutil
    import subprocess
    from valor import iso9660
    img = iso9660.build({"ks.cfg": b"a" * 5000, "readme.txt": b"hi"}, "OEMDRV")
    assert len(img) % 2048 == 0 and img[32769:32774] == b"CD001" and img[32808:32814] == b"OEMDRV"
    with pytest.raises(ValueError):
        iso9660.build({"much-too-long-name.cfg": b""}, "OEMDRV")
    if shutil.which("isoinfo"):
        p = tmp_path / "x.iso"
        p.write_bytes(img)
        listing = subprocess.run(["isoinfo", "-l", "-i", str(p)], capture_output=True, text=True).stdout
        assert "KS.CFG;1" in listing and "README.TXT;1" in listing
        data = subprocess.run(["isoinfo", "-x", "/KS.CFG;1", "-i", str(p)], capture_output=True).stdout
        assert data == b"a" * 5000
