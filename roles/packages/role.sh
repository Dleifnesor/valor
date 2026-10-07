# *rummages through the package shelf* OwO roles/packages/role.sh: installs any packages, enables their
# services and opens their ports. Idempotent ^w^
# Role: packages - idempotent. Params: VALOR_PARAM_PACKAGES, _RHEL_PACKAGES, _SERVICES, _PORTS, _EPEL
# The package list (or the Rocky/Alma list on those hosts) UwU
PKGS="${VALOR_PARAM_PACKAGES:-}"
[ "$VALOR_FAMILY" = rhel ] && [ -n "${VALOR_PARAM_RHEL_PACKAGES:-}" ] && PKGS="$VALOR_PARAM_RHEL_PACKAGES"
# Package and service names must look like names (no shell tricks!) >w<
for w in $PKGS ${VALOR_PARAM_SERVICES:-}; do
  [[ "$w" =~ ^[A-Za-z0-9][A-Za-z0-9.+_:@-]*$ ]] || { echo "invalid name '$w'" >&2; exit 1; }
done
# Ports must be numbers, and there must be at least one package OwO
for p in ${VALOR_PARAM_PORTS:-}; do [[ "$p" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$p'" >&2; exit 1; }; done
[ -n "$PKGS" ] || { echo "no packages given" >&2; exit 1; }

# EPEL first, when asked, on Rocky/Alma ^w^
if [ "$VALOR_FAMILY" = rhel ] && [ "${VALOR_PARAM_EPEL:-false}" = true ]; then
  dnf_install epel-release
fi
# Install them all; $PKGS is unquoted on purpose so it splits into words (the directive tells shellcheck) UwU
# shellcheck disable=SC2086
pkg_install $PKGS

# *wakes up every service* enable and start each one; a service that won't start shows its journal and fails >w<
for s in ${VALOR_PARAM_SERVICES:-}; do
  systemctl is-enabled -q "$s" 2>/dev/null || { systemctl enable -q "$s"; changed; }
  systemctl is-active -q "$s" || { systemctl start "$s" || true; changed; }
  systemctl is-active -q "$s" || { echo "service $s did not start:" >&2; journalctl -u "$s" -n 20 --no-pager >&2; exit 1; }
done
# Open each port in firewalld and wait up to a minute for it OwO
for p in ${VALOR_PARAM_PORTS:-}; do
  firewall_open "$p"
  wait_port "$p" 60
done
