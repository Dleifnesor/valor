#!/usr/bin/env bash
# *rolls up sleeves inside the VALOR VM* OwO appliance/setup.sh SETS UP the VALOR VM (the appliance)! ^w^
# The installer copies everything into the VM and runs this as root, for a fresh install or an upgrade. It's
# idempotent (safe to run again). The original header below lists its inputs. In order: OS packages, accounts and
# folders, the Python environment and VALOR's code, content and the web UI, config and secrets, TLS, nginx, the
# firewall, the services, the first admin, automatic updates, a self-check, and wiping the secrets *wags tail*
# VALOR appliance setup. Runs as root inside the VALOR VM (Ubuntu 24.04 LTS), started by the installer over the
# QEMU guest agent:   setup.sh install | upgrade
#
# Idempotent. Inputs in /var/lib/valor-install (root-only, removed at the end):
#   src/            the release payload          config.toml   engine + web settings
#   appliance.env   firewall/TLS/admin settings  token.json    Proxmox API token (install only)
#   admin.pw        first admin password         pve-ca.pem    CA of the Proxmox API (optional)
#   tls.crt/key     your certificate (TLS_MODE=own)
# Strict mode; umask 022 = new files readable by everyone unless made stricter explicitly UwU
set -euo pipefail
umask 022

# The mode (install or upgrade), the staging folder and the unpacked source >w<
MODE="${1:-install}"
STAGE=/var/lib/valor-install
SRC=$STAGE/src
# shellcheck source=/dev/null
# Load the settings (MODE, ALLOWED_NETWORKS, TLS_MODE, VM_HOSTNAME, ...) OwO
source "$STAGE/appliance.env"
# log(): a "[HH:MM:SS] message" line (the installer shows these live); on any error, say which line failed ^w^
log() { echo "[$(date +%H:%M:%S)] $*"; }
trap 'echo "ERROR: setup failed at line $LINENO (exit $?)"' ERR

# No interactive prompts from apt or needrestart UwU
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

# ---- OS packages: nginx, Python venv, sqlite, nftables, curl, openssl, automatic upgrades, the guest agent; then
# upgrade everything, keeping existing config files >w<
log "VALOR ${VALOR_VERSION} - ${MODE}"
log "operating system packages"
apt-get update -q >/dev/null
apt-get install -y -q --no-install-recommends nginx python3-venv sqlite3 nftables ca-certificates curl openssl \
  unattended-upgrades qemu-guest-agent >/dev/null
apt-get -y -q -o Dpkg::Options::=--force-confold upgrade >/dev/null

# ---- The 'valor' system account (no login shell) and the folders, owned so only VALOR (and root) can read them OwO
log "accounts and directories"
id valor >/dev/null 2>&1 || useradd --system --home-dir /var/lib/valor --shell /usr/sbin/nologin --user-group valor
install -d -m 0750 -o root -g valor /etc/valor /etc/valor/tls /etc/valor/ssh
install -d -m 0750 -o valor -g valor /var/lib/valor /var/lib/valor/data /var/lib/valor/data/ranges \
  /var/lib/valor/data/journals /var/lib/valor/state
install -d -m 0755 /opt/valor /opt/valor/bin

# ---- Python: a virtual environment, and packages installed ONLY with their pinned hashes (--require-hashes) ^w^
log "python environment (hash-pinned packages)"
if [[ ! -x /opt/valor/venv/bin/python ]]; then
  python3 -m venv /opt/valor/venv
fi
/opt/valor/venv/bin/pip install -q --disable-pip-version-check --require-hashes --no-deps \
  -r "$SRC/appliance/requirements.lock"
