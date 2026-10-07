# Role: juice-shop - idempotent. Params: VALOR_PARAM_PORT
# Published ports need IP forwarding: container_engine keeps it on and marks the host (the baseline exempts it)
PORT="${VALOR_PARAM_PORT:-3000}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }
firewall_open "$PORT"
run_container juice-shop docker.io/bkimminich/juice-shop:latest -p "${PORT}:3000"
wait_port "$PORT" 240
