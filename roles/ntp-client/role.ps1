# Role: ntp-client - idempotent.
$server = $VALOR_PARAM_SERVER -replace '/32$', ''
$want = "$server,0x8"
$p = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters'
$svc = Get-Service w32time
if ($svc.StartType -ne 'Automatic') { Set-Service w32time -StartupType Automatic; Changed }
if ($svc.Status -ne 'Running') { Start-Service w32time; Changed }
if ($p.NtpServer -ne $want -or $p.Type -ne 'NTP') {
  w32tm /config /manualpeerlist:$want /syncfromflags:manual /update | Out-Null
  Restart-Service w32time
  Changed
}
w32tm /resync /nowait | Out-Null
