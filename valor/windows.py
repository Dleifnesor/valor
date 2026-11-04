# *opens a window and peeks out* OwO valor/windows.py is how VALOR looks after Windows VMs! ^w^
# Linux VMs get set up by cloud-init, but Windows doesn't have that here, so VALOR does it itself over the QEMU
# guest agent with PowerShell scripts (run as SYSTEM, always from a FILE, never on a command line where secrets
# could leak). This file writes those scripts:
#   prep_script       name, Administrator password, static IP, DNS, network profile and licensing
#   role_script       wraps a role's role.ps1 with its settings and helper functions
#   baseline_bundle   the PowerShell twin of baseline.bundle() (hardening checks and fixes)
#   probe_script      "can this VM reach that address/port?" for the connectivity tests
# Scripts talk back with marker lines in their output (REBOOT, PREP_OK, VALOR-ROLE-CHANGED, VALOR-CONTROL) *wags tail*
"""Windows range hosts: guest preparation over the QEMU agent (Windows has no cloud-init here), PowerShell role
wrapper, baseline bundle and connectivity probes. Scripts run as SYSTEM through PVE.ps() (file, never a command line).

Scripts talk back to the engine with marker lines in their output: REBOOT asks the engine to restart the VM and
run the script again, PREP_OK confirms the preparation finished, VALOR-ROLE-CHANGED / VALOR-CONTROL report like
their Linux counterparts (roles.py, baseline.py).
"""

# Lazy type hints UwU
from __future__ import annotations

# The marker lines ^w^: REBOOT = "please restart the VM, then run me again"; PREP_OK = "preparation finished".
# PREP_VERSION is part of every Windows host's converge fingerprint: bump it when prep_script changes, and every
# Windows host gets prepared again on its next build >w<
REBOOT = "VALOR-REBOOT-REQUIRED"
PREP_OK = "VALOR-PREP-OK"
PREP_VERSION = 2          # part of a Windows host's converge hash: bump when the preparation changes

# Range VMs are test systems running Windows from evaluation media (no product key). Before an evaluation period runs
# out (an expired one shuts Windows down every hour), Microsoft's rearm starts a new one: Windows Server may rearm 5
# more times (180 days each), Windows 11 once (90 days). Preparation runs this script, and so does a daily task for VMs
# left running between builds; from the task, Windows restarts 10 minutes after a rearm (the new period needs one).
# RENEW_DAYS: rearm when fewer than 14 days of the evaluation period are left. RENEW_TASK: the scheduled task's
# name. RENEW_PS1: the renewal script itself (an rf-string: raw, so backslashes stay, and formatted, so {RENEW_DAYS}
# gets filled in; doubled braces are literal PowerShell braces). It finds the Windows license, and if it's a
# time-based evaluation with too little time left and rearms remain, it rearms (and, from the daily task,
# schedules a restart in 10 minutes), printing REBOOT OwO
RENEW_DAYS = 14
RENEW_TASK = "VALOR evaluation renewal"
RENEW_PS1 = rf"""param([switch]$Restart)
# VALOR: rearm Windows when its evaluation period has less than {RENEW_DAYS} days left
$os = Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" |
  Where-Object {{ -not $_.LicenseIsAddon }} | Select-Object -First 1
$svc = Get-CimInstance SoftwareLicensingService
if ($os.Description -match 'TIMEBASED_EVAL' -and $os.GracePeriodRemaining -lt {RENEW_DAYS * 1440} -and $svc.RemainingWindowsReArmCount -gt 0) {{
  Invoke-CimMethod -InputObject $svc -MethodName ReArmWindows | Out-Null
  if ($Restart) {{ shutdown.exe /r /t 600 /d p:0:0 /c 'VALOR renewed the Windows evaluation period: restarting in 10 minutes.' }}
  '{REBOOT}'
}}
"""


