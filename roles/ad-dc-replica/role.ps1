# Role: ad-dc-replica - promote this server to an additional domain controller. Idempotent; one reboot.
$domain = $VALOR_PARAM_DOMAIN
$dcip = $VALOR_PARAM_DC -replace '/32$', ''
foreach ($f in 'AD-Domain-Services', 'DNS') {
  if (-not (Get-WindowsFeature -Name $f).Installed) { Install-WindowsFeature -Name $f -IncludeManagementTools | Out-Null; Changed }
}
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.DomainRole -lt 4) {
  Set-DnsByRole @($dcip)
  Wait-Until { (Resolve-DnsName -Name $domain -ErrorAction Stop -QuickTimeout) -and
               (Test-NetConnection -ComputerName $dcip -Port 389 -WarningAction SilentlyContinue).TcpTestSucceeded } 900 "the domain controller $dcip"
  $pw = ConvertTo-SecureString $VALOR_LOGIN_PASSWORD -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential ("$domain\Administrator", $pw)
  Import-Module ADDSDeployment
  Install-ADDSDomainController -DomainName $domain -Credential $cred -SafeModeAdministratorPassword $pw -InstallDns `
    -Force -NoRebootOnCompletion -WarningAction SilentlyContinue | Out-Null
  Changed
  Write-RebootRequired
  return
}
if ($cs.Domain -ne $domain) { throw "this domain controller belongs to $($cs.Domain), not $domain" }
Wait-Until { Get-ADDomainController -Identity $env:COMPUTERNAME } 900 'Active Directory'
Set-DnsByRole @('127.0.0.1', $dcip)
