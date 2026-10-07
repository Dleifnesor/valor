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


def test_build_egress_is_temporary_and_reverts():
    from valor.netpolicy import REVERT_AFTER, router_script
    final, build = "table inet valor { # FINAL\n}\n", "table inet valor { # BUILD\n}\n"
    s = router_script(final, build)
    saved = s.split("cat > /etc/nftables.conf.valor-new <<'VALOR_NFT_EOF'\n", 1)[1].split("VALOR_NFT_EOF", 1)[0]
    assert "# FINAL" in saved and "# BUILD" not in saved                      # a reboot loads the final policy
    assert "/run/valor-build.nft" in s and f"--on-active={REVERT_AFTER}" in s   # build rules: this boot, then revert
    plain = router_script(final)
    assert "systemctl stop valor-build-revert.timer" in plain and "BUILD" not in plain


def test_verify_looks_again_before_calling_an_open_port_closed(cfg, ref_spec, monkeypatch):
    """A service on a VM the build just (re)started may not listen yet: open tests are retried, closed ones not."""
    from valor import verify as V
    from valor.cluster import VMState
    from valor.spec import ROUTER, spec_hash
    spec = ref_spec.model_copy(update={"baseline": "none"})
    hosts = [ROUTER, *(h.name for h in spec.hosts)]
    vms = [VMState(100 + i, f"{spec.name}-{h}", "running", [], {"host": h, "spec": spec_hash(spec)})
           for i, h in enumerate(hosts)]
    monkeypatch.setattr(V, "range_vms", lambda pve, name: vms)
    monkeypatch.setattr(V, "load_catalog", lambda c: {})
    monkeypatch.setattr(V, "effective_tests", lambda s, probe: [
        {"name": "late service", "from": hosts[1], "to": hosts[2], "proto": "tcp", "port": 80, "expect": "open"},
        {"name": "never", "from": hosts[1], "to": hosts[2], "proto": "tcp", "port": 81, "expect": "open"},
        {"name": "isolated", "from": hosts[1], "to": hosts[2], "proto": "tcp", "port": 22, "expect": "closed"}])
    calls: dict[int, int] = {}

    def probe(pve, vmid, proto, ip, port, family="debian"):
        calls[port] = calls.get(port, 0) + 1
        return ("open", "connected") if port == 80 and calls[port] == 2 else ("closed", "refused")
    monkeypatch.setattr(V, "_probe", probe)
    monkeypatch.setattr(V.time, "sleep", lambda s: None)
    pve = type("P", (), {"cfg": cfg})()
    r = {t["name"]: t for t in V.verify(pve, spec)["tests"]}
    assert r["late service"]["pass"] and calls[80] == 2
    assert not r["never"]["pass"] and calls[81] == 1 + V.OPEN_RETRIES
    assert r["isolated"]["pass"] and calls[22] == 1
