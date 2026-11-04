# *guards the database* OwO roles/mariadb/role.sh: MariaDB with one database and one user that may connect from
# the listed clients. Idempotent ^w^
# Role: mariadb - idempotent. Params: VALOR_PARAM_ALLOW_FROM, VALOR_PARAM_DATABASE, VALOR_PARAM_USER
# The database and user names (default app), checked against safe patterns UwU
DB="${VALOR_PARAM_DATABASE:-app}"
DBUSER="${VALOR_PARAM_USER:-app}"
[[ "$DB" =~ ^[a-z_][a-z0-9_]{0,40}$ ]] || { echo "invalid database name '$DB'" >&2; exit 1; }
[[ "$DBUSER" =~ ^[a-z_][a-z0-9_]{0,30}$ ]] || { echo "invalid user name '$DBUSER'" >&2; exit 1; }
# allow_from arrives as address/prefix pairs (VALOR turned the spec's names into them) >w<
for src in ${VALOR_PARAM_ALLOW_FROM:-}; do
  [[ "$src" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "allow_from entry '$src' is not an address/prefix" >&2; exit 1; }
done

# Install the server; on Rocky/Alma also open 3306 in firewalld OwO
pkg_install mariadb-server
if [ "$VALOR_FAMILY" = rhel ]; then CONFD=/etc/my.cnf.d; firewall_open 3306; else CONFD=/etc/mysql/mariadb.conf.d; fi
# *opens the window* listen on every address; who may connect is limited per user (and by the range router) ^w^
write_file "$CONFD/90-valor.cnf" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): who may connect is limited per user and by the range router
[mysqld]
bind-address = 0.0.0.0
CONF
# Start at boot; restart when something changed UwU
systemctl enable -q mariadb
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q mariadb; then systemctl restart mariadb; fi

# q(): run one SQL statement as root over the local socket, printing bare results >w<
q() { mysql -N -B -e "$1"; }            # root over the local socket (unix_socket authentication)
# Wait up to 30 seconds for the server to answer OwO
for i in $(seq 1 30); do q "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
q "SELECT 1" >/dev/null

# *creates the den* the database, if it's missing ^w^
if [ -z "$(q "SHOW DATABASES LIKE '${DB}'")" ]; then q "CREATE DATABASE \`${DB}\`"; changed; fi
# The user's random password, made once and kept (readable only by root) in /root UwU
PWFILE="/root/.valor-mariadb-${DBUSER}"
if [ ! -s "$PWFILE" ]; then
  (umask 077; head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24 > "$PWFILE"; echo >> "$PWFILE")
fi
PW=$(head -n1 "$PWFILE")
# netmask(): turn a prefix length into a dotted netmask, because MariaDB host patterns need one >w<
netmask() {                              # 24 -> 255.255.255.0
  local p=$1 out="" i b
  for i in 1 2 3 4; do b=$(( p >= 8 ? 8 : (p > 0 ? p : 0) )); out+=$(( 256 - (1 << (8 - b)) )); p=$(( p - 8 )); [ $i -lt 4 ] && out+=.; done
  echo "$out"
}
# *hands out keys* one account per client source: localhost, single hosts (/32), or whole networks
# (address/netmask), each with full rights on the database OwO
for src in localhost ${VALOR_PARAM_ALLOW_FROM:-}; do
  case "$src" in
    localhost) host=localhost ;;
    */32) host=${src%/32} ;;
    *) host="${src%/*}/$(netmask "${src#*/}")" ;;
  esac
  if [ -z "$(q "SELECT 1 FROM mysql.user WHERE User='${DBUSER}' AND Host='${host}'")" ]; then
    q "CREATE USER '${DBUSER}'@'${host}' IDENTIFIED BY '${PW}'"; changed
  fi
  q "GRANT ALL PRIVILEGES ON \`${DB}\`.* TO '${DBUSER}'@'${host}'"
done
# Done once the server listens on 3306 ^w^
wait_port 3306 30
