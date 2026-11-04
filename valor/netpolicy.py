# *puts on a tiny firewall helmet* OwO valor/netpolicy.py writes the range router's FIREWALL! ^w^
# The router is the ONLY path between a range's segments and to the internet, so this file IS the range's security
# boundary. It turns the spec's policy into an nftables ruleset where everything is DENIED unless a rule allows it
# (the chains are explained in the docstring below). It also writes the shell script that installs the ruleset
# safely, with a timer that takes back the temporary build-time internet access *guards the door with fluffy tail*
"""Compile a range's traffic policy into the router's nftables ruleset.

The range router is the only path between segments and to the internet, so this file is the range's security
boundary. Everything is default-deny:

  input     the router itself only answers pings from its segments (and WireGuard, when configured);
            it is managed over the QEMU guest agent, so no service listens on the network.
  forward   only the spec's policy rules, WireGuard peers to the segments they may reach, and internet egress
            for segments with `internet: true` - to public addresses only (never the cluster's own networks).
  nat       everything leaving through the uplink is masqueraded.

During a build every segment gets temporary internet egress (packages, roles); `router_script` makes sure that
can't outlive the build.
"""

# Lazy type hints UwU
from __future__ import annotations

# ipaddress does network math (is this network inside that one? merge overlapping networks) >w<
import ipaddress

# RangeSpec = the parsed spec OwO
from .spec import RangeSpec

# The WireGuard interface's name inside the router ^w^
WG_IFACE = "wg0"

# Destinations that never count as "internet": private ranges, link-local, loopback, multicast and reserved
# space. The cluster's own networks (reserved_networks in the config) are added, so a cluster whose LAN uses
# public addresses is protected too.
# These are written into an nftables SET called private_v4; "internet egress" rules only allow destinations
# NOT in it (see _forward_chain) UwU
PRIVATE_V4 = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
              "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4"]


# private_set: PRIVATE_V4 plus the cluster's own reserved networks, merged without overlaps >w<
def private_set(reserved=()) -> list[str]:
    """PRIVATE_V4 plus reserved networks it does not already cover, without overlaps (nft interval sets reject
    overlapping elements). Unchanged output when nothing new is added keeps existing policy hashes stable."""
    # Turn the strings into network objects OwO
    base = [ipaddress.IPv4Network(net) for net in PRIVATE_V4]
    extra = []
    # Keep only IPv4 reserved networks that aren't already inside a private range ^w^
    for net in reserved:
        network = ipaddress.ip_network(net, strict=False)
        if network.version == 4 and not any(network.subnet_of(known) for known in base):
            extra.append(network)
    # Nothing new? Return the exact original list, so the generated ruleset (and its hash) doesn't change UwU
    if not extra:
        return list(PRIVATE_V4)
    # collapse_addresses merges overlapping/adjacent networks into the fewest possible >w<
    return [str(network) for network in ipaddress.collapse_addresses(base + extra)]


# _ports: a port list in nftables set syntax: [80, 443] -> "{ 80, 443 }" (doubled braces in the f-string are
# literal braces) OwO
def _ports(ports: list) -> str:
    items = ", ".join(str(port) for port in ports)
    return f"{{ {items} }}"


