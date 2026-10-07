# *collects everyone's diaries* OwO roles/syslog-server/role.sh: the central rsyslog server, storing each host's
# logs under /var/log/remote/<host>/. Idempotent ^w^
# Role: syslog-server - idempotent. Params: VALOR_PARAM_ALLOW_FROM
# Allowed senders (default every private network), checked, then joined for rsyslog UwU
ALLOW="${VALOR_PARAM_ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
for a in $ALLOW; do [[ "$a" =~ ^[0-9.]+(/[0-9]{1,2})?$ ]] || { echo "allow_from entry '$a' is not an address/prefix" >&2; exit 1; }; done
SENDERS="127.0.0.1$(for a in $ALLOW; do printf ', %s' "$a"; done)"

# Install rsyslog >w<
pkg_install rsyslog
# Rocky/Alma: root owns the logs, SELinux allows TCP 514 for syslog, firewalld opens 514 TCP and UDP OwO
if [ "$VALOR_FAMILY" = rhel ]; then
  OWNER=root:root
  selinux_port syslogd_port_t 514          # TCP 514 is the rsh port in the stock SELinux policy
  firewall_open 514; firewall_open 514 udp
else
  OWNER=syslog:adm
fi
# The folder for remote logs ^w^
install -d -m 0750 -o "${OWNER%:*}" -g "${OWNER#*:}" /var/log/remote

# *sets up the mailroom* listen on TCP and UDP 514 for the allowed senders and file each message by host and
# program UwU
write_file /etc/rsyslog.d/10-valor-server.conf 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): receive logs from range hosts, one directory per host
module(load="imtcp")
module(load="imudp")
\$AllowedSender TCP, ${SENDERS}
\$AllowedSender UDP, ${SENDERS}
template(name="ValorRemote" type="string" string="/var/log/remote/%HOSTNAME%/%PROGRAMNAME%.log")
ruleset(name="valor_remote") {
  action(type="omfile" dynaFile="ValorRemote" createDirs="on" dirCreateMode="0750" fileCreateMode="0640")
}
input(type="imtcp" port="514" ruleset="valor_remote")
input(type="imudp" port="514" ruleset="valor_remote")
CONF
# Check the config, start at boot, restart when something changed, and wait for TCP 514 >w<
rsyslogd -N1 >/dev/null 2>&1 || { rsyslogd -N1; exit 1; }
systemctl enable -q rsyslog
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q rsyslog; then systemctl restart rsyslog; fi
wait_port 514 30
