# Role: syslog-client - idempotent. Params: VALOR_PARAM_SERVER
SERVER="${VALOR_PARAM_SERVER%/32}"
[[ "$SERVER" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "server '$SERVER' is not a host of the range" >&2; exit 1; }
pkg_install rsyslog
write_file /etc/rsyslog.d/90-valor-forward.conf 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): everything also goes to the range's log server
*.* action(type="omfwd" target="${SERVER}" port="514" protocol="tcp"
           action.resumeRetryCount="-1" queue.type="LinkedList" queue.size="10000"
           queue.filename="valor_forward" queue.saveOnShutdown="on")
CONF
rsyslogd -N1 >/dev/null 2>&1 || { rsyslogd -N1; exit 1; }
systemctl enable -q rsyslog
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q rsyslog; then systemctl restart rsyslog; fi
logger -t valor "log forwarding from ${VALOR_HOST} to ${SERVER} configured"
