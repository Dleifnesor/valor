"""Install a RHEL-family host (Rocky, Alma) from its official ISO instead of the cloud image (`install: iso`).

VALOR creates an empty VM with two CD-ROMs: the installer ISO from the library and a small ISO labeled OEMDRV
holding ks.cfg - Anaconda finds a kickstart on an OEMDRV volume by itself, so the installer's boot menu is never
touched. The kickstart holds no secrets (the login password is set over the guest agent afterwards, as for cloud
images); it sets the static address, the guest agent and VALOR's SSH key, then reboots into the installed system.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import ValorError

MARKER = "/etc/valor-installed"          # written by %post: the installed system (not the installer) is up
LABEL = "OEMDRV"


def kickstart(host: str, address: str, prefix: int, gateway: str, nameservers: list[str], user: str,
              ssh_key: str) -> str:
    mask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
    ns = ",".join(nameservers)
    key = ssh_key.strip().replace('"', "")
    return f"""# VALOR kickstart for {host} (generated; no secrets)
text
cdrom
lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --device=link --bootproto=static --ip={address} --netmask={mask} --gateway={gateway} --nameserver={ns} --hostname={host} --activate --onboot=yes
rootpw --lock
user --name={user} --groups=wheel --lock
sshkey --username={user} "{key}"
zerombr
clearpart --all --initlabel
autopart --type=lvm --nohome
bootloader --location=mbr
firstboot --disabled
selinux --enforcing
firewall --enabled --ssh
reboot --eject

%packages
@^minimal-environment
qemu-guest-agent
%end

%post --log=/root/valor-ks-post.log
# the guest agent is VALOR's management channel: allow guest-exec, and make only its SELinux domain permissive
sed -i -E 's/^(BLACKLIST_RPC|BLOCK_RPCS|FILTER_RPC_ARGS)=.*/\\1=/' /etc/sysconfig/qemu-ga || true
printf '(typepermissive virt_qemu_ga_t)\\n' > /root/valor_qga.cil && semodule -i /root/valor_qga.cil; rm -f /root/valor_qga.cil
systemctl enable qemu-guest-agent
echo '{user} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-valor && chmod 440 /etc/sudoers.d/90-valor
date -u +%FT%TZ > {MARKER}
%end
"""


def build_iso(ks_text: str, out: Path) -> None:
    tool = shutil.which("genisoimage") or shutil.which("mkisofs") or shutil.which("xorrisofs")
    if not tool:
        raise ValorError("iso_tool_missing", "genisoimage is not installed on the VALOR VM",
                         hint="Upgrade VALOR (the installer adds it) or install genisoimage.")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "ks.cfg").write_text(ks_text)
        r = subprocess.run([tool, "-quiet", "-J", "-r", "-V", LABEL, "-o", str(out), d], capture_output=True, text=True)
        if r.returncode:
            raise ValorError("iso_build_failed", f"could not build the kickstart ISO: {r.stderr.strip()[-300:]}")
