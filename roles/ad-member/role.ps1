# Role: ad-member - join the domain. Idempotent; one reboot after joining.
$domain = $VALOR_PARAM_DOMAIN
$servers = @($VALOR_PARAM_DC -replace '/32$', '')
if ($VALOR_PARAM_DC2) { $servers += ($VALOR_PARAM_DC2 -replace '/32$', '') }
Set-DnsByRole $servers
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.PartOfDomain -and $cs.Domain -eq $domain) { return }
if ($cs.PartOfDomain) { throw "already joined to $($cs.Domain)" }
Wait-Until { Resolve-DnsName -Name $domain -ErrorAction Stop -QuickTimeout } 900 "DNS for $domain"
Wait-Until { (Test-NetConnection -ComputerName $servers[0] -Port 389 -WarningAction SilentlyContinue).TcpTestSucceeded } 900 'the domain controller'
$pw = ConvertTo-SecureString $VALOR_LOGIN_PASSWORD -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential ("$domain\Administrator", $pw)
Add-Computer -DomainName $domain -Credential $cred -Force -WarningAction SilentlyContinue
Changed
Write-RebootRequired
