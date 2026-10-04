# Role: iis - idempotent.
if (-not (Get-WindowsFeature -Name Web-Server).Installed) { Install-WindowsFeature -Name Web-Server | Out-Null; Changed }
$page = "<!doctype html><title>$VALOR_HOST - $VALOR_RANGE</title><h1>$VALOR_HOST</h1><p>VALOR range <b>$VALOR_RANGE</b>, segment $VALOR_SEGMENT ($VALOR_ADDRESS).</p>"
$path = 'C:\inetpub\wwwroot\index.html'
if (-not (Test-Path $path) -or (Get-Content $path -Raw) -ne $page) { Set-Content -Path $path -Value $page -NoNewline -Encoding ASCII; Changed }
Get-NetFirewallRule -Name 'IIS-WebServerRole-HTTP-In-TCP' -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -ne 'True' } | Enable-NetFirewallRule
if ((Get-Service W3SVC).Status -ne 'Running') { Start-Service W3SVC }
Wait-Until { (Test-NetConnection -ComputerName 127.0.0.1 -Port 80 -WarningAction SilentlyContinue).TcpTestSucceeded } 120 'IIS'
