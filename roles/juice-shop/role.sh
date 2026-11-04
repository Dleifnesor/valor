# *sips juice* OwO roles/juice-shop/role.sh: runs OWASP Juice Shop in a container. Idempotent ^w^
# Role: juice-shop - idempotent. Params: VALOR_PARAM_PORT
# Published ports need IP forwarding: container_engine keeps it on and marks the host (the baseline exempts it)
# The published port (default 3000), digits only UwU
PORT="${VALOR_PARAM_PORT:-3000}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$PORT'" >&2; exit 1; }
# Open it in firewalld, run the container with the port mapped to its 3000, and wait up to 4 minutes (it starts
# slowly) >w<
firewall_open "$PORT"
run_container juice-shop docker.io/bkimminich/juice-shop:latest -p "${PORT}:3000"
wait_port "$PORT" 240
