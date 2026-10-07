# Role: mariadb - idempotent. Params: VALOR_PARAM_ALLOW_FROM, VALOR_PARAM_DATABASE, VALOR_PARAM_USER
DB="${VALOR_PARAM_DATABASE:-app}"
DBUSER="${VALOR_PARAM_USER:-app}"
[[ "$DB" =~ ^[a-z_][a-z0-9_]{0,40}$ ]] || { echo "invalid database name '$DB'" >&2; exit 1; }
[[ "$DBUSER" =~ ^[a-z_][a-z0-9_]{0,30}$ ]] || { echo "invalid user name '$DBUSER'" >&2; exit 1; }
for src in ${VALOR_PARAM_ALLOW_FROM:-}; do
  [[ "$src" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "allow_from entry '$src' is not an address/prefix" >&2; exit 1; }
done

pkg_install mariadb-server
if [ "$VALOR_FAMILY" = rhel ]; then CONFD=/etc/my.cnf.d; firewall_open 3306; else CONFD=/etc/mysql/mariadb.conf.d; fi
write_file "$CONFD/90-valor.cnf" 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}): who may connect is limited per user and by the range router
[mysqld]
bind-address = 0.0.0.0
CONF
systemctl enable -q mariadb
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q mariadb; then systemctl restart mariadb; fi

q() { mysql -N -B -e "$1"; }            # root over the local socket (unix_socket authentication)
for i in $(seq 1 30); do q "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
q "SELECT 1" >/dev/null

if [ -z "$(q "SHOW DATABASES LIKE '${DB}'")" ]; then q "CREATE DATABASE \`${DB}\`"; changed; fi
PWFILE="/root/.valor-mariadb-${DBUSER}"
if [ ! -s "$PWFILE" ]; then
  (umask 077; head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24 > "$PWFILE"; echo >> "$PWFILE")
fi
PW=$(head -n1 "$PWFILE")
netmask() {                              # 24 -> 255.255.255.0
  local p=$1 out="" i b
  for i in 1 2 3 4; do b=$(( p >= 8 ? 8 : (p > 0 ? p : 0) )); out+=$(( 256 - (1 << (8 - b)) )); p=$(( p - 8 )); [ $i -lt 4 ] && out+=.; done
  echo "$out"
}
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
wait_port 3306 30
