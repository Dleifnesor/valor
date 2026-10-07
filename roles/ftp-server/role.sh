# *carries files in its mouth* OwO roles/ftp-server/role.sh: a vsftpd FTP server. Idempotent ^w^
# Role: ftp-server - idempotent. Params: VALOR_PARAM_ANONYMOUS
# anonymous=true becomes vsftpd's YES, anything else NO UwU
ANON=$( [ "${VALOR_PARAM_ANONYMOUS:-false}" = true ] && echo YES || echo NO )
# Install vsftpd (same name on both families) >w<
pkg_install vsftpd
# Rocky/Alma: config path, firewalld opening FTP and the passive ports, and SELinux allowing full FTP access OwO
if [ "$VALOR_FAMILY" = rhel ]; then
  CONF=/etc/vsftpd/vsftpd.conf; EMPTY=/usr/share/empty
  if systemctl is-active -q firewalld 2>/dev/null && ! firewall-cmd -q --query-service=ftp; then
    firewall-cmd -q --permanent --add-service=ftp; firewall-cmd -q --permanent --add-port=40000-40100/tcp
    firewall-cmd -q --reload; changed
  fi
  if command -v getsebool >/dev/null 2>&1 && getsebool ftpd_full_access | grep off >/dev/null; then setsebool -P ftpd_full_access on; changed; fi
else
  # Debian/Ubuntu: config path and the empty chroot directory ^w^
  CONF=/etc/vsftpd.conf; EMPTY=/var/run/vsftpd/empty
fi
# The anonymous download folder UwU
install -d -m 0755 /srv/ftp
# *writes the house rules* the vsftpd config: local users locked into their home (chroot), optional anonymous
# reads from /srv/ftp, passive ports 40000-40100 and a banner >w<
write_file "$CONF" 0600 <<CONF || true
# Managed by VALOR (range ${VALOR_RANGE}, host ${VALOR_HOST})
listen=YES
listen_ipv6=NO
anonymous_enable=${ANON}
anon_root=/srv/ftp
no_anon_password=YES
local_enable=YES
write_enable=YES
local_umask=022
chroot_local_user=YES
allow_writeable_chroot=YES
secure_chroot_dir=${EMPTY}
pam_service_name=vsftpd
xferlog_enable=YES
connect_from_port_20=YES
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100
ftpd_banner=VALOR range ${VALOR_RANGE} - ${VALOR_HOST}
CONF
# Start at boot; restart when something changed; wait for port 21 OwO
systemctl enable -q vsftpd
if [ "$VALOR_CHANGED" = 1 ] || ! systemctl is-active -q vsftpd; then systemctl restart vsftpd; fi
wait_port 21 30
