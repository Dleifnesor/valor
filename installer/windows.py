"""Windows templates from installer ISOs: unattended setup, VirtIO guest tools (QEMU agent), sysprep, template.

The ISO comes from the ISO library (catalog entry or an uploaded ISO); the edition is picked by name from the
ISO's install.wim. Setup runs with UEFI + Secure Boot + TPM 2.0 (Windows 11 needs them; Server works the same),
a SATA disk and an Intel e1000e NIC - drivers Windows ships with - so nothing can stall the unattended install.
The NIC is unplugged during the build (no updates or Store apps sneak in and break sysprep).
"""

from __future__ import annotations

import os
import shutil
import secrets
import string
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

from . import ui
from .answers import Answers
from .discover import Facts
from .record import Record
from .sh import pvesh, run
from .templates import _create
from .wim import editions_from_iso, pick

NS = 'xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"'
COMP = 'processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'
DISK_GIB = 64


def _password() -> str:
    """Template build password: meets Windows complexity (upper, lower, digit, symbol)."""
    while True:
        pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(18)) + "-Va1"
        if any(c.isdigit() for c in pw) and any(c.islower() for c in pw) and any(c.isupper() for c in pw):
            return pw


def _oobe(password: str, autologon: bool) -> str:
    pw = escape(password)
    logon = f"""
      <AutoLogon><Enabled>true</Enabled><Username>Administrator</Username><LogonCount>1</LogonCount>
        <Password><Value>{pw}</Value><PlainText>true</PlainText></Password></AutoLogon>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add"><Order>1</Order><Description>VALOR template setup</Description>
          <CommandLine>powershell -NoProfile -ExecutionPolicy Bypass -Command "$v = Get-Volume | Where-Object {{ $_.DriveLetter -and (Test-Path ($_.DriveLetter + ':\\valor-setup.ps1')) }} | Select-Object -First 1; &amp; ($v.DriveLetter + ':\\valor-setup.ps1')"</CommandLine>
        </SynchronousCommand>
      </FirstLogonCommands>""" if autologon else ""
    return f"""
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" {COMP}>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" {COMP}>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideLocalAccountScreen>true</HideLocalAccountScreen>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <ProtectYourPC>3</ProtectYourPC>
      </OOBE>
      <UserAccounts><AdministratorPassword><Value>{pw}</Value><PlainText>true</PlainText></AdministratorPassword></UserAccounts>
      <TimeZone>UTC</TimeZone>{logon}
    </component>
  </settings>"""


def _specialize() -> str:
    return f"""
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" {COMP}>
      <ComputerName>*</ComputerName>
      <TimeZone>UTC</TimeZone>
    </component>
    <component name="Microsoft-Windows-Deployment" {COMP}>
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add"><Order>1</Order>
          <Path>reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\OOBE /v BypassNRO /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
        <RunSynchronousCommand wcm:action="add"><Order>2</Order>
          <Path>reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\BitLocker /v PreventDeviceEncryption /t REG_DWORD /d 1 /f</Path>
        </RunSynchronousCommand>
      </RunSynchronous>
    </component>
  </settings>"""


def autounattend(image_index: int, password: str) -> str:
    """Answer file for the template build: wipe disk 0 (UEFI layout), install the chosen edition, log on once."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend {NS}>
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" {COMP}>
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" {COMP}>
      <DiskConfiguration>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>260</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
            <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID></ModifyPartition>
            <ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/INDEX</Key><Value>{image_index}</Value></MetaData></InstallFrom>
          <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
        </OSImage>
      </ImageInstall>
      <UserData><AcceptEula>true</AcceptEula><FullName>VALOR</FullName><Organization>VALOR</Organization></UserData>
    </component>
  </settings>{_specialize()}{_oobe(password, autologon=True)}
</unattend>
"""


def clone_unattend(password: str) -> str:
    """Answer file sysprep leaves in the template: every clone finishes OOBE by itself (no logon)."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend {NS}>
  <settings pass="generalize">
    <component name="Microsoft-Windows-PnpSysprep" {COMP}>
      <PersistAllDeviceInstalls>true</PersistAllDeviceInstalls>
    </component>
  </settings>{_specialize()}{_oobe(password, autologon=False)}
</unattend>
"""


SETUP_PS1 = r"""# VALOR template setup (first logon of the template build VM). Logs to C:\Windows\Temp\valor-setup.log
$ErrorActionPreference = 'Stop'
Start-Transcript -Path C:\Windows\Temp\valor-setup.log -Force
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# 1. VirtIO drivers + QEMU guest agent (VALOR's management channel)
$virtio = Get-Volume | Where-Object { $_.DriveLetter -and (Test-Path "$($_.DriveLetter):\virtio-win-guest-tools.exe") } | Select-Object -First 1
if (-not $virtio) { throw 'virtio-win ISO not found' }
Start-Process -FilePath "$($virtio.DriveLetter):\virtio-win-guest-tools.exe" -ArgumentList '/install','/quiet','/norestart' -Wait
Set-Service -Name QEMU-GA -StartupType Automatic
# 2. Lab-friendly defaults: no hibernation, high performance, answer ping (verification tests use it)
powercfg /hibernate off
powercfg /setactive SCHEME_MIN
Get-NetFirewallRule -Name 'FPS-ICMP4-ERQ-In*' -ErrorAction SilentlyContinue | Enable-NetFirewallRule
# 3. Generalize and shut down; clones finish OOBE by themselves with the clone answer file
Copy-Item -Path (Join-Path $here 'clone-unattend.xml') -Destination C:\Windows\Panther\valor-clone.xml -Force
Stop-Transcript
& "$env:WINDIR\System32\Sysprep\sysprep.exe" /generalize /oobe /shutdown /quiet /unattend:C:\Windows\Panther\valor-clone.xml
"""


