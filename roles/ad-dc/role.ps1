# Role: ad-dc - first domain controller of a new forest. Idempotent; reboots once after promotion.
# The Directory Services Restore Mode password and the domain Administrator password are the range login.
$domain = $VALOR_PARAM_DOMAIN
if ($domain -notmatch '^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$') { throw "invalid domain name '$domain'" }
$netbios = if ($VALOR_PARAM_NETBIOS) { $VALOR_PARAM_NETBIOS } else { ($domain.Split('.')[0]).ToUpper() }
if ($netbios -notmatch '^[A-Z0-9-]{1,15}$') { throw "invalid NetBIOS name '$netbios'" }

foreach ($f in 'AD-Domain-Services', 'DNS') {
  if (-not (Get-WindowsFeature -Name $f).Installed) { Install-WindowsFeature -Name $f -IncludeManagementTools | Out-Null; Changed }
}
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.DomainRole -lt 4) {
  if ($cs.PartOfDomain) { throw "this host is joined to $($cs.Domain); ad-dc builds a new forest on a standalone server" }
  $dsrm = ConvertTo-SecureString $VALOR_LOGIN_PASSWORD -AsPlainText -Force
  Import-Module ADDSDeployment
  Install-ADDSForest -DomainName $domain -DomainNetbiosName $netbios -SafeModeAdministratorPassword $dsrm `
    -InstallDns -Force -NoRebootOnCompletion -WarningAction SilentlyContinue | Out-Null
  Changed
  Write-RebootRequired
  return
}
if ($cs.Domain -ne $domain) { throw "this domain controller belongs to $($cs.Domain), not $domain" }
# After the promotion reboot: wait until the directory answers, then forward other names to VALOR's resolvers
Wait-Until { (Get-ADDomain).DNSRoot -eq $domain } 900 'Active Directory'
Wait-Until { Resolve-DnsName -Name $domain -Server 127.0.0.1 -ErrorAction Stop } 600 'DNS'
$want = @($VALOR_NAMESERVERS -split ' ' | Where-Object { $_ })
$have = @((Get-DnsServerForwarder).IPAddress | ForEach-Object { $_.IPAddressToString })
if ($want.Count -and (($have -join ',') -ne ($want -join ','))) { Set-DnsServerForwarder -IPAddress $want -PassThru | Out-Null; Changed }
Set-DnsByRole @('127.0.0.1')
