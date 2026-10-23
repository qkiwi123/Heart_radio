$ErrorActionPreference='Stop'
try { & "$PSScriptRoot\Connection-Control.ps1" -Action heart_receive_off }
catch { Write-Output '连接管理未运行，继续检查原Wi-Fi接收器。' }
& "$PSScriptRoot\Stop-Receiver.ps1"
Write-Output '已停止电脑心率接收，基础连接和Agent交互保留；手表持续采集需在手表单独关闭。'
