# *checks its tiny watch* OwO roles/ntp-client/role.ps1: takes this Windows host's time from the range's time
# server (overriding the domain hierarchy). Idempotent ^w^
# Role: ntp-client - idempotent.
# The server's address, with 0x8 = "client mode" for w32time UwU
$server = $VALOR_PARAM_SERVER -replace '/32$', ''
$want = "$server,0x8"
# The current w32time settings and service >w<
$p = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters'
$svc = Get-Service w32time
# *winds the clock* the time service starts automatically and runs OwO
if ($svc.StartType -ne 'Automatic') { Set-Service w32time -StartupType Automatic; Changed }
if ($svc.Status -ne 'Running') { Start-Service w32time; Changed }
# Point it at our server only when it isn't already, then restart it ^w^
if ($p.NtpServer -ne $want -or $p.Type -ne 'NTP') {
  w32tm /config /manualpeerlist:$want /syncfromflags:manual /update | Out-Null
  Restart-Service w32time
  Changed
}
# Ask for a sync now (without waiting for it) UwU
w32tm /resync /nowait | Out-Null
