"""Windows range hosts: guest preparation over the QEMU agent (Windows has no cloud-init here), PowerShell role
wrapper, baseline bundle and connectivity probes. Scripts run as SYSTEM through PVE.ps() (file, never a command line).
"""

from __future__ import annotations

REBOOT = "VALOR-REBOOT-REQUIRED"
PREP_OK = "VALOR-PREP-OK"
PREP_VERSION = 1          # part of a Windows host's converge hash: bump when the preparation changes


def q(value) -> str:
    """PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def prep_script(name: str, address: str, prefix: int, gateway: str, dns: list[str], password: str | None) -> str:
    """Hostname, Administrator password, static IPv4, DNS and network profile. Idempotent; prints REBOOT when the
    rename needs one. DNS stays as a role set it (e.g. pointing at a domain controller) once a role took it over."""
    pw = (f"$pw = ConvertTo-SecureString {q(password)} -AsPlainText -Force\n"
          "if ($cs.DomainRole -ge 4) { Import-Module ActiveDirectory; "
          "Set-ADAccountPassword -Identity Administrator -Reset -NewPassword $pw }\n"
          "else { Set-LocalUser -Name Administrator -Password $pw -PasswordNeverExpires $true; "
          "Enable-LocalUser -Name Administrator }\n") if password else ""
    return f"""
$cs = Get-CimInstance Win32_ComputerSystem
{pw}
$nic = Get-NetAdapter -Physical | Sort-Object ifIndex | Select-Object -First 1
if (-not $nic) {{ throw 'no network adapter' }}
$ip = {q(address)}; $prefix = {int(prefix)}; $gw = {q(gateway)}
$cur = @(Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue)
if (-not ($cur | Where-Object {{ $_.IPAddress -eq $ip -and $_.PrefixLength -eq $prefix }})) {{
  Set-NetIPInterface -InterfaceIndex $nic.ifIndex -Dhcp Disabled
  $cur | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
  Get-NetRoute -InterfaceIndex $nic.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false
  New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $ip -PrefixLength $prefix -DefaultGateway $gw | Out-Null
}}
if (-not (Test-Path 'HKLM:\\SOFTWARE\\VALOR\\DnsByRole')) {{
  Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ServerAddresses @({', '.join(q(d) for d in dns)})
}}
# Private network profile. Right after an address change Windows shows "Identifying..." and refuses to set it;
# retry for a minute, never fail on it (firewall rules VALOR relies on apply to every profile).
for ($i = 0; $i -lt 12; $i++) {{
  $p = Get-NetConnectionProfile -InterfaceIndex $nic.ifIndex -ErrorAction SilentlyContinue
  if ($p -and $p.NetworkCategory -ne 'Public') {{ break }}
  if ($p) {{ try {{ $p | Set-NetConnectionProfile -NetworkCategory Private -ErrorAction Stop; break }} catch {{ }} }}
  Start-Sleep -Seconds 5
}}
if ($cs.DomainRole -lt 4 -and $env:COMPUTERNAME -ne {q(name.upper())}) {{
  Rename-Computer -NewName {q(name)} -Force -WarningAction SilentlyContinue
  '{REBOOT}'
}}
'{PREP_OK}'
"""


PS_PRELUDE = r"""
$VALOR_CHANGED = $false
function Changed { $script:VALOR_CHANGED = $true }
function Write-RebootRequired { 'VALOR-REBOOT-REQUIRED' }
function Set-DnsByRole([string[]]$servers) {
  # Point the host's DNS at e.g. a domain controller and keep VALOR's preparation from resetting it.
  $nic = Get-NetAdapter -Physical | Sort-Object ifIndex | Select-Object -First 1
  $cur = (Get-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4).ServerAddresses
  if (($cur -join ',') -ne ($servers -join ',')) {
    Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ServerAddresses $servers; Changed
  }
  New-Item -Path 'HKLM:\SOFTWARE\VALOR' -Force | Out-Null
  New-ItemProperty -Path 'HKLM:\SOFTWARE\VALOR' -Name DnsByRole -Value ($servers -join ',') -Force | Out-Null
}
function Wait-Until([scriptblock]$test, [int]$seconds = 600, [string]$what = 'condition') {
  $end = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $end) { try { if (& $test) { return } } catch {} ; Start-Sleep -Seconds 5 }
  throw "timed out waiting for $what"
}
"""


def role_script(name: str, script: str, env: dict[str, str]) -> str:
    vars_ = "\n".join(f"${k} = {q(v)}" for k, v in sorted(env.items()))
    return (f"{vars_}\n{PS_PRELUDE}\n# ---- role {name} ----\n"
            f"try {{\n{script}\n}} finally {{ \"VALOR-ROLE-CHANGED=$([int]$VALOR_CHANGED)\" }}\n")


def baseline_bundle(baseline, is_router: bool, *, fix: bool) -> str:
    """PowerShell counterpart of baseline.bundle(): prints 'VALOR-CONTROL <id> <before> <after>' per control."""
    parts = ["$ErrorActionPreference = 'Continue'",
             "$IsDC = (Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4",
             "function Invoke-Check([scriptblock]$c) { try { [bool](& $c) } catch { $false } }"]
    for c in baseline.controls:
        if not c.applies(is_router):
            continue
        parts.append(f"$check = {{\n{c.check.strip()}\n}}")
        parts.append(f"$fix = {{\n{c.fix.strip()}\n}}")
        parts.append("$before = Invoke-Check $check; $after = $before")
        if fix:
            parts.append("if (-not $before) { try { & $fix *> $null } catch {} ; $after = Invoke-Check $check }")
        parts.append(f"\"VALOR-CONTROL {c.id} $(if ($before) {{ 'pass' }} else {{ 'fail' }}) "
                     f"$(if ($after) {{ 'pass' }} else {{ 'fail' }})\"")
    return "\n".join(parts) + "\n"


def probe_script(proto: str, ip: str, port: int | None) -> str:
    """Prints 0 (reached), 124 (timed out / filtered), 1 (refused) - the same codes as the Linux probe."""
    if proto == "icmp":
        return f"if (Test-Connection -ComputerName {q(ip)} -Count 1 -Quiet) {{ '0' }} else {{ '1' }}"
    return f"""
$c = New-Object System.Net.Sockets.TcpClient
try {{
  $t = $c.ConnectAsync({q(ip)}, {int(port)})
  if (-not $t.Wait(5000)) {{ '124' }} elseif ($c.Connected) {{ '0' }} else {{ '1' }}
}} catch {{ if ($_.Exception.InnerException -and $_.Exception.InnerException.Message -match 'refused') {{ '1' }} else {{ '1' }} }}
finally {{ $c.Close() }}
"""