def _iso_volid(a: Answers, facts: Facts, filename: str) -> str:
    for storage in a.iso_storages or [a.iso_storage]:
        for it in pvesh("get", f"/nodes/{facts.node}/storage/{storage}/content", content="iso") or []:
            if it["volid"].split("/", 1)[-1] == filename:
                return it["volid"]
    raise SystemExit(f"{filename} is not in the ISO library yet: download it in VALOR (ISO library) first")


def _iso_path(facts: Facts, volid: str) -> str:
    return run(["pvesm", "path", volid]).stdout.strip()


def build(a: Answers, facts: Facts, rec: Record, os_name: str, entry: dict, isos: dict, cpu: str) -> int:
    """isos: ISO catalog (templates/isos.toml). Returns the template VMID."""
    if not shutil.which("genisoimage"):
        raise SystemExit("genisoimage is missing (Proxmox VE installs it with qemu-server for cloud-init): "
                         "apt install genisoimage")
    iso_entry = isos[entry["iso"]]
    win_volid = _iso_volid(a, facts, iso_entry["filename"])
    virtio_volid = _iso_volid(a, facts, isos[entry.get("virtio_iso", "virtio-win")]["filename"])
    edition = pick(editions_from_iso(_iso_path(facts, win_volid)), entry["edition"])
    ui.ok(f"edition: {edition['name']} (image {edition['index']})")
    password = _password()
    st = facts.storage(a.range_storage)
    local = facts.storage("local") if facts.storage("local") and "iso" in facts.storage("local").content else None
    answer_store = local or facts.storage(a.iso_storage)
    answer_name = f"valor-unattend-{secrets.token_hex(6)}.iso"
    answer_dir = Path(answer_store.path) / "template" / "iso"
    answer_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "autounattend.xml").write_text(autounattend(edition["index"], password))
        (Path(tmp) / "clone-unattend.xml").write_text(clone_unattend(password))
        (Path(tmp) / "valor-setup.ps1").write_text(SETUP_PS1.replace("\n", "\r\n"))
        run(["genisoimage", "-quiet", "-J", "-r", "-V", "VALOR_UNATTEND", "-o", str(answer_dir / answer_name), tmp])
    os.chmod(answer_dir / answer_name, 0o600)
    answer_volid = f"{answer_store.id}:iso/{answer_name}"
    disk_fmt = ",format=qcow2" if st.file_based else ""
    name = "tpl-" + os_name.replace(".", "")
    desc = (f"VALOR {a.id} template for {os_name}: {edition['name']} from {iso_entry['filename']}. Built "
            f"{time.strftime('%Y-%m-%d %H:%M %Z')} (unattended, sysprepped). Managed by the VALOR installer; do not edit.")
    try:
        vmid = _create(a, facts, lambda vmid: ["qm", "create", str(vmid), "--name", name, "--pool", a.pool_templates, "--ostype", "win11",
             "--machine", "q35", "--bios", "ovmf", "--efidisk0", f"{st.id}:1,efitype=4m,pre-enrolled-keys=1{disk_fmt}",
             "--tpmstate0", f"{st.id}:1,version=v2.0", "--cpu", cpu, "--cores", "2", "--memory", "4096",
             "--sata0", f"{st.id}:{DISK_GIB},discard=on,ssd=1{disk_fmt}",
             "--sata1", f"{win_volid},media=cdrom", "--sata2", f"{virtio_volid},media=cdrom",
             "--sata3", f"{answer_volid},media=cdrom", "--boot", "order=sata1;sata0",
             "--net0", f"e1000e,bridge={a.vm_bridge},link_down=1" + (f",tag={a.vm_vlan}" if a.vm_vlan else ""),
             "--agent", "enabled=1", "--vga", "std", "--tablet", "1", "--localtime", "0",
             "--tags", f"valor-template;os-{os_name.replace('.', '-')}", "--description", desc])
        ui.info(f"created Windows template VM {vmid} ({name}) on {st.id}")
        rec.add_template({"os": os_name, "vmid": vmid, "built": True})
        rec.set(f"windows_password_{vmid}", password)       # break-glass for the template only (root-only record)
        run(["qm", "start", str(vmid)])
        # "Press any key to boot from CD or DVD..." - keep pressing a key for the first 40 seconds. A letter, not
        # Enter: a fast setup can already show its progress page, where Enter would press "Cancel".
        for _ in range(40):
            run(["qm", "sendkey", str(vmid), "x"], check=False)
            time.sleep(1)
        ui.info("Windows setup runs unattended (install, drivers, guest agent, sysprep): typically 15-40 minutes")
        t0, end = time.time(), time.time() + 3 * 3600
        last = 0
        while time.time() < end:
            if run(["qm", "status", str(vmid)], check=False).stdout.strip().endswith("stopped"):
                break
            if time.time() - last > 300:
                ui.info(f"... still installing ({(time.time() - t0) / 60:.0f} min)")
                last = time.time()
            time.sleep(15)
        else:
            raise SystemExit(f"Windows template VM {vmid} did not finish within 3 hours; check its console")
        run(["qm", "set", str(vmid), "--delete", "sata1,sata2,sata3", "--boot", "order=sata0",
             "--net0", f"e1000e,bridge={a.vm_bridge}" + (f",tag={a.vm_vlan}" if a.vm_vlan else "")])
        run(["qm", "template", str(vmid)])
    finally:
        (answer_dir / answer_name).unlink(missing_ok=True)
    ui.ok(f"template {vmid} ({os_name}) ready after {(time.time() - t0) / 60:.0f} minutes")
    return vmid
