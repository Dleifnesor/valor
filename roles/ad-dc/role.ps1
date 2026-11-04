# *puts on a tiny crown* OwO roles/ad-dc/role.ps1: creates a brand-new Active Directory forest on this Windows
# Server. VALOR wraps it with $VALOR_* settings and its PowerShell helpers and runs it as SYSTEM. Idempotent ^w^
# Role: ad-dc - first domain controller of a new forest. Idempotent; reboots once after promotion.
# The Directory Services Restore Mode password and the domain Administrator password are the range login.
# The domain name and the NetBIOS name (default: the first label in upper case), both checked UwU
$domain = $VALOR_PARAM_DOMAIN
if ($domain -notmatch '^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+$') { throw "invalid domain name '$domain'" }
$netbios = if ($VALOR_PARAM_NETBIOS) { $VALOR_PARAM_NETBIOS } else { ($domain.Split('.')[0]).ToUpper() }
if ($netbios -notmatch '^[A-Z0-9-]{1,15}$') { throw "invalid NetBIOS name '$netbios'" }

# *installs the tools* AD DS and DNS server features, if missing >w<
foreach ($f in 'AD-Domain-Services', 'DNS') {
  if (-not (Get-WindowsFeature -Name $f).Installed) { Install-WindowsFeature -Name $f -IncludeManagementTools | Out-Null; Changed }
}
# DomainRole below 4 means "not a domain controller yet" OwO
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.DomainRole -lt 4) {
  # A host already joined to some domain can't start a new forest ^w^
  if ($cs.PartOfDomain) { throw "this host is joined to $($cs.Domain); ad-dc builds a new forest on a standalone server" }
  # *plants the forest* promote with the range login as the restore-mode password, with DNS, without rebooting by
  # itself UwU
  $dsrm = ConvertTo-SecureString $VALOR_LOGIN_PASSWORD -AsPlainText -Force
  Import-Module ADDSDeployment
  Install-ADDSForest -DomainName $domain -DomainNetbiosName $netbios -SafeModeAdministratorPassword $dsrm `
    -InstallDns -Force -NoRebootOnCompletion -WarningAction SilentlyContinue | Out-Null
  # Tell VALOR it changed and needs a reboot; after the reboot VALOR runs the role again >w<
  Changed
  Write-RebootRequired
  return
}
# Already a DC: it must be of THIS domain OwO
if ($cs.Domain -ne $domain) { throw "this domain controller belongs to $($cs.Domain), not $domain" }
# After the promotion reboot: wait until the directory answers, then forward other names to VALOR's resolvers
Wait-Until { (Get-ADDomain).DNSRoot -eq $domain } 900 'Active Directory'
Wait-Until { Resolve-DnsName -Name $domain -Server 127.0.0.1 -ErrorAction Stop } 600 'DNS'
# *points outward* forward names outside the domain to VALOR's nameservers, changing the forwarders only when
# they differ ^w^
$want = @($VALOR_NAMESERVERS -split ' ' | Where-Object { $_ })
$have = @((Get-DnsServerForwarder).IPAddress | ForEach-Object { $_.IPAddressToString })
if ($want.Count -and (($have -join ',') -ne ($want -join ','))) { Set-DnsServerForwarder -IPAddress $want -PassThru | Out-Null; Changed }
# The DC uses itself for DNS UwU
Set-DnsByRole @('127.0.0.1')