# q: turn any value into a PowerShell single-quoted string. Inside single quotes PowerShell expands NOTHING, and
# a ' is written as '' , so any text (even a password with quotes) is safe ^w^ *guards the quotes*
def q(value) -> str:
    """PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


# prep_script: the preparation script for one Windows host UwU
#   name     the computer name; address/prefix/gateway = its static IPv4; dns = DNS servers
#   password the range login for the Administrator (None = leave it alone)
# It's idempotent (safe to run any number of times) and prints REBOOT when a rename or a rearm needs a restart >w<
def prep_script(name: str, address: str, prefix: int, gateway: str, dns: list[str], password: str | None) -> str:
    """Hostname, Administrator password, static IPv4, DNS, network profile and licensing (no license checks, the
    evaluation renewed). Idempotent; prints REBOOT when the rename or a rearm needs one. DNS stays as a role set it
    (e.g. pointing at a domain controller) once a role took it over."""
    # On a domain controller (DomainRole >= 4) there are no local accounts: the domain's Administrator gets it.
    # The password part (only when there's a password): on a domain controller set the DOMAIN Administrator's
    # password, otherwise the local Administrator's (never expiring, and enabled) OwO
    set_password = (f"$pw = ConvertTo-SecureString {q(password)} -AsPlainText -Force\n"
                    "if ($cs.DomainRole -ge 4) { Import-Module ActiveDirectory; "
                    "Set-ADAccountPassword -Identity Administrator -Reset -NewPassword $pw }\n"
                    "else { Set-LocalUser -Name Administrator -Password $pw -PasswordNeverExpires $true; "
                    "Enable-LocalUser -Name Administrator }\n") if password else ""
    # The script, step by step ^w^ (doubled braces = literal braces; doubled backslashes = single ones in registry paths):
    #   - keep the hardware clock in UTC (Proxmox gives Windows VMs a UTC clock)
    #   - set the password (above)
    #   - find the first physical network card; set the static address and default gateway unless already set
    #   - set DNS servers, unless a role took DNS over (e.g. pointed it at a domain controller)
    #   - set the network profile to Private (retrying, since Windows briefly can't while "Identifying...")
    #   - licensing for test VMs: no online license checks, no activation popups, save RENEW_PS1, register the daily
    #     renewal task at 3 am and run the renewal once now; problems here only print a warning
    #   - rename the computer if needed (not on a DC), printing REBOOT
    #   - print PREP_OK at the end UwU
    return f"""
$cs = Get-CimInstance Win32_ComputerSystem
# the VM's hardware clock is UTC (Proxmox localtime=0): keep it so if someone changes the time zone
New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation' -Name RealTimeIsUniversal `
  -Value 1 -PropertyType DWord -Force | Out-Null
{set_password}
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
  Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ServerAddresses @({', '.join(q(server) for server in dns)})
}}
# Private network profile. Right after an address change Windows shows "Identifying..." and refuses to set it;
# retry for a minute, never fail on it (firewall rules VALOR relies on apply to every profile).
for ($i = 0; $i -lt 12; $i++) {{
  $p = Get-NetConnectionProfile -InterfaceIndex $nic.ifIndex -ErrorAction SilentlyContinue
  if ($p -and $p.NetworkCategory -ne 'Public') {{ break }}
  if ($p) {{ try {{ $p | Set-NetConnectionProfile -NetworkCategory Private -ErrorAction Stop; break }} catch {{ }} }}
  Start-Sleep -Seconds 5
}}
# Licensing for test systems: no online license validation or activation attempts (ranges are often offline), no
# activation notifications, and the evaluation renewed in time (RENEW_PS1). Never fails the preparation.
try {{
  $spp = 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\CurrentVersion\\Software Protection Platform'
  if (-not (Test-Path $spp)) {{ New-Item -Path $spp -Force | Out-Null }}
  New-ItemProperty -Path $spp -Name NoGenTicket -Value 1 -PropertyType DWord -Force | Out-Null
  $act = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SoftwareProtectionPlatform\\Activation'
  if (-not (Test-Path $act)) {{ New-Item -Path $act -Force | Out-Null }}
  New-ItemProperty -Path $act -Name Manual -Value 1 -PropertyType DWord -Force | Out-Null
  New-ItemProperty -Path $act -Name NotificationDisabled -Value 1 -PropertyType DWord -Force | Out-Null
  $dir = Join-Path $env:ProgramData 'VALOR'
  if (-not (Test-Path $dir)) {{ New-Item -ItemType Directory -Path $dir | Out-Null }}
  $renew = Join-Path $dir 'renew-evaluation.ps1'
  Set-Content -LiteralPath $renew -Value {q(RENEW_PS1)} -Encoding ascii
  $run = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$renew`" -Restart"
  Register-ScheduledTask -TaskName {q(RENEW_TASK)} -Action $run -Trigger (New-ScheduledTaskTrigger -Daily -At 3am) `
    -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
  & $renew
}} catch {{ "VALOR-WARNING licensing: $_" }}
if ($cs.DomainRole -lt 4 -and $env:COMPUTERNAME -ne {q(name.upper())}) {{
  Rename-Computer -NewName {q(name)} -Force -WarningAction SilentlyContinue
  '{REBOOT}'
}}
'{PREP_OK}'
"""


# PS_PRELUDE: helper functions every Windows role can use *hands over a toolbox* >w<
#   Changed               note that the role changed something (reported as VALOR-ROLE-CHANGED=1)
#   Write-RebootRequired  ask VALOR for a restart
#   Set-DnsByRole         point DNS somewhere (e.g. a DC) and stop prep_script from resetting it
#   Open-TcpPort          open an inbound TCP port in Windows Firewall
#   Wait-Port / Wait-Until  wait until a port listens / a condition is true (with a timeout)
#   Get-Words             split a string into words (for list parameters)
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
  if (-not (Test-Path 'HKLM:\SOFTWARE\VALOR')) { New-Item -Path 'HKLM:\SOFTWARE\VALOR' | Out-Null }
  New-ItemProperty -Path 'HKLM:\SOFTWARE\VALOR' -Name DnsByRole -Value ($servers -join ',') -Force | Out-Null
}
function Open-TcpPort([int]$port) {
  # Allow inbound TCP to a port in Windows Firewall (every profile)
  $n = "VALOR-TCP-$port"
  if (-not (Get-NetFirewallRule -Name $n -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $n -DisplayName "VALOR TCP $port" -Direction Inbound -Protocol TCP -LocalPort $port `
      -Action Allow -Profile Any | Out-Null; Changed
  }
}
function Wait-Port([int]$port, [int]$seconds = 120) {
  Wait-Until { Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue } $seconds "TCP port $port"
}
function Get-Words([string]$s) { @(($s -split '\s+') | Where-Object { $_ }) }
function Wait-Until([scriptblock]$test, [int]$seconds = 600, [string]$what = 'condition') {
  $end = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $end) { try { if (& $test) { return } } catch {} ; Start-Sleep -Seconds 5 }
  throw "timed out waiting for $what"
}
"""


