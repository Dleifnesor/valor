# *shares a folder* OwO roles/windows-file-share/role.ps1: an SMB share on Windows at C:\Shares\<name>. Runs as
# SYSTEM with VALOR's helpers. Idempotent ^w^
# Role: windows-file-share - idempotent.
# The share name (checked), its folder, and whether users may only read UwU
$name = $VALOR_PARAM_SHARE
if ($name -notmatch '^[A-Za-z0-9_-]{1,32}$') { throw "invalid share name '$name'" }
$path = "C:\Shares\$name"
$readOnly = $VALOR_PARAM_READ_ONLY -eq 'true'
# Create the folder if missing >w<
if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null; Changed }
# *checks the existing share* one with the wrong path is removed and made again OwO
$share = Get-SmbShare -Name $name -ErrorAction SilentlyContinue
if ($share -and $share.Path -ne $path) { Remove-SmbShare -Name $name -Force; $share = $null }
# New share: full control for Administrators, change or read access for authenticated users ^w^
if (-not $share) {
  $p = @{ Name = $name; Path = $path; FullAccess = 'BUILTIN\Administrators' }
  if ($readOnly) { $p.ReadAccess = 'NT AUTHORITY\Authenticated Users' } else { $p.ChangeAccess = 'NT AUTHORITY\Authenticated Users' }
  New-SmbShare @p | Out-Null
  Changed
}
# *fixes the permissions* the authenticated users' share right must be what's wanted UwU
$want = if ($readOnly) { 'Read' } else { 'Change' }
$acc = Get-SmbShareAccess -Name $name | Where-Object { $_.AccountName -eq 'NT AUTHORITY\Authenticated Users' }
if ("$($acc.AccessRight)" -ne $want) {
  if ($acc) { Revoke-SmbShareAccess -Name $name -AccountName 'NT AUTHORITY\Authenticated Users' -Force | Out-Null }
  Grant-SmbShareAccess -Name $name -AccountName 'NT AUTHORITY\Authenticated Users' -AccessRight $want -Force | Out-Null
  Changed
}
# NTFS permissions to match (S-1-5-11 = Authenticated Users)
$right = if ($readOnly) { 'RX' } else { 'M' }
icacls $path /grant "*S-1-5-11:(OI)(CI)$right" | Out-Null
# Open SMB 445 in Windows Firewall and wait for it >w<
Open-TcpPort 445
Wait-Port 445
