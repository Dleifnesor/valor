# *whispers logs to a friend* OwO roles/syslog-client/role.sh: forwards every log line to the range's log server
# over TCP 514, queued on disk while the server is down. Idempotent ^w^
# Role: syslog-client - idempotent. Params: VALOR_PARAM_SERVER
# The server arrives as its address/32 UwU
SERVER="${VALOR_PARAM_SERVER%/32}"
[[ "$SERVER" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "server '$SERVER' is not a host of the range" >&2; exit 1; }
# Install rsyslog and add the forwarding rule (retry forever, queue up to 10000 messages, keep them over a
# shutdown) >w<
pkg_install rsyslog
write_file /etc/rsyslog.d/90-valor-forward.conf 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): everything also goes to the range's log server
*.* action(type="omfwd" target="${SERVER}" port="514" protocol="tcp"
           action.resumeRetryCount="-1" queue.type="LinkedList" queue.size="10000"
           queue.filename="valor_forward" queue.saveOnShutdown="on")
CONF
# Check the config, start at boot, restart when something changed OwO
rsyslogd -N1 >/dev/null 2>&1 || { rsyslogd -N1; exit 1; }
systemctl enable -q rsyslog
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q rsyslog; then systemctl restart rsyslog; fi
# *sends a hello* a test line, so the server shows this host right away ^w^
logger -t valor "log forwarding from ${VALOR_HOST} to ${SERVER} configured"
