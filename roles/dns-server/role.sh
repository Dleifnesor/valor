# *answers every "who is that?"* OwO roles/dns-server/role.sh: a BIND DNS server with a zone naming every range
# host, answering and forwarding for range clients. Idempotent ^w^
# Role: dns-server - idempotent. Params: VALOR_PARAM_ZONE, VALOR_PARAM_ALLOW_FROM, VALOR_PARAM_FORWARDERS
# The zone (default <range>.lab), checked against a DNS-name pattern UwU
ZONE="${VALOR_PARAM_ZONE:-${VALOR_RANGE}.lab}"
[[ "$ZONE" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || { echo "invalid zone '$ZONE'" >&2; exit 1; }
# Who may query: the spec's allow_from, else every private network >w<
ALLOW="${VALOR_PARAM_ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
# *collects forwarders* the upstream resolvers (the spec's, else VALOR's nameservers), each an IPv4 address,
# built into "a; b; " for BIND OwO
FWD=""
for f in ${VALOR_PARAM_FORWARDERS:-${VALOR_NAMESERVERS:-}}; do
  f=${f%/32}
  [[ "$f" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "forwarder '$f' is not an IPv4 address" >&2; exit 1; }
  FWD+="$f; "
done
# Every allow_from entry must look like an address or prefix ^w^
for a in $ALLOW; do [[ "$a" =~ ^[0-9.]+(/[0-9]{1,2})?$ ]] || { echo "allow_from entry '$a' is not an address/prefix" >&2; exit 1; }; done

# Rocky/Alma: package bind, one named.conf for everything, zone files in /var/named, opened in firewalld UwU
if [ "$VALOR_FAMILY" = rhel ]; then
  pkg_install -- bind
  OPTIONS=/etc/named.conf; LOCAL=/etc/named.conf; DIR=/var/named; ZFILE="/var/named/db.${ZONE}"; OWNER=named
  firewall_open 53; firewall_open 53 udp
else
  # Debian/Ubuntu: package bind9 with split options/local files and a writable zone directory >w<
  pkg_install bind9
  OPTIONS=/etc/bind/named.conf.options; LOCAL=/etc/bind/named.conf.local; DIR=/var/cache/bind
  ZFILE="/var/lib/bind/db.${ZONE}"; OWNER=bind
fi

# *writes the rules* the options block: an ACL of clients, listen on IPv4 53, answer and recurse only for those
# clients, forward everything else (when there are forwarders), no DNSSEC validation (lab networks!) OwO
OPTS=$(cat <<CONF
acl valor_clients { 127.0.0.1; $(for a in $ALLOW; do printf '%s; ' "$a"; done)};
options {
    directory "${DIR}";
    listen-on port 53 { any; };
    listen-on-v6 { none; };
    allow-query { valor_clients; };
    recursion yes;
    allow-recursion { valor_clients; };
    $( [ -n "$FWD" ] && echo "forwarders { ${FWD}}; forward only;" )
    dnssec-validation no;
};
CONF
)
# The zone declaration: this host is the master for the zone ^w^
ZONECONF="zone \"${ZONE}\" { type master; file \"${ZFILE}\"; };"
# Rocky/Alma: options and zone go into one file; Debian: into their two files UwU
if [ "$VALOR_FAMILY" = rhel ]; then
  write_file /etc/named.conf 0640 < <(printf '// Managed by VALOR (range %s)\n%s\n%s\n' "$VALOR_RANGE" "$OPTS" "$ZONECONF") || true
  chgrp named /etc/named.conf
else
  write_file "$OPTIONS" 0644 < <(printf '// Managed by VALOR (range %s)\n%s\n' "$VALOR_RANGE" "$OPTS") || true
  write_file "$LOCAL" 0644 < <(printf '// Managed by VALOR (range %s)\n%s\n' "$VALOR_RANGE" "$ZONECONF") || true
fi

# *names everyone* the zone file: SOA and NS records, "ns" for this host, and one A record per range host
# (VALOR_RANGE_HOSTS is "name=address" pairs) >w<
write_file "$ZFILE" 0644 < <(
  echo "; Managed by VALOR (range ${VALOR_RANGE})"
  echo "\$TTL 300"
  echo "@ IN SOA ns.${ZONE}. hostmaster.${ZONE}. ( 1 3600 600 86400 300 )"
  echo "@ IN NS ns.${ZONE}."
  echo "ns IN A ${VALOR_ADDRESS}"
  for hv in $VALOR_RANGE_HOSTS; do echo "${hv%%=*} IN A ${hv#*=}"; done
) || true
# The zone file belongs to BIND's user (and gets its SELinux label) OwO
chown "$OWNER" "$ZFILE"
command -v restorecon >/dev/null 2>&1 && restorecon "$ZFILE"

# Check the config and the zone before (re)starting ^w^
named-checkconf
named-checkzone -q "$ZONE" "$ZFILE"
# Start at boot; restart when something changed UwU
systemctl enable -q named
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q named; then systemctl restart named; fi
# *asks itself a question* done once BIND answers this host's own name with its address (installing dig if
# needed), retrying for about 30 seconds >w<
wait_port 53 30
for i in $(seq 1 15); do
  [ "$(dig +short @127.0.0.1 "${VALOR_HOST}.${ZONE}" A 2>/dev/null)" = "$VALOR_ADDRESS" ] && exit 0
  command -v dig >/dev/null || pkg_install dnsutils -- bind-utils
  sleep 2
done
echo "the zone ${ZONE} does not answer for ${VALOR_HOST}" >&2; exit 1
