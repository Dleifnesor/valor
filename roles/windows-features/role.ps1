# *flips Windows switches* OwO roles/windows-features/role.ps1: installs Windows features and capabilities,
# starts services and opens ports. Runs as SYSTEM with VALOR's helpers. Idempotent ^w^
# Role: windows-features - idempotent.
# Windows Server or client Windows? Each has its own feature commands UwU
$isServer = (Get-CimInstance Win32_OperatingSystem).ProductType -ne 1
$reboot = $false
# *installs each feature* Server: Install-WindowsFeature; client: Enable-WindowsOptionalFeature. Unknown names
# fail, and any that needs a restart is remembered >w<
foreach ($f in Get-Words $VALOR_PARAM_FEATURES) {
  if ($isServer) {
    $wf = Get-WindowsFeature -Name $f
    if (-not $wf) { throw "unknown Windows feature '$f' (see Get-WindowsFeature)" }
    if (-not $wf.Installed) {
      $r = Install-WindowsFeature -Name $f -IncludeManagementTools
      if (-not $r.Success) { throw "installing the Windows feature $f failed" }
      if ("$($r.RestartNeeded)" -eq 'Yes') { $reboot = $true }
      Changed
    }
  } else {
    $of = Get-WindowsOptionalFeature -Online -FeatureName $f
    if (-not $of) { throw "unknown optional feature '$f' (see Get-WindowsOptionalFeature -Online)" }
    if ($of.State -ne 'Enabled') {
      $r = Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart
      if ($r.RestartNeeded) { $reboot = $true }
      Changed
    }
  }
}
# Capabilities (like OpenSSH.Server) come from Windows Update OwO
foreach ($c in Get-Words $VALOR_PARAM_CAPABILITIES) {
  $cap = Get-WindowsCapability -Online -Name $c
  if (-not $cap) { throw "unknown Windows capability '$c' (see Get-WindowsCapability -Online)" }
  if ($cap.State -ne 'Installed') {
    $r = Add-WindowsCapability -Online -Name $c
    if ($r.RestartNeeded) { $reboot = $true }
    Changed
  }
}
# A reboot is needed: ask VALOR for it; the role runs again afterwards and finishes the rest ^w^
if ($reboot) { Write-RebootRequired; return }
# *wakes up the services* set each to Automatic and start it UwU
foreach ($s in Get-Words $VALOR_PARAM_SERVICES) {
  $svc = Get-Service -Name $s
  if ($svc.StartType -ne 'Automatic') { Set-Service -Name $s -StartupType Automatic; Changed }
  if ($svc.Status -ne 'Running') { Start-Service -Name $s; Changed }
}
# Open each port in Windows Firewall and wait for it *wags tail*
foreach ($p in Get-Words $VALOR_PARAM_PORTS) { Open-TcpPort $p; Wait-Port $p }
