# *checks every spec rule* OwO tests/test_spec.py: tests for the RANGE SPEC model! ^w^ *wags tail*
import pytest
import yaml

# SpecError, and the parser/path resolver/hash under test UwU
from valor.errors import SpecError
from valor.spec import parse_spec, resolve_spec_path, spec_hash

# BASE: a minimal valid spec (two segments, one host each) that the tests below break on purpose >w<
BASE = {
    "name": "t1",
    "segments": [{"name": "sega", "vlan": 210, "cidr": "10.210.0.0/24"},
                 {"name": "segb", "vlan": 220, "cidr": "10.220.0.0/24"}],
    "hosts": [{"name": "h1", "segment": "sega", "address": "10.210.0.10"},
              {"name": "h2", "segment": "segb", "address": "10.220.0.10"}],
}


# errors_for(): copy BASE, apply a change, and return the problems the parser reports (it MUST fail) OwO
def errors_for(mutate) -> str:
    data = yaml.safe_load(yaml.safe_dump(BASE))
    mutate(data)
    with pytest.raises(SpecError) as e:
        parse_spec(yaml.safe_dump(data))
    return str(e.value.details)


# The example spec gets the default OS everywhere and the first address as the segment gateway ^w^
def test_reference_spec_defaults(ref_spec):
    assert ref_spec.router.os == "ubuntu-24.04"
    assert all(h.os == "ubuntu-24.04" for h in ref_spec.hosts)
    assert str(ref_spec.segment("dmz").gateway) == "10.110.0.1"


# BASE is valid, with the default baseline and automatic tests on UwU
def test_minimal_spec_is_valid():
    s = parse_spec(yaml.safe_dump(BASE))
    assert s.baseline == "ubuntu-l1" and s.auto_tests


# Each broken spec must report the matching problem: duplicate VLAN, overlap, address outside the segment, the
# router's address, the reserved "rtr" name, unknown segment/endpoint, tcp without ports, a rule inside one segment, a
# bad test target, a public network >w<
@pytest.mark.parametrize("mutate,needle", [
    (lambda spec_data: spec_data["segments"][1].update(vlan=210), "own VLAN"),
    (lambda spec_data: spec_data["segments"][1].update(cidr="10.210.0.128/25"), "overlap"),
    (lambda spec_data: spec_data["hosts"][0].update(address="10.220.0.5"), "not a usable address"),
    (lambda spec_data: spec_data["hosts"][0].update(address="10.210.0.1"), "router's address"),
    (lambda spec_data: spec_data["hosts"][1].update(name="rtr"), "reserved"),
    (lambda spec_data: spec_data["hosts"][1].update(segment="nope"), "unknown segment"),
    (lambda spec_data: spec_data.update(policy=[{"from": "sega", "to": "zz", "proto": "tcp", "ports": [1]}]), "unknown segment or host"),
    (lambda spec_data: spec_data.update(policy=[{"from": "sega", "to": "segb", "proto": "tcp"}]), "need at least one port"),
    (lambda spec_data: spec_data.update(policy=[{"from": "h1", "to": "sega", "proto": "any"}]), "same segment"),
    (lambda spec_data: spec_data.update(tests=[{"from": "h1", "to": "nowhere", "port": 1, "expect": "open"}]), "'to' must be"),
    (lambda spec_data: spec_data["segments"][0].update(cidr="8.8.8.0/24"), "private"),
])
def test_invalid_specs(mutate, needle):
    assert needle in errors_for(mutate)


# Typos (unknown keys) are errors, not silently ignored OwO
def test_unknown_fields_rejected():
    assert "Extra inputs" in errors_for(lambda spec_data: spec_data.update(hostz=[]))


# A broken YAML file never echoes its content in the error (it might contain secrets) ^w^
def test_yaml_errors_never_echo_content():
    secret = 'secret: "tok-1234567890"\n  broken: [\n'
    with pytest.raises(SpecError) as e:
        parse_spec(secret)
    assert "tok-1234567890" not in str(e.value.to_dict())


# The spec hash doesn't depend on key order UwU
def test_hash_ignores_key_order():
    a = parse_spec(yaml.safe_dump(BASE, sort_keys=True))
    b = parse_spec(yaml.safe_dump(BASE, sort_keys=False))
    assert spec_hash(a) == spec_hash(b)


# Spec paths must stay inside the ranges folder (no /etc/passwd, no ../ tricks) >w<
def test_spec_path_must_be_inside_ranges(tmp_path):
    ranges = tmp_path / "ranges"
    ranges.mkdir()
    (ranges / "ok.yaml").write_text("x")
    assert resolve_spec_path("ok.yaml", ranges).name == "ok.yaml"
    for bad in ("/etc/passwd", "../ranges/../x.yaml"):
        with pytest.raises(SpecError):
            resolve_spec_path(bad, ranges)