# VALOR itself is pure Python: copy it next to the venv instead of building a package.
# Swap the code in atomically-ish (new folder, then replace), point the venv at it with a .pth file, and precompile UwU
rm -rf /opt/valor/lib.new && mkdir -p /opt/valor/lib.new && cp -a "$SRC/valor" /opt/valor/lib.new/
rm -rf /opt/valor/lib && mv /opt/valor/lib.new /opt/valor/lib
site=$(/opt/valor/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
echo "/opt/valor/lib" > "$site/valor.pth"
/opt/valor/venv/bin/python -m compileall -q /opt/valor/lib >/dev/null
# Wrapper commands `valor` and `valor-web` in /opt/valor/bin (linked into /usr/local/bin): they always run as the
# 'valor' user (root is switched with runuser), so files keep the right owner >w<
for tool in valor valor-web; do
  mod=$([[ $tool == valor ]] && echo valor.cli || echo valor.web.cli)
  # Always runs as the 'valor' user (root is switched), so files under /var/lib/valor keep the right owner.
  {
    echo '#!/bin/sh'
    echo 'export VALOR_CONFIG=/etc/valor/config.toml'
    echo 'if [ "$(id -u)" = 0 ]; then'
    echo "  exec runuser -u valor -g www-data -G valor -- env VALOR_CONFIG=/etc/valor/config.toml /opt/valor/venv/bin/python -m $mod \"\$@\""
    echo 'fi'
    echo "exec /opt/valor/venv/bin/python -m $mod \"\$@\""
  } > /opt/valor/bin/$tool
  chmod 0755 /opt/valor/bin/$tool
  ln -sf /opt/valor/bin/$tool /usr/local/bin/$tool
done

# ---- Content (roles, baselines, catalogs, examples) and the built web UI, each swapped in as a whole; VALOR's CA
# certificate download is carried over OwO
log "content (roles, baselines, OS catalog) and web UI"
rm -rf /opt/valor/share.new && mkdir -p /opt/valor/share.new
cp -a "$SRC/roles" "$SRC/baselines" "$SRC/templates" /opt/valor/share.new/
cp -a "$SRC/examples" /opt/valor/share.new/
rm -rf /opt/valor/share && mv /opt/valor/share.new /opt/valor/share
rm -rf /opt/valor/web.new && cp -a "$SRC/web/dist" /opt/valor/web.new
[[ -f /opt/valor/web/ca.crt ]] && cp -a /opt/valor/web/ca.crt /opt/valor/web.new/ca.crt
rm -rf /opt/valor/web && mv /opt/valor/web.new /opt/valor/web
echo "$VALOR_VERSION" > /opt/valor/VERSION

# ---- Config and secrets: config.toml and the token readable by the valor group only; a 32-byte secret key made
# once; the engine's SSH key made once; and the Proxmox host's name in /etc/hosts when the API certificate needs it ^w^
log "configuration and secrets"
install -m 0640 -o root -g valor "$STAGE/config.toml" /etc/valor/config.toml
[[ -f $STAGE/token.json ]] && install -m 0640 -o root -g valor "$STAGE/token.json" /etc/valor/token.json
[[ -f $STAGE/pve-ca.pem ]] && install -m 0644 "$STAGE/pve-ca.pem" /etc/valor/pve-ca.pem
if [[ ! -f /etc/valor/secret.key ]]; then
  (umask 027; head -c 32 /dev/urandom > /etc/valor/secret.key)
fi
chown root:valor /etc/valor/secret.key && chmod 0640 /etc/valor/secret.key
if [[ ! -f /etc/valor/ssh/id_ed25519 ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "valor-engine (range break-glass)" -f /etc/valor/ssh/id_ed25519
fi
chown valor:valor /etc/valor/ssh/id_ed25519 /etc/valor/ssh/id_ed25519.pub
chmod 0600 /etc/valor/ssh/id_ed25519
if [[ -n "${PVE_HOST:-}" && "$PVE_HOST" != "$PVE_IP" ]] && ! grep -q " $PVE_HOST\$" /etc/hosts; then
  echo "$PVE_IP $PVE_HOST" >> /etc/hosts       # the Proxmox API certificate names this host
fi

# ---- TLS: install the valor-tls helper; use your own certificate, or let valor-tls make/renew VALOR's own CA UwU
log "TLS certificate ($TLS_MODE)"
install -m 0755 "$SRC/appliance/valor-tls" /opt/valor/bin/valor-tls
if [[ $TLS_MODE == own && -f $STAGE/tls.crt ]]; then
  install -m 0644 "$STAGE/tls.crt" /etc/valor/tls/server.crt
  install -m 0640 -o root -g www-data "$STAGE/tls.key" /etc/valor/tls/server.key
  rm -f /etc/valor/tls/ca.key /opt/valor/web/ca.crt
fi
/opt/valor/bin/valor-tls ensure "$TLS_MODE" "$VM_HOSTNAME" "$VM_DOMAIN"

# ---- nginx: VALOR's site and security headers, no default site, hide nginx's version, test, then (re)load >w<
log "web server (nginx)"
install -m 0644 "$SRC/appliance/nginx-headers.conf" /etc/nginx/snippets/valor-headers.conf
install -m 0644 "$SRC/appliance/nginx.conf" /etc/nginx/sites-available/valor
ln -sf /etc/nginx/sites-available/valor /etc/nginx/sites-enabled/valor
rm -f /etc/nginx/sites-enabled/default
sed -i 's/^\s*#\?\s*server_tokens .*/\tserver_tokens off;/' /etc/nginx/nginx.conf
nginx -t -q
systemctl enable -q nginx
systemctl reload-or-restart nginx

# ---- Firewall: fill the allowed networks into the nftables template (the @...@ placeholders), check it, install it,
# load it OwO
log "firewall (nftables: HTTPS from ${ALLOWED_NETWORKS}; SSH from the Proxmox nodes)"
web=$(echo "$ALLOWED_NETWORKS" | tr ' ' ',')
ssh=$(echo "$SSH_FROM" | tr ' ' ',')
sed -e "s|@WEB_ALLOWED@|$web|" -e "s|@SSH_ALLOWED@|$ssh|" "$SRC/appliance/nftables.conf" > /etc/nftables.conf.new
nft -c -f /etc/nftables.conf.new
mv /etc/nftables.conf.new /etc/nftables.conf
systemctl enable -q nftables
nft -f /etc/nftables.conf

# ---- Services: install the units, create/upgrade the database, enable and (re)start everything ^w^
log "services"
for unit in valor-web.service valor-worker.service valor-tls.service valor-tls.timer; do
  install -m 0644 "$SRC/appliance/$unit" /etc/systemd/system/$unit
done
systemctl daemon-reload
/opt/valor/bin/valor-web migrate >/dev/null
systemctl enable -q valor-web valor-worker valor-tls.timer
systemctl restart valor-web valor-worker
systemctl start valor-tls.timer

# ---- First admin (fresh installs only): the password goes via a root-only file in /run that is shredded right
# after UwU
if [[ $MODE == install && -f $STAGE/admin.pw ]]; then
  log "first admin account ($ADMIN_USER)"
  install -m 0400 -o valor -g valor "$STAGE/admin.pw" /run/valor-admin.pw
  /opt/valor/bin/valor-web create-admin --username "$ADMIN_USER" --password-file /run/valor-admin.pw
  shred -u /run/valor-admin.pw
fi

# ---- Automatic security updates (daily) >w<
log "automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# ---- Self-check: the health endpoint must answer (30 tries), NOTHING unexpected may listen (only SSH, HTTP(S) and
# DHCP client ports), and the services must be active OwO
log "self-check"
for i in $(seq 1 30); do
  curl -fsk --max-time 3 https://127.0.0.1/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -fsk --max-time 5 https://127.0.0.1/api/health | grep -q '"ok":true'
unexpected=$(ss -H -lntu | awk '{print $5}' | grep -vE '^(127\.|\[::1\]|::1)' | sed -E 's/.*:([0-9]+)$/\1/' \
  | sort -u | grep -vxE '22|80|443|68|546' || true)
if [[ -n $unexpected ]]; then
  echo "ERROR: unexpected listening ports: $unexpected"
  exit 1
fi
systemctl is-active -q valor-web valor-worker nginx nftables

# ---- Shred the secrets in the staging area and remove it ^w^ *dusts off paws*
log "cleaning up the staging area (secrets)"
find "$STAGE" -type f \( -name 'token.json' -o -name 'admin.pw' -o -name 'tls.key' \) -exec shred -u {} +
rm -rf "$STAGE"
log "done: VALOR ${VALOR_VERSION} is running"
