# *shares its toys* OwO roles/samba-share/role.sh: a Samba (SMB) file share. Idempotent ^w^
# Role: samba-share - idempotent. Params: VALOR_PARAM_SHARE, VALOR_PARAM_GUEST, VALOR_PARAM_WRITABLE
# The share name (checked), guest access, writability, and the share's folder UwU
SHARE="${VALOR_PARAM_SHARE:-share}"
[[ "$SHARE" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || { echo "invalid share name '$SHARE'" >&2; exit 1; }
GUEST="${VALOR_PARAM_GUEST:-false}"; WRITABLE="${VALOR_PARAM_WRITABLE:-true}"
DIR="/srv/share/${SHARE}"

# Install Samba; the service is smb on Rocky/Alma and smbd on Debian >w<
pkg_install samba
if [ "$VALOR_FAMILY" = rhel ]; then SERVICE=smb; else SERVICE=smbd; fi
# *makes a friend* a system user "share" that owns the files OwO
id share >/dev/null 2>&1 || { useradd --system --no-create-home --shell /usr/sbin/nologin share; changed; }
# The folder, group-writable with the setgid bit so new files stay in the group ^w^
install -d -m 2775 -o share -g share "$DIR"
# SELinux: label the share folder for Samba UwU
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = Enforcing ]; then
  dnf_install policycoreutils-python-utils
  semanage fcontext -l | grep '^/srv/share(/.\*)?' >/dev/null || { semanage fcontext -a -t samba_share_t '/srv/share(/.*)?'; changed; }
  restorecon -R /srv/share
fi
# Open Samba in firewalld where it runs >w<
if systemctl is-active -q firewalld 2>/dev/null && ! firewall-cmd -q --query-service=samba; then
  firewall-cmd -q --permanent --add-service=samba; firewall-cmd -q --reload; changed
fi

# *writes the config* a standalone server, unknown users mapped to guest, and the share with its read-only and
# guest settings, everything owned by "share" OwO
write_file /etc/samba/smb.conf 0644 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
[global]
   workgroup = WORKGROUP
   server string = ${VALOR_HOST}
   server role = standalone server
   map to guest = Bad User
   log file = /var/log/samba/log.%m
   max log size = 1000

[${SHARE}]
   path = ${DIR}
   read only = $( [ "$WRITABLE" = true ] && echo no || echo yes )
   guest ok = $( [ "$GUEST" = true ] && echo yes || echo no )
   force user = share
   force group = share
   create mask = 0664
   directory mask = 2775
CONF
# Check the config ^w^
testparm -s >/dev/null 2>&1 || { testparm -s; exit 1; }

# The share user's random password, made once and kept for root, then registered with Samba UwU
PWFILE=/root/.valor-samba-share
if [ ! -s "$PWFILE" ]; then
  (umask 077; head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20 > "$PWFILE"; echo >> "$PWFILE")
fi
if ! pdbedit -L 2>/dev/null | grep '^share:' >/dev/null; then
  PW=$(head -n1 "$PWFILE"); printf '%s\n%s\n' "$PW" "$PW" | smbpasswd -s -a share >/dev/null; changed
fi
# Start at boot, restart when something changed, and wait for port 445 >w<
systemctl enable -q "$SERVICE"
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q "$SERVICE"; then systemctl restart "$SERVICE"; fi
wait_port 445 30
