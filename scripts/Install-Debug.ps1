[CmdletBinding()]
param([string]$Serial)
$ErrorActionPreference = 'Stop'
$watch = & "$PSScriptRoot\Get-Watch.ps1" -Serial $Serial
$apk = Join-Path $PSScriptRoot '..\app\build\outputs\apk\debug\app-debug.apk'
if (!(Test-Path $apk)) { throw '请先构建 debug APK。' }
$exists = & $watch.Adb -s $watch.Serial shell pm path $watch.Package
if ($exists) { Write-Output '该独立包名已存在。仅执行保留数据的覆盖安装；签名不符会失败，不卸载或清数据。' }
& $watch.Adb -s $watch.Serial install -r $apk
if ($LASTEXITCODE -ne 0) { throw '安装失败。没有尝试卸载或清数据。' }
& $watch.Adb -s $watch.Serial shell am start -W -n "$($watch.Package)/com.example.heart_radio.MainActivity"
if ($LASTEXITCODE -ne 0) { throw '启动命令失败。' }
