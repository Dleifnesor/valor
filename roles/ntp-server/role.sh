# Role: ntp-server - idempotent. Params: VALOR_PARAM_ALLOW_FROM, VALOR_PARAM_UPSTREAM
ALLOW="${VALOR_PARAM_ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
for a in $ALLOW; do [[ "$a" =~ ^[0-9.]+(/[0-9]{1,2})?$ ]] || { echo "allow_from entry '$a' is not an address/prefix" >&2; exit 1; }; done
UPSTREAM="${VALOR_PARAM_UPSTREAM:-pool.ntp.org}"
pkg_install chrony
if [ "$VALOR_FAMILY" = rhel ]; then CONF=/etc/chrony.conf; SERVICE=chronyd; firewall_open 123 udp
else CONF=/etc/chrony/chrony.conf; SERVICE=chrony; fi
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
systemctl enable -q "$SERVICE"
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then systemctl restart "$SERVICE"; fi
for i in $(seq 1 15); do ss -lunH "sport = :123" | grep . >/dev/null && exit 0; sleep 1; done
echo "chrony does not listen on UDP 123" >&2; exit 1
