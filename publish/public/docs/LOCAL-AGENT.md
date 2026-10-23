# 独立MCP与Agent接入

MCP源码为agent/mcp_server.py，工具清单为agent/tools.json，当前0.7.0/11工具。适配器通过stdio工作，访问已经运行的本机仪表盘127.0.0.1:8766/api/tools，不启动手表、连接或采集。详见[MCP契约](MCP-CONTRACT.md)。

在已提交的源码根目录生成独立包，使用自己的Python和输出目录：

```powershell
$projectRoot = (Get-Location).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$destination = Join-Path $projectRoot 'dist\mcp\heartradio'
& $python agent\export_package.py --destination $destination --python $python
& $python (Join-Path $destination 'scripts\check.py')
```

生成目录包含README、runtime.json、VERSION.json和客户端示例。导出不修改正式Agent配置。检查包完整性与工具发现不等于设备在线；不要发送真实提醒来检查工具发现。将包的README交给你的Agent，让它核对当前profile和配置位置、备份并保留已有设置，仅合并heartradio，再由使用者重新连接该MCP。

先调用get_capabilities，按返回的指标许可、窗口、缓存与间隔决定操作。get_heart_rate和默认get_reminder_status只读已有允许的电脑数据；新设备读取、30秒状态观察和提醒需要用户开启且两端确认的Agent交互。未知或失败不连续自动重试。用户状态结果是未验证的弱推测，不是睡眠/佩戴或清醒测量。

协议允许并发不代表客户端会并发。取消进行中的任务可以从网页或另一客户端发起，必须等待原请求确认释放资源。关闭/超时的客户端不等于设备已释放监听。WSL/容器的localhost和Windows不一定相同，需自行验证路径与stdio通路。

使用云端模型时，工具结果可能随对话发送到模型服务。适配器在本机运行不代表整条链路离线；接入Agent应说明实际模型与去向，不擅自切换配置。
