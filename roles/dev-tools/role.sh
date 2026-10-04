# Role: dev-tools - idempotent. Params: VALOR_PARAM_EXTRA_PACKAGES
EXTRA="${VALOR_PARAM_EXTRA_PACKAGES:-}"
for p in $EXTRA; do [[ "$p" =~ ^[a-z0-9][a-z0-9.+-]+$ ]] || { echo "invalid package name '$p'" >&2; exit 1; }; done
# shellcheck disable=SC2086
pkg_install git build-essential python3-venv python3-pip tmux vim-tiny curl postgresql-client $EXTRA \
  -- git gcc make python3 python3-pip tmux vim-minimal curl postgresql $EXTRA
git --version >/dev/null && python3 -m venv --help >/dev/null
