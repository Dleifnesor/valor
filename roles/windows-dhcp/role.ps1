# Role: windows-dhcp - idempotent.
if ((Get-CimInstance Win32_OperatingSystem).ProductType -eq 1) { throw 'windows-dhcp needs Windows Server' }
function To-Int([string]$ip) { $b = ([ipaddress]($ip -replace '/32$', '')).GetAddressBytes(); [array]::Reverse($b); [BitConverter]::ToUInt32($b, 0) }
function To-Ip([uint32]$n) { $b = [BitConverter]::GetBytes($n); [array]::Reverse($b); ([ipaddress]$b).ToString() }
$net, $len = $VALOR_SEGMENT_CIDR -split '/'
$len = [int]$len
$mask = To-Ip ([uint32]([math]::Pow(2, 32) - [math]::Pow(2, 32 - $len)))
$first = To-Int $net
$size = [uint32][math]::Pow(2, 32 - $len)
$start = if ($VALOR_PARAM_RANGE_START) { To-Ip (To-Int $VALOR_PARAM_RANGE_START) } elseif ($len -le 24) { To-Ip ($first + 100) } else { To-Ip ($first + [uint32]($size / 2)) }
$end = if ($VALOR_PARAM_RANGE_END) { To-Ip (To-Int $VALOR_PARAM_RANGE_END) } else { To-Ip ([math]::Min((To-Int $start) + 99, $first + $size - 2)) }
$nic = Get-NetAdapter -Physical | Sort-Object ifIndex | Select-Object -First 1
$dns = if ($VALOR_PARAM_DNS) { @(Get-Words $VALOR_PARAM_DNS | ForEach-Object { $_ -replace '/32$', '' }) }
       else { @((Get-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4).ServerAddresses) }

if (-not (Get-WindowsFeature DHCP).Installed) { Install-WindowsFeature DHCP -IncludeManagementTools | Out-Null; Changed }
# lab setting: serve without being authorized in AD (that needs Enterprise Admin rights); mark the setup wizard done
$params = 'HKLM:\SYSTEM\CurrentControlSet\Services\DHCPServer\Parameters'
if ((Get-ItemProperty $params -Name DisableRogueDetection -ErrorAction SilentlyContinue).DisableRogueDetection -ne 1) {
  New-ItemProperty $params -Name DisableRogueDetection -Value 1 -PropertyType DWord -Force | Out-Null; Changed
}
Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\ServerManager\Roles\12' -Name ConfigurationState -Value 2 -ErrorAction SilentlyContinue
if (-not (Get-ItemProperty 'HKLM:\SOFTWARE\VALOR' -Name DhcpGroups -ErrorAction SilentlyContinue)) {
  netsh dhcp add securitygroups | Out-Null              # local groups, or domain groups on a DC
  if (-not (Test-Path 'HKLM:\SOFTWARE\VALOR')) { New-Item -Path 'HKLM:\SOFTWARE\VALOR' | Out-Null }
  New-ItemProperty -Path 'HKLM:\SOFTWARE\VALOR' -Name DhcpGroups -Value 1 -Force | Out-Null
  Changed
}
if ((Get-Service DHCPServer).Status -ne 'Running' -or $VALOR_CHANGED) { Restart-Service DHCPServer }

$scope = Get-DhcpServerv4Scope -ScopeId $net -ErrorAction SilentlyContinue
$lease = New-TimeSpan -Hours ([int]$VALOR_PARAM_LEASE_HOURS)
if (-not $scope) {
  Add-DhcpServerv4Scope -Name "VALOR $VALOR_SEGMENT" -StartRange $start -EndRange $end -SubnetMask $mask `
    -LeaseDuration $lease -State Active
  Changed
} elseif ("$($scope.StartRange)" -ne $start -or "$($scope.EndRange)" -ne $end -or $scope.LeaseDuration -ne $lease) {
  Set-DhcpServerv4Scope -ScopeId $net -StartRange $start -EndRange $end -LeaseDuration $lease
  Changed
}
Set-DhcpServerv4OptionValue -ScopeId $net -Router $VALOR_GATEWAY -DnsServer $dns -Force
Get-NetFirewallRule -Name 'DHCPServer-In-UDP*' -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -ne 'True' } | Enable-NetFirewallRule
Wait-Until { (Get-Service DHCPServer).Status -eq 'Running' } 60 'the DHCP server'
