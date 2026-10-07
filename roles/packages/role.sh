# Role: packages - idempotent. Params: VALOR_PARAM_PACKAGES, _RHEL_PACKAGES, _SERVICES, _PORTS, _EPEL
PKGS="${VALOR_PARAM_PACKAGES:-}"
[ "$VALOR_FAMILY" = rhel ] && [ -n "${VALOR_PARAM_RHEL_PACKAGES:-}" ] && PKGS="$VALOR_PARAM_RHEL_PACKAGES"
for w in $PKGS ${VALOR_PARAM_SERVICES:-}; do
  [[ "$w" =~ ^[A-Za-z0-9][A-Za-z0-9.+_:@-]*$ ]] || { echo "invalid name '$w'" >&2; exit 1; }
done
for p in ${VALOR_PARAM_PORTS:-}; do [[ "$p" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$p'" >&2; exit 1; }; done
[ -n "$PKGS" ] || { echo "no packages given" >&2; exit 1; }

if [ "$VALOR_FAMILY" = rhel ] && [ "${VALOR_PARAM_EPEL:-false}" = true ]; then
  dnf_install epel-release
fi
# shellcheck disable=SC2086
pkg_install $PKGS

for s in ${VALOR_PARAM_SERVICES:-}; do
  systemctl is-enabled -q "$s" 2>/dev/null || { systemctl enable -q "$s"; changed; }
  systemctl is-active -q "$s" || { systemctl start "$s" || true; changed; }
  systemctl is-active -q "$s" || { echo "service $s did not start:" >&2; journalctl -u "$s" -n 20 --no-pager >&2; exit 1; }
done
for p in ${VALOR_PARAM_PORTS:-}; do
  firewall_open "$p"
  wait_port "$p" 60
done
