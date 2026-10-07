# *boops a shipping container* OwO roles/container/role.sh: runs any container image as a service with
# run_container (Docker on Debian family, Podman on Rocky/Alma), restarted on boot. Idempotent ^w^
# Role: container - idempotent. Params: VALOR_PARAM_IMAGE, _NAME, _PUBLISH, _ENV
# Published ports need IP forwarding: container_engine keeps it on and marks the host (the baseline exempts it)
# The image and container name, both checked against safe patterns UwU
IMAGE="${VALOR_PARAM_IMAGE:-}"; NAME="${VALOR_PARAM_NAME:-app}"
[[ "$IMAGE" =~ ^[a-z0-9][a-z0-9._/:@-]*$ ]] || { echo "invalid image '$IMAGE'" >&2; exit 1; }
[[ "$NAME" =~ ^[a-z0-9][a-z0-9_.-]*$ ]] || { echo "invalid container name '$NAME'" >&2; exit 1; }
# *collects the options* docker run arguments are gathered in an array >w<
args=()
# Each publish entry (host:container[/proto]) becomes -p, and its host port is opened in firewalld OwO
for p in ${VALOR_PARAM_PUBLISH:-}; do
  [[ "$p" =~ ^([0-9]{1,5}):[0-9]{1,5}(/(tcp|udp))?$ ]] || { echo "invalid publish '$p' (host:container)" >&2; exit 1; }
  args+=(-p "$p")
  case "$p" in */udp) firewall_open "${p%%:*}" udp ;; *) firewall_open "${p%%:*}" ;; esac
done
# Each KEY=VALUE becomes -e ^w^
for e in ${VALOR_PARAM_ENV:-}; do
  [[ "$e" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || { echo "invalid env entry '$e' (KEY=VALUE)" >&2; exit 1; }
  args+=(-e "$e")
done
# (Re)create the container only when its settings changed; the image is pulled during the build UwU
run_container "$NAME" "$IMAGE" "${args[@]}"
# *waits patiently* wait up to 3 minutes for every published TCP port (UDP can't be waited for this way) >w<
for p in ${VALOR_PARAM_PUBLISH:-}; do case "$p" in */udp) ;; *) wait_port "${p%%:*}" 180 ;; esac; done
