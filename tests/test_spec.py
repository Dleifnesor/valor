import pytest
import yaml

from valor.errors import SpecError
from valor.spec import parse_spec, resolve_spec_path, spec_hash

BASE = {
    "name": "t1",
    "segments": [{"name": "sega", "vlan": 210, "cidr": "10.210.0.0/24"},
                 {"name": "segb", "vlan": 220, "cidr": "10.220.0.0/24"}],
    "hosts": [{"name": "h1", "segment": "sega", "address": "10.210.0.10"},
              {"name": "h2", "segment": "segb", "address": "10.220.0.10"}],
}


def errors_for(mutate) -> str:
    data = yaml.safe_load(yaml.safe_dump(BASE))
    mutate(data)
    with pytest.raises(SpecError) as e:
        parse_spec(yaml.safe_dump(data))
    return str(e.value.details)


def test_reference_spec_defaults(ref_spec):
    assert ref_spec.router.os == "ubuntu-24.04"
    assert all(h.os == "ubuntu-24.04" for h in ref_spec.hosts)
    assert str(ref_spec.segment("dmz").gateway) == "10.110.0.1"


def test_minimal_spec_is_valid():
    s = parse_spec(yaml.safe_dump(BASE))
    assert s.baseline == "ubuntu-l1" and s.auto_tests


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["segments"][1].update(vlan=210), "own VLAN"),
    (lambda d: d["segments"][1].update(cidr="10.210.0.128/25"), "overlap"),
    (lambda d: d["hosts"][0].update(address="10.220.0.5"), "not a usable address"),
    (lambda d: d["hosts"][0].update(address="10.210.0.1"), "router's address"),
    (lambda d: d["hosts"][1].update(name="rtr"), "reserved"),
    (lambda d: d["hosts"][1].update(segment="nope"), "unknown segment"),
    (lambda d: d.update(policy=[{"from": "sega", "to": "zz", "proto": "tcp", "ports": [1]}]), "unknown segment or host"),
    (lambda d: d.update(policy=[{"from": "sega", "to": "segb", "proto": "tcp"}]), "need at least one port"),
    (lambda d: d.update(policy=[{"from": "h1", "to": "sega", "proto": "any"}]), "same segment"),
    (lambda d: d.update(tests=[{"from": "h1", "to": "nowhere", "port": 1, "expect": "open"}]), "'to' must be"),
    (lambda d: d["segments"][0].update(cidr="8.8.8.0/24"), "private"),
])
def test_invalid_specs(mutate, needle):
    assert needle in errors_for(mutate)


def test_unknown_fields_rejected():
    assert "Extra inputs" in errors_for(lambda d: d.update(hostz=[]))


def test_yaml_errors_never_echo_content():
    secret = 'secret: "tok-1234567890"\n  broken: [\n'
    with pytest.raises(SpecError) as e:
        parse_spec(secret)
    assert "tok-1234567890" not in str(e.value.to_dict())


def test_hash_ignores_key_order():
    a = parse_spec(yaml.safe_dump(BASE, sort_keys=True))
    b = parse_spec(yaml.safe_dump(BASE, sort_keys=False))
    assert spec_hash(a) == spec_hash(b)


def test_spec_path_must_be_inside_ranges(tmp_path):
    ranges = tmp_path / "ranges"
    ranges.mkdir()
    (ranges / "ok.yaml").write_text("x")
    assert resolve_spec_path("ok.yaml", ranges).name == "ok.yaml"
    for bad in ("/etc/passwd", "../ranges/../x.yaml"):
        with pytest.raises(SpecError):
            resolve_spec_path(bad, ranges)
