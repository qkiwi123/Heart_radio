# 手表数据 MCP：由你的 Agent 自行接入

这是 HeartRadio 的独立 stdio MCP 接入包。用户先通过自己配置的桌面01入口及网页准备电脑服务，在手表分别开启心率广播、待命通道，然后手动启动自己的 Agent，让它阅读本文件并完成接入。导出本目录不会修改 Hermes 或其他 Agent 的配置；无需每次重新安装。

你可以直接给 Hermes 这段要求：

> 请阅读 D:\AIWorkplace\MCP\heartradio\README.md，将这个手表数据 MCP 接入你当前使用的配置。先核对运行环境、配置位置和 profile，备份并保留已有配置，只添加 heartradio；检查工具发现，再读取设备状态。不要自动启动或修改电脑及手表采集服务，无法连接时说明缺少哪一步。模型在云端时先明确数据去向。

## 给接入 Agent 的步骤

1. 读取 `runtime.json` 与 `VERSION.json`，确认文件路径、Python及电脑服务端口。运行 `runtime.json` 中的 Python，参数为本目录 `scripts/check.py`。这一步只检查文件完整性、MCP 握手、六项工具和电脑能力清单，不启动采集，不请求新测量。`packageValid:true` 与 `localServiceAvailable:true` 是不同结论；电脑服务关闭时前者仍可通过。
2. 核对你自己的 MCP 配置格式。Hermes 使用 YAML `mcp_servers`，示例为 `clients/hermes.yaml.example`，等价 JSON 片段为 `clients/hermes.json`；使用 `mcpServers` 格式的客户端参考 `clients/generic.json.example`。不要把一种格式原样写入另一客户端。实际绝对命令和参数以这些生成文件为准。
3. 确认正在使用的 profile、配置文件和运行环境，备份原文件，只合并 `heartradio` 项，保留其他 MCP、模型和用户设置。Hermes 原生 Windows 的默认路径可能是 `%LOCALAPPDATA%\hermes\config.yaml`，其他安装可能是 `~/.hermes/config.yaml`；以自己的实际环境为准，不根据示例猜测。
4. 重启本 Agent 会话，或使用已支持的 `/reload-mcp`。Hermes 可用 `hermes mcp test heartradio` 验证连接。工具集名称为 `mcp-heartradio`，具体发现后的工具名称由客户端决定。
5. 先调用 `get_capabilities`，再读取 `get_heart_rate` 与 `get_device_status`。佩戴有效、原心率广播开启并收到数据时才能取得有效心率；按需设备状态及测量要求电脑按需服务与手表待命同时开启。需要验证新测量时，明确请求一次4秒气压或加速度，观察 `ok/status`、单位、缓存年龄与清理结果。遵守仪表盘保存的指标开关和间隔；失败不编造数值或连续重试。

普通 stdio 客户端按配置启动 `server.py` 这个 Python 子进程，通过标准输入/输出交换 MCP 消息。MCP 是通信协议；这个文件是本项目的服务入口。安装通常指配置并连接该进程，无需另装一套通用“MCP软件”。适配器只需 Python 标准库，不需 API key，不读取手表配对凭据；不要手动打开 `server.py` 等待它显示网页。

## 可选 Hermes 合并工具

`scripts/configure_hermes.py` 供接入 Agent 自主选择使用，不会自动执行。必须使用 Hermes 自己的 Python，并传入真实的 Hermes 源码目录和当前配置的绝对路径；它借用该安装已有的 YAML 编辑库，保留注释、引号和其他配置。默认只预览；明确加入 `--apply` 才修改，修改前生成同目录备份。重复添加同一配置返回 `unchanged`；`--action remove --apply` 只移除本项。备份可能包含原配置的秘密，留在原配置目录，不复制到本 MCP 目录或项目。

```powershell
# 先由 Agent 查明下面三个路径，再替换示例；不要猜当前 profile。
& 'Hermes实际Python绝对路径' -I 'D:\AIWorkplace\MCP\heartradio\scripts\configure_hermes.py' --hermes-source 'Hermes实际源码绝对目录' --config '当前config.yaml绝对路径'
# 确认预览后，同一命令添加 --apply；移除时再添加 --action remove。
```

也可以使用 Agent 原有的配置编辑能力或 Hermes 自己的 MCP 管理命令。不支持 MCP 的 Agent 可以经项目的本机 HTTP 工具入口接入，见项目 `docs/LOCAL-AGENT.md`。

## 六项工具与数据含义

| 工具 | 行为 |
| --- | --- |
| `get_capabilities` | 查询已验证指标及限制，不请求手表新采集。 |
| `get_heart_rate` | 读取原广播的有效缓存，`max_age_seconds` 为1～30秒；不开第二路心率监听。 |
| `get_device_status` | 按需读取电量、充电和连接信息；`fresh:true` 绕过允许的状态缓存。 |
| `measure_pressure` | 1～10秒标准气压，单位 hPa；默认窗口以仪表盘保存设置为准。 |
| `measure_motion` | 1～10秒加速度或陀螺仪原始数据，单位 m/s²、rad/s；不推断步数或活动类型。 |
| `cancel_measurement` | 可带当前测量的 `request_id`，省略时捕获当前测量一次；不停止待命或心率。 |

