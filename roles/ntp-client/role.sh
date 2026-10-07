# Role: ntp-client - idempotent. Params: VALOR_PARAM_SERVER
SERVER="${VALOR_PARAM_SERVER%/32}"
[[ "$SERVER" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "server '$SERVER' is not a host of the range" >&2; exit 1; }
pkg_install chrony
if [ "$VALOR_FAMILY" = rhel ]; then CONF=/etc/chrony.conf; SERVICE=chronyd; else CONF=/etc/chrony/chrony.conf; SERVICE=chrony; fi
write_file "$CONF" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): time from the range's time server
server ${SERVER} iburst prefer
driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
CONF
systemctl enable -q "$SERVICE"
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then systemctl restart "$SERVICE"; fi
chronyc -n sources | grep -F "${SERVER}" >/dev/null
