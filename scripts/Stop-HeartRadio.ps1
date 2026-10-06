[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference='Stop'
if ($PSCmdlet.ShouldProcess('本项目电脑接收器、按需服务和仪表盘','停止')) {
    & "$PSScriptRoot\Stop-Receiver.ps1"
    $taskBridgeError=$null
    try { & "$PSScriptRoot\Capability-Service.ps1" -Action stop }
    catch { $taskBridgeError=$_.Exception.Message }
    & "$PSScriptRoot\Stop-Dashboard.ps1" -Confirm:$false
    if ($taskBridgeError) { throw "接收器和仪表盘已请求停止，但按需服务停止失败：$taskBridgeError" }
    Write-Output '电脑端已请求停止，已有数据保留。手表采集请在手表点击“关闭心率广播”。'
}
