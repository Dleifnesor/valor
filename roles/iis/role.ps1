# *dons a tiny Windows hat* OwO roles/iis/role.ps1: installs IIS with a page naming the host and range. Runs as
# SYSTEM with VALOR's helpers. Idempotent ^w^
# Role: iis - idempotent.
# Install the Web-Server feature if missing UwU
if (-not (Get-WindowsFeature -Name Web-Server).Installed) { Install-WindowsFeature -Name Web-Server | Out-Null; Changed }
# *writes the page* only when its content differs >w<
$page = "<!doctype html><title>$VALOR_HOST - $VALOR_RANGE</title><h1>$VALOR_HOST</h1><p>VALOR range <b>$VALOR_RANGE</b>, segment $VALOR_SEGMENT ($VALOR_ADDRESS).</p>"
$path = 'C:\inetpub\wwwroot\index.html'
if (-not (Test-Path $path) -or (Get-Content $path -Raw) -ne $page) { Set-Content -Path $path -Value $page -NoNewline -Encoding ASCII; Changed }
# Enable the HTTP inbound firewall rule, make sure the web service runs, and wait up to 2 minutes for port 80 OwO
Get-NetFirewallRule -Name 'IIS-WebServerRole-HTTP-In-TCP' -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -ne 'True' } | Enable-NetFirewallRule
if ((Get-Service W3SVC).Status -ne 'Running') { Start-Service W3SVC }
Wait-Until { (Test-NetConnection -ComputerName 127.0.0.1 -Port 80 -WarningAction SilentlyContinue).TcpTestSucceeded } 120 'IIS'
