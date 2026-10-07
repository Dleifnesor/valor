# Role: apache - idempotent. Params: VALOR_PARAM_PORT
PORT="${VALOR_PARAM_PORT:-80}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }

if [ "$VALOR_FAMILY" = rhel ]; then
  pkg_install -- httpd
  SERVICE=httpd; SITE=/etc/httpd/conf.d/valor.conf; PORTS=/etc/httpd/conf/httpd.conf
  selinux_port http_port_t "$PORT"
  firewall_open "$PORT"
else
  pkg_install apache2
  SERVICE=apache2; SITE=/etc/apache2/sites-available/valor.conf; PORTS=/etc/apache2/ports.conf
fi
# the main "Listen" line (not the indented ones for TLS)
if ! grep -qx "Listen ${PORT}" "$PORTS"; then sed -i -E "s/^Listen [0-9]+$/Listen ${PORT}/" "$PORTS"; changed; fi

write_file "$SITE" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
<VirtualHost *:${PORT}>
    DocumentRoot /var/www/valor
    <Directory /var/www/valor>
        Require all granted
    </Directory>
</VirtualHost>
CONF
write_file /var/www/valor/index.html 0644 <<HTML || true
<!doctype html>
<title>${VALOR_HOST} - ${VALOR_RANGE}</title>
<h1>${VALOR_HOST}</h1>
<p>VALOR range <b>${VALOR_RANGE}</b>, segment ${VALOR_SEGMENT} (${VALOR_ADDRESS}). Served by Apache.</p>
HTML
command -v restorecon >/dev/null 2>&1 && restorecon -R /var/www/valor

if [ "$VALOR_FAMILY" != rhel ]; then
  if [ -e /etc/apache2/sites-enabled/000-default.conf ]; then a2dissite -q 000-default >/dev/null; changed; fi
  if [ ! -e /etc/apache2/sites-enabled/valor.conf ]; then a2ensite -q valor >/dev/null; changed; fi
fi

apachectl -t >/dev/null 2>&1 || { apachectl -t; exit 1; }
systemctl enable -q "$SERVICE"
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then systemctl restart "$SERVICE"; fi
wait_port "$PORT" 30
