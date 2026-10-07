# *sniffs the feather logo* OwO roles/apache/role.sh: the apache role's script! VALOR sends it to the guest with
# its shared helpers in front (set -euo pipefail, pkg_install, write_file, firewall_open, wait_port, changed...)
# and runs it as root. It is IDEMPOTENT: running it again on a finished host changes nothing ^w^
# The settings arrive as VALOR_PARAM_* variables; VALOR_HOST/RANGE/SEGMENT/ADDRESS/FAMILY describe the host UwU
# Role: apache - idempotent. Params: VALOR_PARAM_PORT
# The port to listen on (default 80) >w<
PORT="${VALOR_PARAM_PORT:-80}"
# *growls at bad input* refuse anything that isn't 1-5 digits OwO
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }

# Rocky/Alma: the package is httpd, its config lives under /etc/httpd, SELinux must allow the port and firewalld
# must open it ^w^
if [ "$VALOR_FAMILY" = rhel ]; then
  pkg_install -- httpd
  SERVICE=httpd; SITE=/etc/httpd/conf.d/valor.conf; PORTS=/etc/httpd/conf/httpd.conf
  selinux_port http_port_t "$PORT"
  firewall_open "$PORT"
else
  # Debian/Ubuntu: the package is apache2 with its sites-available layout UwU
  pkg_install apache2
  SERVICE=apache2; SITE=/etc/apache2/sites-available/valor.conf; PORTS=/etc/apache2/ports.conf
fi
# *pounces on the Listen line* >w<
# the main "Listen" line (not the indented ones for TLS)
if ! grep -qx "Listen ${PORT}" "$PORTS"; then sed -i -E "s/^Listen [0-9]+$/Listen ${PORT}/" "$PORTS"; changed; fi

# Our site: a VirtualHost on the port serving /var/www/valor. write_file only rewrites it when the content
# differs, and then marks the host changed OwO
write_file "$SITE" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
<VirtualHost *:${PORT}>
    DocumentRoot /var/www/valor
    <Directory /var/www/valor>
        Require all granted
    </Directory>
</VirtualHost>
CONF
# The tiny page naming the host, range, segment and address ^w^
write_file /var/www/valor/index.html 0644 <<HTML || true
<!doctype html>
<title>${VALOR_HOST} - ${VALOR_RANGE}</title>
<h1>${VALOR_HOST}</h1>
<p>VALOR range <b>${VALOR_RANGE}</b>, segment ${VALOR_SEGMENT} (${VALOR_ADDRESS}). Served by Apache.</p>
HTML
# On SELinux systems, give the new files their proper labels UwU
command -v restorecon >/dev/null 2>&1 && restorecon -R /var/www/valor

# Debian: switch off the stock default site and switch ours on (each counts as a change) *wags tail*
if [ "$VALOR_FAMILY" != rhel ]; then
  if [ -e /etc/apache2/sites-enabled/000-default.conf ]; then a2dissite -q 000-default >/dev/null; changed; fi
  if [ ! -e /etc/apache2/sites-enabled/valor.conf ]; then a2ensite -q valor >/dev/null; changed; fi
fi

# Check the config first; a broken config prints apachectl's errors and fails the role >w<
apachectl -t >/dev/null 2>&1 || { apachectl -t; exit 1; }
# Start at boot, and restart only when something changed (or it isn't running) OwO
systemctl enable -q "$SERVICE"
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then systemctl restart "$SERVICE"; fi
# *waits by the door* done once something listens on the port (up to 30 seconds) ^w^
wait_port "$PORT" 30
