# Role: dvwa - idempotent. Params: VALOR_PARAM_PORT
PORT="${VALOR_PARAM_PORT:-80}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }
firewall_open "$PORT"
run_container dvwa docker.io/vulnerables/web-dvwa:latest -p "${PORT}:80"
wait_port "$PORT" 180
