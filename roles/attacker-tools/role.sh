# *sharpens claws* OwO roles/attacker-tools/role.sh: installs the pentest toolbox. Runs as root after VALOR's
# helpers (pkg_install etc.) and is idempotent ^w^
# Role: attacker-tools - idempotent. Params: VALOR_PARAM_EXTRA_PACKAGES
# Extra packages from the spec, each checked against a safe package-name pattern UwU
EXTRA="${VALOR_PARAM_EXTRA_PACKAGES:-}"
for p in $EXTRA; do [[ "$p" =~ ^[a-z0-9][a-z0-9.+-]+$ ]] || { echo "invalid package name '$p'" >&2; exit 1; }; done
# Debian names before "--", Rocky/Alma names after it; $EXTRA is left unquoted on purpose so it splits into
# words (the directive below tells shellcheck that's intended) >w<
# shellcheck disable=SC2086
pkg_install nmap netcat-openbsd tcpdump curl dnsutils $EXTRA -- nmap nmap-ncat tcpdump curl bind-utils $EXTRA
# *checks its pockets* the role passes only if nmap, nc and dig really exist OwO
command -v nmap >/dev/null && command -v nc >/dev/null && command -v dig >/dev/null
