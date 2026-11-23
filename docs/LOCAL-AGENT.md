# 独立MCP与Agent接入

MCP 源码为 `agent/mcp_server.py`，工具清单为 `agent/tools.json`。当前 MCP `0.13.0` 提供 13 项工具，配套电脑端版本为 `1.18.0-stress-history`。适配器通过stdio工作，访问已经运行的本机仪表盘127.0.0.1:8766/api/tools，不启动手表、连接或采集。详见[MCP契约](MCP-CONTRACT.md)。

在已提交的源码根目录生成独立包，使用自己的Python和输出目录：

```powershell
$projectRoot = (Get-Location).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$destination = Join-Path $projectRoot 'dist\mcp\heartradio'
& $python agent\export_package.py --destination $destination --python $python
& $python (Join-Path $destination 'scripts\check.py')
```

生成目录包含README、runtime.json、VERSION.json和客户端示例。导出不修改正式Agent配置。检查包完整性与工具发现不等于设备在线；不要发送真实提醒来检查工具发现。将包的README交给你的Agent，让它核对当前profile和配置位置、备份并保留已有设置，仅合并heartradio，再由使用者重新连接该MCP。

先调用 `get_capabilities`，按返回的指标许可、窗口、缓存与间隔决定操作。`get_heart_rate` 和默认的 `get_reminder_status` 只读允许的电脑数据。新设备读取、状态观察和提醒需要用户开启 Agent 交互并由两端确认。`get_user_state` 默认只查佩戴，可显式选查睡眠、心率、两路运动和压力点测；运动观测约需 30 秒，活动分类准确率尚未测定。压力需要在腕及手表无障碍许可，会唤醒手表并占用原厂页面。`read_watch_health_now` 查询原厂累计活动、佩戴和最近睡眠；`get_watch_health_history` 只读经授权的电脑归档（含原厂心率及成功的压力点测），手表可离线。未知或失败时避免连续自动重试。

协议允许并发不代表客户端会并发。取消进行中的任务可以从网页或另一客户端发起，必须等待原请求确认释放资源。关闭/超时的客户端不等于设备已释放监听。WSL/容器的localhost和Windows不一定相同，需自行验证路径与stdio通路。

使用云端模型时，工具结果可能随对话发送到模型服务。适配器在本机运行不代表整条链路离线；接入Agent应说明实际模型与去向，不擅自切换配置。
