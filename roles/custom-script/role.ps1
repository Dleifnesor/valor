# Role: custom-script - runs the spec's own PowerShell script with VALOR's helpers.
if (-not "$VALOR_PARAM_SCRIPT".Trim()) { throw 'the script is empty' }
"---- custom script on $VALOR_HOST ----"
. ([scriptblock]::Create($VALOR_PARAM_SCRIPT))
Changed
foreach ($p in Get-Words $VALOR_PARAM_PORTS) { Open-TcpPort $p; Wait-Port $p }
