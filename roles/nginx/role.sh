# *sniffs the green logo* OwO roles/nginx/role.sh: nginx with a tiny page, or a reverse proxy with proxy_to.
# Idempotent ^w^
# Role: nginx - idempotent. Params: VALOR_PARAM_PORT, VALOR_PARAM_SERVER_NAME
# The listen port, digits only UwU
PORT="${VALOR_PARAM_PORT:-80}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }
# proxy_to (a host name arrives as its address/32) and the port to forward to >w<
UPSTREAM="${VALOR_PARAM_PROXY_TO:-}"; UPSTREAM="${UPSTREAM%/32}"      # a host name arrives as its address/32
UPORT="${VALOR_PARAM_PROXY_PORT:-80}"
# *picks a job* proxying: forward every request with the usual X-Forwarded headers; otherwise serve files OwO
if [ -n "$UPSTREAM" ]; then
  [[ "$UPSTREAM" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "proxy_to '$UPSTREAM' is not a host of the range or an IPv4 address" >&2; exit 1; }
  [[ "$UPORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid proxy_port '$UPORT'" >&2; exit 1; }
  LOCATION="location / { proxy_pass http://${UPSTREAM}:${UPORT}; proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto \$scheme; }"
else
  LOCATION='location / { try_files $uri $uri/ =404; }'
fi

# Install nginx ^w^
pkg_install nginx
# Rocky/Alma: UwU
if [ "$VALOR_FAMILY" = rhel ]; then
  # The stock main config ships its own default server on port 80; VALOR manages a minimal one instead.
  write_file /etc/nginx/nginx.conf 0644 <<'CONF' || true
# Managed by VALOR
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;
include /usr/share/nginx/modules/*.conf;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    server_tokens off;
    access_log /var/log/nginx/access.log;
    include /etc/nginx/conf.d/*.conf;
}
CONF
  # our site file in conf.d, SELinux allowing the port, and firewalld opening it >w<
  SITE=/etc/nginx/conf.d/valor.conf
  selinux_port http_port_t "$PORT"
  firewall_open "$PORT"
# Debian/Ubuntu: the sites-available layout OwO
else
  SITE=/etc/nginx/sites-available/valor
fi

# *writes the site* a default server on the port, with the page root and either the proxy or the file rule ^w^
write_file "$SITE" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
server {
    listen ${PORT} default_server;
    server_name ${VALOR_PARAM_SERVER_NAME:-_};
    root /var/www/valor;
    index index.html;
    server_tokens off;
    ${LOCATION}
}
CONF

# The tiny page naming the host, range, segment and address UwU
write_file /var/www/valor/index.html 0644 <<HTML || true
<!doctype html>
<title>${VALOR_HOST} - ${VALOR_RANGE}</title>
<h1>${VALOR_HOST}</h1>
<p>VALOR range <b>${VALOR_RANGE}</b>, segment ${VALOR_SEGMENT} (${VALOR_ADDRESS}).</p>
HTML
# Proper SELinux labels for the page >w<
command -v restorecon >/dev/null 2>&1 && restorecon -R /var/www/valor
# a reverse proxy opens connections: SELinux (Rocky/Alma) allows that only with httpd_can_network_connect
if [ -n "$UPSTREAM" ] && command -v getsebool >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null)" = Enforcing ] \
   && ! getsebool httpd_can_network_connect | grep " on$" >/dev/null; then
  setsebool -P httpd_can_network_connect 1; changed
fi

# Debian: remove the stock default site and enable ours (each counts as a change) OwO
if [ "$VALOR_FAMILY" != rhel ]; then
  if [ -e /etc/nginx/sites-enabled/default ]; then rm -f /etc/nginx/sites-enabled/default; changed; fi
  if [ ! -L /etc/nginx/sites-enabled/valor ]; then ln -sf /etc/nginx/sites-available/valor /etc/nginx/sites-enabled/valor; changed; fi
fi

# Check the config, start at boot, reload or restart when something changed ^w^
nginx -t -q
systemctl enable -q nginx
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q nginx; then systemctl reload-or-restart nginx; fi
# *double-checks* nginx is running and listening on the port UwU
systemctl is-active -q nginx
ss -ltn "sport = :${PORT}" | grep LISTEN >/dev/null
