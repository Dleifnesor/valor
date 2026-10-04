# *perks ears* OwO roles/ad-dc-replica/role.ps1: promotes this Windows Server to an ADDITIONAL domain controller
# of an existing domain. VALOR wraps it with the settings as $VALOR_* variables and its PowerShell helpers
# (Changed, Wait-Until, Set-DnsByRole, Write-RebootRequired...) and runs it as SYSTEM. Idempotent ^w^
# Role: ad-dc-replica - promote this server to an additional domain controller. Idempotent; one reboot.
# The domain, and the existing DC's address (host names arrive as address/32) UwU
$domain = $VALOR_PARAM_DOMAIN
$dcip = $VALOR_PARAM_DC -replace '/32$', ''
# *installs the tools* the AD DS and DNS server features, if missing >w<
foreach ($f in 'AD-Domain-Services', 'DNS') {
  if (-not (Get-WindowsFeature -Name $f).Installed) { Install-WindowsFeature -Name $f -IncludeManagementTools | Out-Null; Changed }
}
# DomainRole 4 or 5 means "already a domain controller" OwO
$cs = Get-CimInstance Win32_ComputerSystem
# Not one yet: ^w^
if ($cs.DomainRole -lt 4) {
  # point DNS at the existing DC (AD finds everything through DNS), then wait up to 15 minutes until the domain
  # resolves and the DC answers on LDAP 389 UwU
  Set-DnsByRole @($dcip)
  Wait-Until { (Resolve-DnsName -Name $domain -ErrorAction Stop -QuickTimeout) -and
               (Test-NetConnection -ComputerName $dcip -Port 389 -WarningAction SilentlyContinue).TcpTestSucceeded } 900 "the domain controller $dcip"
  # *borrows the admin badge* the domain Administrator (its password is the range login) promotes this server,
  # with DNS, without rebooting by itself >w<
  $pw = ConvertTo-SecureString $VALOR_LOGIN_PASSWORD -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential ("$domain\Administrator", $pw)
  Import-Module ADDSDeployment
  Install-ADDSDomainController -DomainName $domain -Credential $cred -SafeModeAdministratorPassword $pw -InstallDns `
    -Force -NoRebootOnCompletion -WarningAction SilentlyContinue | Out-Null
  # Tell VALOR something changed and a reboot is needed; VALOR reboots and runs the role again OwO
  Changed
  Write-RebootRequired
  return
}
# Already a DC: it must be one of THIS domain ^w^
if ($cs.Domain -ne $domain) { throw "this domain controller belongs to $($cs.Domain), not $domain" }
# After the reboot: wait until the directory knows this DC, then use itself first and the other DC second for
# DNS UwU *wags tail*
Wait-Until { Get-ADDomainController -Identity $env:COMPUTERNAME } 900 'Active Directory'
Set-DnsByRole @('127.0.0.1', $dcip)
