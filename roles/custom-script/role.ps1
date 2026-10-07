# *hands over the pen* OwO roles/custom-script/role.ps1: runs the spec's OWN PowerShell script with VALOR's helpers
# (Changed, Open-TcpPort, Wait-Port, Wait-Until, Write-RebootRequired) ^w^
# Role: custom-script - runs the spec's own PowerShell script with VALOR's helpers.
# An empty script is a mistake UwU
if (-not "$VALOR_PARAM_SCRIPT".Trim()) { throw 'the script is empty' }
# A marker in the output so the log shows where the custom part starts >w<
"---- custom script on $VALOR_HOST ----"
# *takes a deep breath* turn the text into a script block and dot-source it, so it runs in this scope with the
# helpers OwO
. ([scriptblock]::Create($VALOR_PARAM_SCRIPT))
# VALOR can't know what it did, so it always counts as a change ^w^
Changed
# Open each declared port in Windows Firewall and wait for it UwU
foreach ($p in Get-Words $VALOR_PARAM_PORTS) { Open-TcpPort $p; Wait-Port $p }
