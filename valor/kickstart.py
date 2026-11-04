# OwO *pounces on the kickstart file* hewwo! valor/kickstart.py installs Rocky/Alma Linux the old-fashioned way:
# from the official installer ISO instead of a ready-made cloud image (spec setting `install: iso`) ^w^
# How it works, in short: VALOR makes an empty VM with TWO CD drives: the real installer ISO, and a teeny CD
# labeled "OEMDRV" holding ks.cfg (a kickstart = an answer file for the RHEL installer, Anaconda). Anaconda
# automatically looks for ks.cfg on any disk labeled OEMDRV, so nobody has to edit the boot menu! *wags tail*
# The kickstart has NO secrets in it; the login password is set later over the QEMU guest agent >w<
"""Install a RHEL-family host (Rocky, Alma) from its official ISO instead of the cloud image (`install: iso`).

VALOR creates an empty VM with two CD-ROMs: the installer ISO from the library and a small ISO labeled OEMDRV
holding ks.cfg - Anaconda finds a kickstart on an OEMDRV volume by itself, so the installer's boot menu is never
touched. The kickstart holds no secrets (the login password is set over the guest agent afterwards, as for cloud
images); it sets the static address, the guest agent and VALOR's SSH key, then reboots into the installed system.
"""

# Lazy type hints UwU
from __future__ import annotations

# ipaddress turns a prefix length (24) into a netmask (255.255.255.0); Path is the output file's path OwO
import ipaddress
from pathlib import Path

# iso9660 = VALOR's own tiny ISO writer (pure Python; see iso9660.py) *nuzzles*
from . import iso9660

# MARKER: a file the installed system writes at the end of %post. When VALOR sees it over the guest agent, it
# knows the REAL installed system is running (not the installer) ^w^
# LABEL: the volume label Anaconda searches for a kickstart on. It must be exactly "OEMDRV" >w<
MARKER = "/etc/valor-installed"          # written by %post: the installed system (not the installer) is up
LABEL = "OEMDRV"


# kickstart(): build the ks.cfg text for one host *paws at keyboard*
#   host         the VM's hostname
#   address      its static IPv4 address; prefix = the network size (24 = /24); gateway = the router's address
#   nameservers  DNS servers; user = the admin account to create; ssh_key = VALOR's public key for that user
#   device       which network card to configure (its MAC, or "link" = the first one with a cable plugged in)
#   repos        extra online package repos (e.g. AppStream), name -> URL
# Returns the whole kickstart file as text UwU
def kickstart(host: str, address: str, prefix: int, gateway: str, nameservers: list[str], user: str,
              ssh_key: str, device: str = "link", repos: dict[str, str] | None = None) -> str:
    """device: the NIC's MAC address (VALOR sets it when creating the VM), or 'link' for the first connected NIC.
    repos: extra online repositories (name -> base URL), e.g. AppStream for packages the minimal ISO lacks; the
    router's temporary build egress is open while the host installs."""
    # One "repo --name=... --baseurl=..." line per extra repository (an empty string when there are none) ^w^
    repo_lines = "".join(f"repo --name={name} --baseurl={url}\n" for name, url in (repos or {}).items())
    # Kickstart wants a dotted netmask, so turn the prefix into one: /24 -> "255.255.255.0" OwO
    netmask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
    # Several DNS servers are written comma-separated, as kickstart expects >w<
    dns = ",".join(nameservers)
    # The SSH key goes inside double quotes in the file, so strip any quote chars from it (and stray spaces/newlines)
    # so it can never break out of the quotes *guards the quotes with tail*
    key = ssh_key.strip().replace('"', "")
    # The kickstart itself, as one big f-string (the {placeholders} are filled in from the arguments). Line by line:
    #   text / cdrom                    text-mode install, packages come from the CD
    #   lang / keyboard / timezone      English, US keyboard, UTC clock
    #   network ...                     the static address, gateway, DNS and hostname, brought up right away
    #   rootpw --lock                   root can't log in with a password at all
    #   user ... --lock / sshkey        the admin user (in wheel), password locked, VALOR's SSH key allowed
    #   zerombr / clearpart / autopart  wipe the (new, empty) disk and lay it out with LVM, no separate /home
    #   bootloader / firstboot          boot loader in the MBR, no first-boot wizard
    #   selinux / firewall              SELinux enforcing, firewall on with SSH allowed
    #   reboot --eject                  reboot into the installed system when done, ejecting the CDs
    #   %packages                       the minimal environment + the QEMU guest agent
    #   %post                           allow guest-exec in the agent config, make only the agent's SELinux domain
    #                                   permissive (so VALOR can manage the VM), enable the agent, passwordless sudo for
    #                                   the admin user, then write the MARKER file with the time ^w^ *happy tail wags*
    # (The doubled backslashes are Python escapes that become single backslashes in the file.)
    return f"""# VALOR kickstart for {host} (generated; no secrets)
text
cdrom
{repo_lines}lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --device={device} --bootproto=static --ip={address} --netmask={netmask} --gateway={gateway} --nameserver={dns} --hostname={host} --activate --onboot=yes
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


# build_iso: write the OEMDRV CD with ks.cfg on it to `out`. It's done in pure Python because the worker runs in
# a sandbox whose seccomp filter (on purpose!) blocks tools like genisoimage OwO
def build_iso(ks_text: str, out: Path) -> None:
    """The OEMDRV CD with ks.cfg, written in pure Python (the worker's sandbox rightly blocks ISO tools)."""
    # Encode the kickstart text to bytes, build the ISO image with the OEMDRV label, and write it to the file >w<
    out.write_bytes(iso9660.build({"ks.cfg": ks_text.encode()}, LABEL))
