[CmdletBinding()]
param([string]$Serial, [string]$Adb = 'adb')
$ErrorActionPreference = 'Stop'
$lines = & $Adb devices -l
$candidates = @($lines | Where-Object { $_ -match '^\S+\s+device\s+.*model:OW20W2\b' })
if (!$Serial) {
    if ($candidates.Count -ne 1) { throw 'OW20W2 候选设备不唯一或未授权；请确认 ADB/RSA 并明确指定 -Serial。' }
    $Serial = ($candidates[0] -split '\s+')[0]
}
if ($Serial -notmatch '^[A-Za-z0-9.:_-]+$') { throw '设备序列号格式不合法。' }
if (!($candidates | Where-Object { ($_ -split '\s+')[0] -eq $Serial })) { throw "目标 $Serial 当前未在线或型号未确认；请检查 USB/RSA 后重试。" }
function Read-Property([string]$Name) {
    $value = & $Adb -s $Serial shell getprop $Name
    if ($LASTEXITCODE -ne 0 -or !$value) { throw "读取 $Name 失败；目标连接可能已断开。未执行设备写入。" }
    return ($value -join '').Trim()
}
$model = Read-Property 'ro.product.model'
$brand = Read-Property 'ro.product.brand'
$sdk = Read-Property 'ro.build.version.sdk'
if ($model -ne 'OW20W2' -or $brand -ne 'OPPO' -or $sdk -ne '27') { throw "目标身份不匹配：$brand $model API $sdk" }
[pscustomobject]@{ Serial=$Serial; Adb=$Adb; Package='dev.local.watch2.hrprobe' }
