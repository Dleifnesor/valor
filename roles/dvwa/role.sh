# *waves a "vulnerable on purpose!" flag* OwO roles/dvwa/role.sh: runs DVWA in a container. Idempotent ^w^
# Role: dvwa - idempotent. Params: VALOR_PARAM_PORT
# Published ports need IP forwarding: container_engine keeps it on and marks the host (the baseline exempts it)
# The published port (default 80), digits only UwU
PORT="${VALOR_PARAM_PORT:-80}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }
# Open it in firewalld (Rocky/Alma), run the container with the port mapped to its 80, and wait up to 3
# minutes for it >w<
firewall_open "$PORT"
run_container dvwa docker.io/vulnerables/web-dvwa:latest -p "${PORT}:80"
wait_port "$PORT" 180