完整参数见 `tools.json`。结果中的 `receiptAgeSeconds`、`cacheAgeSeconds` 是电脑接收/缓存年龄，`sourceAgeVerified:false` 表明源采样年龄没有独立标定。旧结果必须作为缓存说明，不能称为当前新测量。离线、无接触、过期返回空值或明确状态；不以0代替。气压与运动只在监听及短时唤醒已释放后提供有效新数值。

常见状态：`local_service_unavailable` 表示仪表盘工具入口不可访问；`bridge_unavailable`/`watch_offline` 表示按需通道未就绪；`busy` 表示已有测量；`rate_limited` 应等待返回的重试时间；`disabled_by_settings` 表示指标被网页关闭。心率可因 `no_contact`、`stale` 等状态不可用，应报告实际返回的状态。

多个 Agent 可各自启动适配器，共享同一电脑数据层的缓存、唯一测量占用和15～300秒间隔。`fresh:true` 不能绕过限制。当前 stdio 顺序处理；本机 Hermes 版本也在同一连接上串行调用，故配置保持 `supports_parallel_tool_calls:false`。测量期间要即时取消，请用网页或另一客户端；取消成功不代表原请求已清理，要等原测量结果确认。单次窗口上限10秒，客户端超时60秒包含通信和清理预算。当前不宣称模型能在自己尚未返回的同一条工具调用里再发取消。

当前默认工具不包含 GPS、计步、血氧、心电和厂商原始佩戴通道。位置采集保留诊断入口，坐标读取仍需在目标设备上验证，未列入默认工具。不得从未验证原始通道推算健康或位置数据。

0.3.1起，Windows适配器启动时在源项目`runs/mcp-adapters`登记自己的进程编号、创建时间、解释器与入口文件；结束时删除自己的登记，不存工具参数、健康数据或凭据。原05～08资源入口可据此统计MCP适配器，Agent本体及模型进程不算入项目。正在运行的旧版本不会自动登记，需由用户在Agent里重新连接本服务；开发侧不修改正式Agent配置。异常结束留下的旧登记由资源工具按创建时间排除，不会误算复用编号的其他进程。

## 日常操作与数据去向

继续使用原桌面01～08和网页设置。02只查看，03结束本项目电脑服务，04只停心率接收。Agent 启动 MCP 不自动开启电脑接收或手表待命，停止服务后也不自动重启它们。恢复电脑服务后适配器可再次读取；不用重新配置本目录。

手表左、右按钮分别控制心率、待命，两项可独立开关。待命默认30分钟，支持不限时、自定义时间及勾选保存“跟随电脑设置”。时长由已有仪表盘/手表设置控制，MCP 无修改时长工具。电脑与手表须处于可互通的同一局域网；同已配对电脑的发现与重连不保证跨网络隔离或互联网远程访问。

本目录不存健康日志或配对凭据，数据仍在源项目的既有位置。Agent 在本机运行不等于模型也在本机：如果 Agent 使用云端模型，工具结果可能随对话进入该提供方。接入 Agent 应解释自己的实际配置和去向，不擅自切换模型。

Windows 原生客户端直接使用生成命令；WSL/容器须实测路径、进程和数据入口可达性。不要把 Linux 的 `127.0.0.1` 当作 Windows 的同一地址。优先考虑从该环境启动 Windows Python 的 stdio 路线，但本包尚未验收这类环境。

## 升级、移除与搬家

部署包来源是自己的源码目录（下例使用 `D:\Android\heartradio`），部署包由 `agent/export_package.py` 生成。升级在源码提交、检查后执行下面命令；生成器不会注册 Agent，更新前保存旧包到本目录 `backups`，最后发布带逐文件哈希的 `VERSION.json`。导出覆盖的是生成文件，个人说明请另存文件，别直接修改生成 README 或代码。

```powershell
& 'D:\Python\python.exe' 'D:\Android\heartradio\agent\export_package.py' --destination 'D:\AIWorkplace\MCP\heartradio' --python 'D:\Python\python.exe'
& 'D:\Python\python.exe' 'D:\AIWorkplace\MCP\heartradio\scripts\check.py'
```

项目、Python或部署目录搬家后，重新导出到实际路径并让各 Agent 更新本服务配置。移除时先让 Agent 从自身配置移除 `heartradio`、重新加载连接；不必删除数据或停止心率，也不要还原整个旧配置覆盖其他新增项。检查失败先根据返回状态核对文件、端口与服务，再看网页；不要通过自动开采集掩盖故障。
