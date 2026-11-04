# *adjusts tiny keyboard* OwO roles/dev-tools/role.sh: installs developer basics. Idempotent ^w^
# Role: dev-tools - idempotent. Params: VALOR_PARAM_EXTRA_PACKAGES
# Extra packages from the spec, each checked against a safe name pattern UwU
EXTRA="${VALOR_PARAM_EXTRA_PACKAGES:-}"
for p in $EXTRA; do [[ "$p" =~ ^[a-z0-9][a-z0-9.+-]+$ ]] || { echo "invalid package name '$p'" >&2; exit 1; }; done
# Debian names, then "--" and the Rocky/Alma names; $EXTRA is unquoted on purpose so it splits into words
# (the directive below says that is intended) >w<
# shellcheck disable=SC2086
pkg_install git build-essential python3-venv python3-pip tmux vim-tiny curl postgresql-client $EXTRA \
  -- git gcc make python3 python3-pip tmux vim-minimal curl postgresql $EXTRA
# *tests the tools* git and Python venvs must work OwO
git --version >/dev/null && python3 -m venv --help >/dev/null
