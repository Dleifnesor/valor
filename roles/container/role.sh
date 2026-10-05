# Role: container - idempotent. Params: VALOR_PARAM_IMAGE, _NAME, _PUBLISH, _ENV
IMAGE="${VALOR_PARAM_IMAGE:-}"; NAME="${VALOR_PARAM_NAME:-app}"
[[ "$IMAGE" =~ ^[a-z0-9][a-z0-9._/:@-]*$ ]] || { echo "invalid image '$IMAGE'" >&2; exit 1; }
[[ "$NAME" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || { echo "invalid container name '$NAME'" >&2; exit 1; }
args=()
for p in ${VALOR_PARAM_PUBLISH:-}; do
  [[ "$p" =~ ^([0-9]{1,5}):[0-9]{1,5}(/(tcp|udp))?$ ]] || { echo "invalid publish '$p' (host:container)" >&2; exit 1; }
  args+=(-p "$p")
  case "$p" in */udp) firewall_open "${p%%:*}" udp ;; *) firewall_open "${p%%:*}" ;; esac
done
for e in ${VALOR_PARAM_ENV:-}; do
  [[ "$e" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || { echo "invalid env entry '$e' (KEY=VALUE)" >&2; exit 1; }
  args+=(-e "$e")
done
run_container "$NAME" "$IMAGE" "${args[@]}"
for p in ${VALOR_PARAM_PUBLISH:-}; do case "$p" in */udp) ;; *) wait_port "${p%%:*}" 180 ;; esac; done
