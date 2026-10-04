# Role: attacker-tools - idempotent. Params: VALOR_PARAM_EXTRA_PACKAGES
EXTRA="${VALOR_PARAM_EXTRA_PACKAGES:-}"
for p in $EXTRA; do [[ "$p" =~ ^[a-z0-9][a-z0-9.+-]+$ ]] || { echo "invalid package name '$p'" >&2; exit 1; }; done
# shellcheck disable=SC2086
pkg_install nmap netcat-openbsd tcpdump curl dnsutils $EXTRA -- nmap nmap-ncat tcpdump curl bind-utils $EXTRA
command -v nmap >/dev/null && command -v nc >/dev/null && command -v dig >/dev/null
