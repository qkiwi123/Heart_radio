# 独立 MCP 目录与本地 Agent

MCP 是 Agent 与工具服务之间的通信协议。本项目的服务入口是 Python 程序，Agent 按自己的 MCP 配置启动它，通过标准输入、输出交换请求；程序再访问本机仪表盘工具接口，电脑通过已配对的 Wi-Fi 通道向手表请求数据。Python 文件只是协议的一种实现方式。

调用路径：`Agent → 独立目录 server.py（stdio MCP）→ 本机电脑数据接口 → 已开启的手表服务`。心率工具直接查询原广播的有效缓存；新测气压及运动数据需要电脑按需服务与手表待命同时开启。

## 导出到自己的管理目录

先确认源码已提交且工作区干净，再从项目目录执行。下面路径为示例，按自己的实际位置替换；目标 Python 必须存在：

```powershell
.\.venv\Scripts\python.exe agent\export_package.py --destination 'D:\AIWorkplace\MCP\heartradio' --python 'D:\Android\heartradio\.venv\Scripts\python.exe'
.\.venv\Scripts\python.exe 'D:\AIWorkplace\MCP\heartradio\scripts\check.py'
```

目录包含 `README.md`、`server.py`、工具定义、客户端示例、运行配置和逐文件哈希版本清单。导出和检查不自动修改 Agent 配置，不启动采集。已管理的包升级时保存旧生成文件到备份目录，个人说明另存文件，避免编辑生成文件。

由用户手动启动 Hermes 或其他 Agent，让它阅读该目录 README，核对当前配置、profile 与运行环境，备份后合并一个 heartradio 项，保留其他设置。适配器只需要 Python 标准库。Hermes 和通用客户端的配置格式可能不同，应采用包内对应示例并由接入 Agent 核对自己的版本；无需开发侧直接安装。

## 六项工具

| 工具 | 返回或行为 |
| --- | --- |
| `get_capabilities` | 指标及限制，不请求新采集。 |
| `get_heart_rate` | 有效心率缓存及接收年龄；最大允许年龄1～30秒。 |
| `get_device_status` | 电量、充电及服务状态。 |
| `measure_pressure` | 1～10秒标准气压，单位hPa。 |
| `measure_motion` | 1～10秒加速度或陀螺仪，单位m/s²或rad/s。 |
| `cancel_measurement` | 取消当前指定测量，不停止心率或待命。 |

完整参数见 [`agent/tools.json`](../agent/tools.json)。`fresh:true` 表示请求新测量，不能绕过网页的指标开关、间隔和唯一测量占用。返回缓存时要说明年龄；失败时报告状态，不编造值。默认工具不包含位置、计步、血氧或心电。

当前 stdio 请求顺序处理，测量期间要立即取消可通过网页或另一客户端。MCP 不提供开启采集及修改时长的工具。多个 Agent 共用电脑数据层的限制，不各自建立手表监听。

不支持 MCP 的 Agent 可以使用 `agent/tool_cli.py` 及本机 `/api/tools` 接口；请求格式以该文件及工具定义为准。Windows 原生、WSL 与容器的路径和 `127.0.0.1` 可能不同，后两者需要另外实测。

Agent 运行在本机不代表模型在本机。使用云端模型时，工具结果可能随会话发送给模型服务方，由用户和接入 Agent 确认实际数据去向。独立包不包含手表配对凭据或个人健康日志。
