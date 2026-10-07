# Role: windows-file-share - idempotent.
$name = $VALOR_PARAM_SHARE
if ($name -notmatch '^[A-Za-z0-9_-]{1,32}$') { throw "invalid share name '$name'" }
$path = "C:\Shares\$name"
$readOnly = $VALOR_PARAM_READ_ONLY -eq 'true'
if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null; Changed }
$share = Get-SmbShare -Name $name -ErrorAction SilentlyContinue
if ($share -and $share.Path -ne $path) { Remove-SmbShare -Name $name -Force; $share = $null }
if (-not $share) {
  $p = @{ Name = $name; Path = $path; FullAccess = 'BUILTIN\Administrators' }
  if ($readOnly) { $p.ReadAccess = 'NT AUTHORITY\Authenticated Users' } else { $p.ChangeAccess = 'NT AUTHORITY\Authenticated Users' }
  New-SmbShare @p | Out-Null
  Changed
}
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
Open-TcpPort 445
Wait-Port 445
