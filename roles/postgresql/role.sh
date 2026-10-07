# Role: postgresql - idempotent. Params: VALOR_PARAM_ALLOW_FROM, VALOR_PARAM_DATABASE, VALOR_PARAM_USER
DB="${VALOR_PARAM_DATABASE:-app}"
DBUSER="${VALOR_PARAM_USER:-app}"
[[ "$DB" =~ ^[a-z_][a-z0-9_]{0,40}$ ]] || { echo "invalid database name '$DB'" >&2; exit 1; }
[[ "$DBUSER" =~ ^[a-z_][a-z0-9_]{0,40}$ ]] || { echo "invalid user name '$DBUSER'" >&2; exit 1; }
for src in ${VALOR_PARAM_ALLOW_FROM:-}; do
  [[ "$src" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || { echo "allow_from entry '$src' is not an address/prefix" >&2; exit 1; }
done

if [ "$VALOR_FAMILY" = rhel ]; then
  pkg_install -- postgresql-server
  CONF=/var/lib/pgsql/data
  if [ ! -f "$CONF/PG_VERSION" ]; then postgresql-setup --initdb >/dev/null; changed; fi
  # RHEL's postgresql.conf has no conf.d include: add one (Debian's has it already)
  if ! grep -q "^include_dir = 'conf.d'" "$CONF/postgresql.conf"; then
    echo "include_dir = 'conf.d'" >> "$CONF/postgresql.conf"; changed
  fi
  install -d -o postgres -g postgres -m 0700 "$CONF/conf.d"
  command -v restorecon >/dev/null 2>&1 && restorecon -R "$CONF/conf.d"
  SERVICE=postgresql
  firewall_open 5432
else
  pkg_install postgresql
  PGVER=$(ls /etc/postgresql | sort -V | tail -1)
  CONF="/etc/postgresql/${PGVER}/main"
  SERVICE="postgresql@${PGVER}-main"
fi

write_file "${CONF}/conf.d/50-valor.conf" 0644 <<CONFEOF || true
# Managed by VALOR (range ${VALOR_RANGE})
listen_addresses = 'localhost,${VALOR_ADDRESS}'
password_encryption = 'scram-sha-256'
CONFEOF

HBA="${CONF}/pg_hba.conf"
write_file "$HBA" 0640 < <(
  awk '/^# BEGIN VALOR/{skip=1} !skip{print} /^# END VALOR/{skip=0}' "$HBA"
  echo "# BEGIN VALOR (managed by the VALOR postgresql role)"
  for src in ${VALOR_PARAM_ALLOW_FROM:-}; do
    echo "host    all    all    ${src}    scram-sha-256"
  done
  echo "# END VALOR"
) || true
chown postgres:postgres "$HBA" "${CONF}/conf.d/50-valor.conf"

systemctl enable -q postgresql
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then
  systemctl restart "$SERVICE"
fi

psql_q() { runuser -u postgres -- psql -X -tAq -c "$1"; }
for i in $(seq 1 20); do psql_q "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done

if [ "$(psql_q "SELECT 1 FROM pg_roles WHERE rolname='${DBUSER}'")" != 1 ]; then
  PW=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  psql_q "CREATE ROLE \"${DBUSER}\" LOGIN PASSWORD '${PW}'"
  (umask 077; printf '%s\n' "$PW" > "/root/.valor-postgres-${DBUSER}")
  changed
fi
if [ "$(psql_q "SELECT 1 FROM pg_database WHERE datname='${DB}'")" != 1 ]; then
  runuser -u postgres -- createdb -O "$DBUSER" "$DB"
  changed
fi

ss -ltn "sport = :5432" | grep "${VALOR_ADDRESS}:5432" >/dev/null
