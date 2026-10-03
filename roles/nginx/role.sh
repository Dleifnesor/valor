# Role: nginx - idempotent. Params: VALOR_PARAM_PORT, VALOR_PARAM_SERVER_NAME
PORT="${VALOR_PARAM_PORT:-80}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }

apt_install nginx

write_file /etc/nginx/sites-available/valor 0644 <<EOF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
server {
    listen ${PORT} default_server;
    server_name ${VALOR_PARAM_SERVER_NAME:-_};
    root /var/www/valor;
    index index.html;
    server_tokens off;
    location / { try_files \$uri \$uri/ =404; }
}
EOF

write_file /var/www/valor/index.html 0644 <<EOF || true
<!doctype html>
<title>${VALOR_HOST} - ${VALOR_RANGE}</title>
<h1>${VALOR_HOST}</h1>
<p>VALOR range <b>${VALOR_RANGE}</b>, segment ${VALOR_SEGMENT} (${VALOR_ADDRESS}).</p>
EOF

if [ -e /etc/nginx/sites-enabled/default ]; then rm -f /etc/nginx/sites-enabled/default; changed; fi
if [ ! -L /etc/nginx/sites-enabled/valor ]; then ln -sf /etc/nginx/sites-available/valor /etc/nginx/sites-enabled/valor; changed; fi

nginx -t -q
systemctl enable -q nginx
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q nginx; then systemctl reload-or-restart nginx; fi
systemctl is-active -q nginx
ss -ltn "sport = :${PORT}" | grep LISTEN >/dev/null