# role_script: one role's full script: its settings as PowerShell variables ($VALOR_HOST = '...', safely quoted),
# then the helpers, then the role's own code in a try/finally so VALOR-ROLE-CHANGED is printed even when the role
# throws an error OwO
def role_script(name: str, script: str, env: dict[str, str]) -> str:
    """A role.ps1 with its environment as PowerShell variables ($VALOR_HOST = '...') and the PS_PRELUDE helpers.
    The finally block reports VALOR-ROLE-CHANGED even when the role throws."""
    # One "$NAME = 'value'" line per setting, sorted so the script is the same every time ^w^
    assignments = "\n".join(f"${key} = {q(value)}" for key, value in sorted(env.items()))
    return (f"{assignments}\n{PS_PRELUDE}\n# ---- role {name} ----\n"
            f"try {{\n{script}\n}} finally {{ \"VALOR-ROLE-CHANGED=$([int]$VALOR_CHANGED)\" }}\n")


# baseline_bundle: like baseline.bundle() but in PowerShell. Prints "VALOR-CONTROL <id> <before> <after>" per
# control UwU
def baseline_bundle(baseline, is_router: bool, *, fix: bool) -> str:
    """PowerShell counterpart of baseline.bundle(): prints 'VALOR-CONTROL <id> <before> <after>' per control."""
    # Preamble: keep going after errors; $IsDC tells controls if this is a domain controller; Invoke-Check runs a
    # check and turns any result (or an exception) into true/false >w<
    parts = ["$ErrorActionPreference = 'Continue'",
             "$IsDC = (Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4",
             "function Invoke-Check([scriptblock]$c) { try { [bool](& $c) } catch { $false } }"]
    # For each control that applies to this host... OwO
    for control in baseline.controls:
        if not control.applies(is_router):
            continue
        # ...store its check and fix as script blocks, check once, and (when fixing) run the fix only if the check failed,
        # silencing its output, then check again ^w^
        parts.append(f"$check = {{\n{control.check.strip()}\n}}")
        parts.append(f"$fix = {{\n{control.fix.strip()}\n}}")
        parts.append("$before = Invoke-Check $check; $after = $before")
        if fix:
            parts.append("if (-not $before) { try { & $fix *> $null } catch {} ; $after = Invoke-Check $check }")
        # Print the marker line with pass/fail before and after UwU
        parts.append(f"\"VALOR-CONTROL {control.id} $(if ($before) {{ 'pass' }} else {{ 'fail' }}) "
                     f"$(if ($after) {{ 'pass' }} else {{ 'fail' }})\"")
    return "\n".join(parts) + "\n"


# probe_script: can this VM reach `ip` (with ICMP ping, or TCP to `port`)? Prints 0 = reached, 124 = timed out
# (probably filtered by the firewall), 1 = refused/unreachable; the same codes as the Linux probe >w<
def probe_script(proto: str, ip: str, port: int | None) -> str:
    """Prints 0 (reached), 124 (timed out / filtered), 1 (refused) - the same codes as the Linux probe."""
    # ICMP: one ping OwO
    if proto == "icmp":
        return f"if (Test-Connection -ComputerName {q(ip)} -Count 1 -Quiet) {{ '0' }} else {{ '1' }}"
    # TCP: start a connection and wait up to 5 s ^w^ *taps the port*
    return f"""
$c = New-Object System.Net.Sockets.TcpClient
try {{
  $t = $c.ConnectAsync({q(ip)}, {int(port)})
  if (-not $t.Wait(5000)) {{ '124' }} elseif ($c.Connected) {{ '0' }} else {{ '1' }}
}} catch {{ if ($_.Exception.InnerException -and $_.Exception.InnerException.Message -match 'refused') {{ '1' }} else {{ '1' }} }}
finally {{ $c.Close() }}
"""
