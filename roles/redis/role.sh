# Role: redis - idempotent. Params: VALOR_PARAM_PORT, VALOR_PARAM_ALLOW_FROM
PORT="${VALOR_PARAM_PORT:-6379}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }
ALLOW="${VALOR_PARAM_ALLOW_FROM:-}"
for src in $ALLOW; do
  [[ "$src" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "allow_from entry '$src' is not an address/prefix" >&2; exit 1; }
done

pkg_install redis-server nftables
# VALOR's settings come last in redis.conf (later directives win), so package updates keep their own file
write_file /etc/redis/valor.conf 0640 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
bind 127.0.0.1 ${VALOR_ADDRESS}
port ${PORT}
protected-mode no
CONF
chown root:redis /etc/redis/valor.conf
if ! grep "^include /etc/redis/valor.conf" /etc/redis/redis.conf >/dev/null; then
  echo "include /etc/redis/valor.conf" >> /etc/redis/redis.conf; changed
fi

# allow_from: a table of its own in the host's nftables, loaded at boot by a unit (Redis has no address filter)
if [ -n "$ALLOW" ]; then
  write_file /etc/valor/redis-allow.nft 0644 <<NFT || true
# Managed by VALOR: who may reach Redis on ${PORT}
table inet valor_redis
delete table inet valor_redis
table inet valor_redis {
  chain input {
    type filter hook input priority 0; policy accept;
    tcp dport ${PORT} ip saddr { 127.0.0.1, ${VALOR_ADDRESS}, $(echo $ALLOW | sed 's/ /, /g') } accept
    tcp dport ${PORT} drop
  }
}
NFT
  write_file /etc/systemd/system/valor-redis-allow.service 0644 <<'UNIT' || true
[Unit]
Description=VALOR: only the allowed clients reach Redis
Before=redis-server.service
After=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/valor/redis-allow.nft
ExecStop=-/usr/sbin/nft delete table inet valor_redis

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable -q valor-redis-allow
  if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q valor-redis-allow; then systemctl restart valor-redis-allow; fi
elif [ -f /etc/systemd/system/valor-redis-allow.service ]; then
  systemctl disable -q --now valor-redis-allow; rm -f /etc/systemd/system/valor-redis-allow.service /etc/valor/redis-allow.nft
  systemctl daemon-reload; changed
fi

systemctl enable -q redis-server
# a changed configuration needs a restart: `enable --now` would leave a running Redis on its old settings
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q redis-server; then systemctl restart redis-server; fi
wait_port "$PORT" 30
ss -ltn "sport = :${PORT}" | grep "${VALOR_ADDRESS}:${PORT}" >/dev/null || { echo "Redis does not listen on ${VALOR_ADDRESS}:${PORT}" >&2; exit 1; }
[ "$(redis-cli -p "$PORT" ping)" = PONG ]
