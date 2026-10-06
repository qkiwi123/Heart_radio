[CmdletBinding()]
param([string]$Serial)
$ErrorActionPreference='Stop'
$watch = & (Join-Path $PSScriptRoot 'Get-Watch.ps1') -Serial $Serial
$Serial = $watch.Serial
$configureRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
& (Join-Path $configureRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'capability_probe.py') provision --serial $Serial
if ($LASTEXITCODE -ne 0) { throw '待命配置写入失败，请检查USB授权、配对配置和已选局域网。' }
Write-Output '待命配置已写入本应用私有目录；尚未开启待命。日常使用无需USB。'
