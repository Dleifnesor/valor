# *delivers letters* OwO roles/mail-server/role.sh: a Postfix mail server for the range's domain. Idempotent ^w^
# Role: mail-server - idempotent. Params: VALOR_PARAM_DOMAIN, VALOR_PARAM_ALLOW_FROM
# The mail domain (default <range>.lab), checked against a DNS-name pattern UwU
DOMAIN="${VALOR_PARAM_DOMAIN:-${VALOR_RANGE}.lab}"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || { echo "invalid domain '$DOMAIN'" >&2; exit 1; }
# Who may send through it: the spec's allow_from, else every private network (each entry checked) >w<
ALLOW="${VALOR_PARAM_ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
for a in $ALLOW; do [[ "$a" =~ ^[0-9.]+(/[0-9]{1,2})?$ ]] || { echo "allow_from entry '$a' is not an address/prefix" >&2; exit 1; }; done

# *answers the installer's questions ahead of time* Debian asks how Postfix is used; pre-answer it OwO
if [ "$VALOR_FAMILY" != rhel ]; then
  echo "postfix postfix/main_mailer_type select Internet Site" | debconf-set-selections
  echo "postfix postfix/mailname string ${DOMAIN}" | debconf-set-selections
fi
# Install Postfix, and open SMTP in firewalld where it runs ^w^
pkg_install postfix
if systemctl is-active -q firewalld 2>/dev/null && ! firewall-cmd -q --query-service=smtp; then
  firewall-cmd -q --permanent --add-service=smtp; firewall-cmd -q --reload; changed
fi
# setting(): change one Postfix setting only when it differs (and count that as a change) UwU
setting() {                               # setting KEY VALUE: postconf -e only when it differs
  [ "$(postconf -h "$1")" = "$2" ] || { postconf -e "$1 = $2"; changed; }
}
# *fills in the forms* host name, domain, what counts as local mail, all interfaces, IPv4 only, the trusted
# networks, and classic /var/mail mailboxes >w<
setting myhostname "${VALOR_HOST}.${DOMAIN}"
setting mydomain "${DOMAIN}"
setting myorigin '$mydomain'
setting mydestination "\$myhostname, \$mydomain, localhost.\$mydomain, localhost"
setting inet_interfaces all
setting inet_protocols ipv4
setting mynetworks "127.0.0.0/8 ${ALLOW}"
setting home_mailbox ""
# Check the config, start at boot, restart when something changed, and wait for port 25 OwO
postfix check
systemctl enable -q postfix
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q postfix; then systemctl restart postfix; fi
wait_port 25 30
