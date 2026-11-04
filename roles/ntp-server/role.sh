# *keeps time for the whole pack* OwO roles/ntp-server/role.sh: the range's chrony time server. Idempotent ^w^
# Role: ntp-server - idempotent. Params: VALOR_PARAM_ALLOW_FROM, VALOR_PARAM_UPSTREAM
# Who may ask for the time (default every private network), each entry checked UwU
ALLOW="${VALOR_PARAM_ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
for a in $ALLOW; do [[ "$a" =~ ^[0-9.]+(/[0-9]{1,2})?$ ]] || { echo "allow_from entry '$a' is not an address/prefix" >&2; exit 1; }; done
# Where it gets its own time (pool.ntp.org by default) >w<
UPSTREAM="${VALOR_PARAM_UPSTREAM:-pool.ntp.org}"
# Install chrony; on Rocky/Alma also open UDP 123 in firewalld OwO
pkg_install chrony
if [ "$VALOR_FAMILY" = rhel ]; then CONF=/etc/chrony.conf; SERVICE=chronyd; firewall_open 123 udp
else CONF=/etc/chrony/chrony.conf; SERVICE=chrony; fi
# *writes the config* pools as "pool", others as "server"; "local stratum 10" keeps serving its own clock without
# internet; one "allow" line per client network ^w^
write_file "$CONF" 0644 < <(
  echo "# Managed by VALOR (range ${VALOR_RANGE}): time server for the range"
  for u in $UPSTREAM; do
    case "$u" in *pool*) echo "pool $u iburst" ;; *) echo "server $u iburst" ;; esac
  done
  echo "driftfile /var/lib/chrony/drift"
  echo "makestep 1.0 3"
  echo "rtcsync"
  echo "local stratum 10"
  for a in $ALLOW; do echo "allow $a"; done
) || true
# UwU
systemctl enable -q "$SERVICE"
# chrony only opens UDP 123 when it starts with an allow line: restart it too when it runs without serving
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE" || ! ss -lunH "sport = :123" | grep . >/dev/null; then
  systemctl restart "$SERVICE"; changed
fi
# *listens closely* done once UDP 123 is open (up to 15 seconds) >w<
for i in $(seq 1 15); do ss -lunH "sport = :123" | grep . >/dev/null && exit 0; sleep 1; done
echo "chrony does not listen on UDP 123" >&2; exit 1
