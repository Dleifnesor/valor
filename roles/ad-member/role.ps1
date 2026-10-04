# *knocks on the domain's door* OwO roles/ad-member/role.ps1: joins this Windows host to a domain. Runs as SYSTEM
# with VALOR's $VALOR_* settings and helpers. Idempotent ^w^
# Role: ad-member - join the domain. Idempotent; one reboot after joining.
# The domain and its DC addresses (dc, plus dc2 if given) UwU
$domain = $VALOR_PARAM_DOMAIN
$servers = @($VALOR_PARAM_DC -replace '/32$', '')
if ($VALOR_PARAM_DC2) { $servers += ($VALOR_PARAM_DC2 -replace '/32$', '') }
# Use the DCs for DNS (needed to find the domain) >w<
Set-DnsByRole $servers
# *checks its collar tag* already in this domain: nothing to do; in another one: refuse OwO
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.PartOfDomain -and $cs.Domain -eq $domain) { return }
if ($cs.PartOfDomain) { throw "already joined to $($cs.Domain)" }
# Wait up to 15 minutes until the domain resolves and the first DC answers on LDAP 389 ^w^
Wait-Until { Resolve-DnsName -Name $domain -ErrorAction Stop -QuickTimeout } 900 "DNS for $domain"
Wait-Until { (Test-NetConnection -ComputerName $servers[0] -Port 389 -WarningAction SilentlyContinue).TcpTestSucceeded } 900 'the domain controller'
# Join with the domain Administrator (password = the range login) UwU
$pw = ConvertTo-SecureString $VALOR_LOGIN_PASSWORD -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential ("$domain\Administrator", $pw)
Add-Computer -DomainName $domain -Credential $cred -Force -WarningAction SilentlyContinue
# Changed, and a reboot is needed to finish the join *wags tail*
Changed
Write-RebootRequired