# render: the router's COMPLETE nftables ruleset as text ^w^
#   ifmap         segment name -> its network interface inside the router (eth1, eth2...)
#   uplink        the router's internet-side interface
#   build_egress  True = the temporary BUILD variant: every segment may reach the internet (to install packages)
#   spec_id       the spec's hash, written in the header comment
#   reserved      the cluster's own networks (never reachable from the range)
def render(spec: RangeSpec, ifmap: dict[str, str], uplink: str, *, build_egress: bool, spec_id: str,
           reserved=()) -> str:
    """The router's complete nftables ruleset (see the module docstring).

    ifmap: segment name -> interface name inside the router; uplink: interface name of the NAT uplink;
    build_egress: the temporary BUILD variant (every segment may reach the internet);
    reserved: the cluster's own networks (never reachable from the range).
    """
    # Which variant this is, for the header comment UwU
    mode = "BUILD (temporary internet egress for every segment)" if build_egress else "FINAL policy"
    # The ruleset, line by line >w<:
    #   - a shebang so the file can be run with nft, header comments, and "flush ruleset" (start from nothing)
    #   - table "inet valor" with the private_v4 set, the input chain, the forward chain and an accept-all output
    #     chain (the router itself may talk out)
    #   - table "ip valor_nat": everything leaving through the uplink is masqueraded (NAT behind the router's address)
    lines = [
        "#!/usr/sbin/nft -f",
        f"# Generated by VALOR for range '{spec.name}' (spec {spec_id}). Do not edit: change the spec and re-apply.",
        f"# Mode: {mode}",
        "flush ruleset",
        "",
        "table inet valor {",
        "  set private_v4 {",
        "    type ipv4_addr",
        "    flags interval",
        f"    elements = {{ {', '.join(private_set(reserved))} }}",
        "  }",
        "",
        *_input_chain(spec, ifmap, uplink),
        "",
        *_forward_chain(spec, ifmap, uplink, build_egress),
        "",
        "  chain output {",
        "    type filter hook output priority filter; policy accept;",
        "  }",
        "}",
        "",
        "table ip valor_nat {",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        f'    oifname "{uplink}" masquerade',
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


# _input_chain: traffic TO the router itself OwO
def _input_chain(spec: RangeSpec, ifmap: dict[str, str], uplink: str) -> list[str]:
    """Traffic to the router itself."""
    # The segment interfaces, quoted and comma-separated for an nftables set ^w^
    segment_ifaces = ", ".join(f'"{ifmap[segment.name]}"' for segment in spec.segments)
    # Default drop; allow loopback; allow replies to connections the router started; drop broken packets; and let
    # segments ping their gateway UwU
    lines = [
        "  chain input {",
        "    type filter hook input priority filter; policy drop;",
        '    iif "lo" accept',
        "    ct state established,related accept",
        "    ct state invalid drop",
        f'    iifname {{ {segment_ifaces} }} icmp type echo-request accept comment "segments may ping their gateway"',
    ]
    # With WireGuard: accept the VPN's UDP port on the uplink, and pings from VPN peers >w<
    wireguard = spec.access.wireguard if spec.access else None
    if wireguard:
        lines += [
            f'    iifname "{uplink}" udp dport {wireguard.port} accept comment "WireGuard access (access.wireguard)"',
            f'    iifname "{WG_IFACE}" ip saddr {wireguard.network} icmp type echo-request accept '
            f'comment "peers may ping the router"',
        ]
    # Nothing else listens: VALOR manages the router over the QEMU guest agent, not the network OwO
    lines += [
        "    # No other services are exposed on the router; it is managed through the QEMU guest agent.",
        "  }",
    ]
    return lines


# _forward_chain: traffic THROUGH the router ^w^
def _forward_chain(spec: RangeSpec, ifmap: dict[str, str], uplink: str, build_egress: bool) -> list[str]:
    """Traffic through the router: between segments, from WireGuard peers, and out to the internet."""
    # Default drop; replies to allowed connections pass; broken packets drop UwU
    lines = [
        "  chain forward {",
        "    type filter hook forward priority filter; policy drop;",
        "    ct state established,related accept",
        "    ct state invalid drop",
    ]
    # One nftables rule per spec policy rule >w<
    lines += [_policy_rule(spec, ifmap, rule) for rule in spec.policy]

    # WireGuard peers may reach the segments in `reach` (all segments when it isn't set) OwO
    wireguard = spec.access.wireguard if spec.access else None
    if wireguard:
        for name in wireguard.reach or [segment.name for segment in spec.segments]:
            lines.append(f'    iifname "{WG_IFACE}" oifname "{ifmap[name]}" ip saddr {wireguard.network} '
                         f'ip daddr {spec.segment(name).cidr} accept comment "WireGuard peers -> {name}"')

    # Internet egress for segments that have it (or for all, during a build), but only to PUBLIC addresses ^w^
    for segment in spec.segments:
        if segment.internet or build_egress:
            why = "internet egress" if segment.internet else "temporary build egress"
            # @private_v4 includes the cluster's own networks: "internet" never means the LAN VALOR runs on.
            lines.append(f'    iifname "{ifmap[segment.name]}" oifname "{uplink}" ip saddr {segment.cidr} '
                         f'ip daddr != @private_v4 accept comment "{segment.name}: {why} (public addresses only)"')
    lines.append("  }")
    return lines


# _policy_rule: one spec rule -> one nftables rule UwU
def _policy_rule(spec: RangeSpec, ifmap: dict[str, str], rule) -> str:
    """One spec policy rule as an nftables rule. Matching on both interfaces and addresses means a host can't
    get around the policy by spoofing an address from another segment."""
    # Which interfaces and which addresses (a whole segment's CIDR, or one host's /32) >w<
    from_iface, to_iface = ifmap[spec.segment_of(rule.from_)], ifmap[spec.segment_of(rule.to)]
    src, dst = spec.endpoint_cidr(rule.from_), spec.endpoint_cidr(rule.to)
    # Matching BOTH the interface and the address stops a VM from faking (spoofing) an address of another segment OwO
    match = f'iifname "{from_iface}" oifname "{to_iface}" ip saddr {src} ip daddr {dst}'
    # The rule's label for the nftables comment: its description, or "from -> to proto ports". Double quotes become
    # single quotes so they can't end the comment early ^w^
    ports = " " + ",".join(map(str, rule.ports)) if rule.ports else ""
    label = (rule.description or f"{rule.from_} -> {rule.to} {rule.proto}{ports}").replace('"', "'")
    # TCP/UDP: only the listed destination ports; ICMP: any ICMP; "any": every protocol UwU
    if rule.proto in ("tcp", "udp"):
        return f'    {match} {rule.proto} dport {_ports(rule.ports)} accept comment "policy: {label}"'
    if rule.proto == "icmp":
        return f'    {match} meta l4proto icmp accept comment "policy: {label}"'
    return f'    {match} accept comment "policy: {label}"'


# REVERT_AFTER: the temporary build egress is taken back after at most 3 hours, even if the build dies >w<
REVERT_AFTER = "3h"


# router_script: the bash script that installs the rules inside the router OwO
def router_script(final: str, build: str | None = None) -> str:
    """Bash run inside the router. The FINAL ruleset is always what is saved (and what a reboot loads). With
    `build`, the temporary build-egress ruleset is loaded on top for this boot only, and a timer puts the final
    policy back after REVERT_AFTER - so a build that dies half way (worker killed, VM lost) can't leave egress open."""
    # The common part (an f-string; doubled braces are literal shell braces) ^w^:
    #   - stop on any error; install nftables if it's missing
    #   - write the FINAL ruleset to a new file (a quoted heredoc, so the shell changes nothing in it), CHECK it with
    #     `nft -c` (dry run), and only then move it into place, readable by root only
    #   - turn on IPv4 forwarding (and keep IPv6 forwarding off, and reverse-path filtering on) via sysctl
    #   - load nftables at every boot, and cancel any revert timer left from an earlier build
    head = f"""set -euo pipefail
command -v nft >/dev/null || {{ export DEBIAN_FRONTEND=noninteractive; apt-get -o DPkg::Lock::Timeout=600 -qq -y install nftables >/dev/null; }}
cat > /etc/nftables.conf.valor-new <<'VALOR_NFT_EOF'
{final}VALOR_NFT_EOF
nft -c -f /etc/nftables.conf.valor-new
mv /etc/nftables.conf.valor-new /etc/nftables.conf
chmod 600 /etc/nftables.conf
cat > /etc/sysctl.d/90-valor-router.conf <<'EOF'
# VALOR range router: forward IPv4 between segments (filtered by nftables), no IPv6 forwarding
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 0
net.ipv4.conf.all.rp_filter = 1
EOF
sysctl -q --system >/dev/null
systemctl enable -q nftables
systemctl stop valor-build-revert.timer valor-build-revert.service 2>/dev/null || true
systemctl reset-failed valor-build-revert.timer valor-build-revert.service 2>/dev/null || true
"""
    # No build variant: remove any leftover build ruleset and load the final one UwU
    if build is None:
        return head + "rm -f /run/valor-build.nft\nnft -f /etc/nftables.conf\necho ok\n"
    # Build variant: write the build ruleset to /run (a tmpfs, so a reboot forgets it), check it, load it, and start
    # a one-shot systemd timer that loads the FINAL rules again after REVERT_AFTER >w< *sets a tiny alarm clock*
    return head + f"""cat > /run/valor-build.nft <<'VALOR_NFT_EOF'
{build}VALOR_NFT_EOF
chmod 600 /run/valor-build.nft
nft -c -f /run/valor-build.nft
nft -f /run/valor-build.nft
systemd-run --quiet --unit=valor-build-revert --on-active={REVERT_AFTER} /usr/sbin/nft -f /etc/nftables.conf
echo ok
"""
