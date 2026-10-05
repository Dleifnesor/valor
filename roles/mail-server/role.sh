# Role: mail-server - idempotent. Params: VALOR_PARAM_DOMAIN, VALOR_PARAM_ALLOW_FROM
DOMAIN="${VALOR_PARAM_DOMAIN:-${VALOR_RANGE}.lab}"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || { echo "invalid domain '$DOMAIN'" >&2; exit 1; }
ALLOW="${VALOR_PARAM_ALLOW_FROM:-10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
for a in $ALLOW; do [[ "$a" =~ ^[0-9.]+(/[0-9]{1,2})?$ ]] || { echo "allow_from entry '$a' is not an address/prefix" >&2; exit 1; }; done

if [ "$VALOR_FAMILY" != rhel ]; then
  echo "postfix postfix/main_mailer_type select Internet Site" | debconf-set-selections
  echo "postfix postfix/mailname string ${DOMAIN}" | debconf-set-selections
fi
pkg_install postfix
if systemctl is-active -q firewalld 2>/dev/null && ! firewall-cmd -q --query-service=smtp; then
  firewall-cmd -q --permanent --add-service=smtp; firewall-cmd -q --reload; changed
fi
setting() {                               # setting KEY VALUE: postconf -e only when it differs
  [ "$(postconf -h "$1")" = "$2" ] || { postconf -e "$1 = $2"; changed; }
}
setting myhostname "${VALOR_HOST}.${DOMAIN}"
setting mydomain "${DOMAIN}"
setting myorigin '$mydomain'
setting mydestination "\$myhostname, \$mydomain, localhost.\$mydomain, localhost"
setting inet_interfaces all
setting inet_protocols ipv4
setting mynetworks "127.0.0.0/8 ${ALLOW}"
setting home_mailbox ""
postfix check
systemctl enable -q postfix
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q postfix; then systemctl restart postfix; fi
wait_port 25 30
