# *checks its tiny watch* OwO roles/ntp-client/role.sh: takes this host's time from the range's time server
# (chrony). Idempotent ^w^
# Role: ntp-client - idempotent. Params: VALOR_PARAM_SERVER
# The server arrives as its address/32; it must be a plain IPv4 address UwU
SERVER="${VALOR_PARAM_SERVER%/32}"
[[ "$SERVER" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "server '$SERVER' is not a host of the range" >&2; exit 1; }
# Install chrony; its config path and service name differ per family >w<
pkg_install chrony
if [ "$VALOR_FAMILY" = rhel ]; then CONF=/etc/chrony.conf; SERVICE=chronyd; else CONF=/etc/chrony/chrony.conf; SERVICE=chrony; fi
# *sets the clock* use only the range server (iburst = fast first sync), step the clock if it's far off OwO
write_file "$CONF" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): time from the range's time server
server ${SERVER} iburst prefer
driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
CONF
# Start at boot; restart when something changed ^w^
systemctl enable -q "$SERVICE"
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then systemctl restart "$SERVICE"; fi
# Done once chrony lists the server as a source UwU
chronyc -n sources | grep -F "${SERVER}" >/dev/null
