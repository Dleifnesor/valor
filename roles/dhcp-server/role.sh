# *hands out addresses like treats* OwO roles/dhcp-server/role.sh: a dnsmasq DHCP server (DHCP only, no DNS) for
# this host's segment. Idempotent ^w^
# Role: dhcp-server - idempotent. Params: VALOR_PARAM_RANGE_START, _RANGE_END, _LEASE, _DNS
# The lease time, checked against dnsmasq's format UwU
LEASE="${VALOR_PARAM_LEASE:-12h}"
[[ "$LEASE" =~ ^[0-9]+[smhdw]?$ ]] || { echo "invalid lease time '$LEASE'" >&2; exit 1; }
# *does maths in Python* work out the netmask and the pool: .100 to .199 by default (or the middle of a small
# network), or the spec's range_start/range_end; it fails when the pool isn't inside the segment >w<
POOL=$(python3 - "$VALOR_SEGMENT_CIDR" "${VALOR_PARAM_RANGE_START:-}" "${VALOR_PARAM_RANGE_END:-}" <<'PY'
import ipaddress, sys
net = ipaddress.IPv4Network(sys.argv[1]); hosts = list(net.hosts())
start = ipaddress.IPv4Address(sys.argv[2].removesuffix("/32")) if sys.argv[2] else (net.network_address + 100 if net.num_addresses > 256 or net.prefixlen <= 24 else hosts[len(hosts) // 2])
end = ipaddress.IPv4Address(sys.argv[3].removesuffix("/32")) if sys.argv[3] else min(start + 99, hosts[-1] - 1)
if not (start in net and end in net and start <= end):
    sys.exit(f"the DHCP range {start}-{end} is not inside {net}")
print(net.netmask, start, end)
PY
)
# Split Python's answer into three variables OwO
read -r NETMASK START END <<<"$POOL"
# DNS servers to hand out (the spec's, else VALOR's nameservers), as a comma-separated list of IPv4 addresses ^w^
DNS=""
for d in ${VALOR_PARAM_DNS:-${VALOR_NAMESERVERS:-}}; do
  d=${d%/32}; [[ "$d" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "DNS server '$d' is not an IPv4 address" >&2; exit 1; }
  DNS+="${DNS:+,}$d"
done
# *sniffs the interfaces* find the interface holding this host's address, so dnsmasq only serves that segment UwU
IFACE=$(ip -o -4 addr show | awk -v a="$VALOR_ADDRESS" '{split($4, x, "/"); if (x[1] == a) print $2}' | head -1)
[ -n "$IFACE" ] || { echo "no interface has ${VALOR_ADDRESS}" >&2; exit 1; }

# >w<
# written before the package: Ubuntu starts dnsmasq on install, and without port=0 it would fight over DNS port 53
write_file /etc/dnsmasq.d/valor-dhcp.conf 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): DHCP only (no DNS) on ${VALOR_SEGMENT}
port=0
interface=${IFACE}
bind-interfaces
dhcp-authoritative
dhcp-range=${START},${END},${NETMASK},${LEASE}
dhcp-option=option:router,${VALOR_GATEWAY}
${DNS:+dhcp-option=option:dns-server,${DNS}}
log-dhcp
CONF
# Install dnsmasq, and open DHCP in firewalld where it runs OwO
pkg_install dnsmasq
if systemctl is-active -q firewalld 2>/dev/null && ! firewall-cmd -q --query-service=dhcp; then
  firewall-cmd -q --permanent --add-service=dhcp; firewall-cmd -q --reload; changed
fi
# Check the config before (re)starting ^w^
dnsmasq --test >/dev/null 2>&1 || { dnsmasq --test; exit 1; }
# Start at boot; restart when something changed UwU
systemctl enable -q dnsmasq
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q dnsmasq; then systemctl restart dnsmasq; fi
# *listens closely* give it a second, then make sure something listens on UDP 67 (else show the journal) >w<
sleep 1
ss -lunH "sport = :67" | grep . >/dev/null || { echo "dnsmasq does not listen on UDP 67" >&2; journalctl -u dnsmasq -n 20 --no-pager >&2; exit 1; }
