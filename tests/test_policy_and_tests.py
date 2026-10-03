from valor.netpolicy import render
from valor.spec import effective_tests

IFMAP = {"dmz": "eth1", "lan": "eth2"}


def test_final_policy(ref_spec):
    r = render(ref_spec, IFMAP, "eth0", build_egress=False, spec_id="x")
    fwd = r.split("chain forward")[1].split("chain output")[0]
    assert "policy drop" in fwd
    assert 'iifname "eth1" oifname "eth2" ip saddr 10.110.0.0/24 ip daddr 10.120.0.0/24 tcp dport { 5432 } accept' in fwd
    assert 'iifname "eth1" oifname "eth0"' in fwd          # dmz has internet
    assert 'iifname "eth2" oifname "eth0"' not in fwd      # lan does not
    assert "ip daddr != @private_v4" in fwd                # egress never reaches private networks
    assert "192.168.0.0/16" in r and 'oifname "eth0" masquerade' in r
    inp = r.split("chain input")[1].split("chain forward")[0]
    assert "policy drop" in inp and "tcp dport" not in inp  # nothing exposed on the router


def test_build_policy_opens_egress_for_all(ref_spec):
    r = render(ref_spec, IFMAP, "eth0", build_egress=True, spec_id="x")
    assert 'iifname "eth2" oifname "eth0"' in r and "temporary build egress" in r


def test_auto_tests_for_reference(ref_spec):
    tests = {(t["from"], t["to"], t["port"], t["expect"]) for t in effective_tests(ref_spec)}
    assert ("web", "db", 5432, "open") in tests
    assert ("web", "db", 22, "closed") in tests
    assert ("db", "web", 80, "closed") in tests
    assert ("db", "web", 22, "closed") in tests
    assert ("web", "internet", 443, "open") in tests
    assert ("db", "internet", 443, "closed") in tests
    assert len(tests) == 6
