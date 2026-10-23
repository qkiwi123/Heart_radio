# 手表数据与提醒 MCP：由你的 Agent 自行接入

这是 HeartRadio 0.7.0的独立 stdio MCP 接入包，共十一项工具，含一次30秒的三档用户状态粗略判断。电脑使用 `项目提供的桌面入口` 的01，手表打开应用，按保存策略选择已配对蓝牙或原Wi-Fi；连接、持续心率和Agent交互是三项独立控制。需要新设备读取、按需测量、状态判断或提醒时，用户在任意一端开启同一个Agent交互开关，等待“两端已同步”。持续心率另由用户开启。导出本目录不会修改Hermes或其他Agent的配置。

你可以直接给 Hermes 这段要求：

> 请阅读 <MCP输出目录>\README.md，将这个手表数据 MCP 接入你当前使用的配置。先核对运行环境、配置位置和 profile，备份并保留已有配置，只添加 heartradio；检查工具发现，再读取设备状态。不要自动启动或修改电脑及手表采集服务，无法连接时说明缺少哪一步。模型在云端时先明确数据去向。

## 给接入 Agent 的步骤

1. 读取 `runtime.json` 与 `VERSION.json`，确认文件路径、Python及电脑服务端口。运行 `runtime.json` 中的 Python，参数为本目录 `scripts/check.py`。这一步只检查文件完整性、MCP握手、十一项工具和电脑能力清单，不请求新测量或发送提醒。`packageValid:true` 与 `localServiceAvailable:true` 是不同结论；电脑服务关闭时前者仍可通过。
2. 核对你自己的 MCP 配置格式。Hermes 使用 YAML `mcp_servers`，示例为 `clients/hermes.yaml.example`，等价 JSON 片段为 `clients/hermes.json`；使用 `mcpServers` 格式的客户端参考 `clients/generic.json.example`。不要把一种格式原样写入另一客户端。实际绝对命令和参数以这些生成文件为准。
3. 确认正在使用的 profile、配置文件和运行环境，备份原文件，只合并 `heartradio` 项，保留其他 MCP、模型和用户设置。Hermes 原生 Windows 的默认路径可能是 `%LOCALAPPDATA%\hermes\config.yaml`，其他安装可能是 `~/.hermes/config.yaml`；以自己的实际环境为准，不根据示例猜测。
4. 重启本 Agent 会话，或使用已支持的 `/reload-mcp`。Hermes 可用 `hermes mcp test heartradio` 验证连接。工具集名称为 `mcp-heartradio`，具体发现后的工具名称由客户端决定。
5. 先调用 `get_capabilities`，再读取 `get_heart_rate` 与 `get_device_status`。已有允许复用的有效广播或一次性心率缓存时可本地读取；没有有效值或需要新读数时，显式调用`measure_heart_rate`，默认最多等待15秒，不保证15秒内一定出值；新设备状态、测量和提醒要求连接可用且Agent交互已同步开启。需要验证新测量时，明确请求一次4秒气压或加速度，观察 `ok/status`、单位、缓存年龄与清理结果。提醒会直接产生振动/亮屏效果，不将它作为发现工具或检查包的步骤。遵守保存的指标开关和间隔；失败不编造数值或连续重试。

普通 stdio 客户端按配置启动 `server.py` 这个 Python 子进程，通过标准输入/输出交换 MCP 消息。MCP 是通信协议；这个文件是本项目的服务入口。安装通常指配置并连接该进程，无需另装一套通用“MCP软件”。适配器只需 Python 标准库，不需 API key，不读取手表配对凭据；不要手动打开 `server.py` 等待它显示网页。

## 可选 Hermes 合并工具

`scripts/configure_hermes.py` 供接入 Agent 自主选择使用，不会自动执行。必须使用 Hermes 自己的 Python，并传入真实的 Hermes 源码目录和当前配置的绝对路径；它借用该安装已有的 YAML 编辑库，保留注释、引号和其他配置。默认只预览；明确加入 `--apply` 才修改，修改前生成同目录备份。重复添加同一配置返回 `unchanged`；`--action remove --apply` 只移除本项。备份可能包含原配置的秘密，留在原配置目录，不复制到本 MCP 目录或项目。

```powershell
# 先由 Agent 查明下面三个路径，再替换示例；不要猜当前 profile。
& 'Hermes实际Python绝对路径' -I '<MCP输出目录>\scripts\configure_hermes.py' --hermes-source 'Hermes实际源码绝对目录' --config '当前config.yaml绝对路径'
# 确认预览后，同一命令添加 --apply；移除时再添加 --action remove。
```

也可以使用 Agent 原有的配置编辑能力或 Hermes 自己的 MCP 管理命令。不支持 MCP 的 Agent 可以经项目的本机 HTTP 工具入口接入，见项目 `docs/LOCAL-AGENT.md`。完整调用边界见项目 `docs/MCP-CONTRACT.md`。

## 十一项工具与数据含义

| 工具 | 行为 |
| --- | --- |
| `get_user_state` | 无参数；新采集完整30秒运动，由电脑返回“可能在休息”“清醒且可能不明显活动”“清醒且活动较明显”或“暂不判断”。只附运动比例和当次有效心率摘要，不返回原始数组，不查历史，不自动开启心率广播。通常需30～40秒。 |
| `read_watch_data` | 一次读取任意组合，例如 `{"metrics":["pressure","accelerometer","gyroscope"],"seconds":4}`。各指标并行；逐项保留成功/失败、来源与时间。默认复用有效心率，否则等待新有效回调，`fresh:true`强制本次新读。返回 `complete` 与 `failedMetrics`，不能只看整体 `ok`。 |
| `get_capabilities` | 查询已验证指标及限制，不请求手表新采集。 |
| `get_heart_rate` | 读取有效广播或一次性心率缓存，`max_age_seconds` 为1～30秒；本次调用不请求设备。 |
| `get_device_status` | 按需读取电量、充电和连接信息；`fresh:true` 绕过允许的状态缓存。 |
| `measure_pressure` | 1～10秒标准气压，单位 hPa；默认窗口以仪表盘保存设置为准。 |
| `measure_motion` | 1～10秒加速度或陀螺仪原始数据，单位 m/s²、rad/s；不推断步数或活动类型。 |
| `measure_heart_rate` | 新采集心率：`seconds`可设1～15秒、默认15；首个有效回调即结束。与广播共用物理监听，结束只释放本次用途，广播开关不改变。 |
| `cancel_measurement` | 可带当前测量的 `request_id`，省略时取消调用瞬间正在进行的全部临时采集；不停止连接或持续心率。 |
| `send_reminder` | 立即发送自定义文字，可选振动/亮屏、模板与自定义波形；需要已同步开启的Agent交互。 |
| `get_reminder_status` | 默认只读电脑保存的发送尝试和回执；`fresh:true`主动向手表核对，需要Agent交互，不重发提醒。 |

只读电脑的调用：`get_capabilities`、`get_heart_rate`、`get_reminder_status(fresh:false)`。设备状态及传感器可按既有设置复用缓存，新查询/新测量必须经过交互门禁；`measure_heart_rate`、`send_reminder`和`get_reminder_status(fresh:true)`需要访问手表。关闭交互仍能查看电脑已有提醒记录；工具没有开启交互、连接或持续心率的能力。

`get_user_state`同样需要手表连接和Agent交互，且加速度、陀螺仪均已许可。服务可用按使用约定默认正常佩戴，`wearBasis:service_available_assumption`不是自动佩戴检测。`source:local_heuristic`、`accuracy:unmeasured`说明它是尚未测定准确率的本地规则；标签中的“清醒”也只是猜测，不确认睡眠、坐姿或行走。第一档还需要本次请求窗口内至少3个不同有效心率接收事件及稳定性依据；心率缺失时很低活动返回暂不判断，第二/三档可只依靠足够运动证据。返回`ok:true,tier:null`表示观测完成但证据不足，属于正常未知结果，不应自动重测。运动失败返回`ok:false`及原因；取消沿用`cancel_measurement`，以原查询返回为最终结果。该查询不写状态历史，也不替换原短测量缓存。

## 提醒参数、模板与回执

0.6.1增加独立声音请求：`sound`默认false，设为true并指定`sound_source:"system"`可请求一次手表系统默认提示音。必须由本人先在手表或仪表盘允许声音，并等待同步；静音、勿扰、音量和系统声音渠道仍可抑制。`soundIssued`表示MediaPlayer.start()已调用，`soundCompletedAtMs`表示完成回调，均不证明本人听见。自定义来源已预留，本轮拒绝custom和任意路径/URL。示例：`{"reminder_id":"sound_unique_id","title":"提示","body":"功能测试","sound":true,"sound_source":"system","vibrate":false,"screen":false}`。


每个新提醒需要唯一`reminder_id`（1～64个ASCII字母/数字/下划线/连字符，建议UUID十六进制）、`title`（1～60字符）、`body`（1～500字符）。中文/表情按完整Unicode字符计数，正文可换行，纯文本显示。手表正文和标题可以滚动，“知道了”固定在底部。

`template`可选`strong`（默认）、`standard`、`light`、`silent`（静默亮屏）、`notification_only`（仅通知）。`vibrate`和`screen`可分别覆盖模板。`vibration`可覆盖`pulse_ms`20～1000、`gap_ms`0～2000、`count`1～8、`amplitude`1～255；技术硬上限为整组含间隔10000ms；用户限额默认6000ms。用户可在电脑或手表的提醒效果设置中调整至1000～10000ms，Agent必须遵守当前较低限额。默认强档为255/360ms/两次/间隔220ms。振幅受硬件支持和实际触感限制，回执同时返回请求参数、实际下发值与硬件能力。

`screen_seconds`为5～30秒，默认15，并须符合用户页面上限（默认30秒，可设5～30秒）；`ttl_seconds`为1～300秒，默认60，是立即发送的有效期，不是未来定时发送。遵守系统勿扰和锁屏，不自动解锁。示例工具参数：

```json
{
  "reminder_id": "example_20261020_001",
  "title": "休息提醒",
  "body": "请稍作休息。\n忙完后可以点知道了。",
  "template": "strong",
  "vibration": {"pulse_ms": 300, "gap_ms": 500, "count": 3, "amplitude": 255},
  "screen": true,
  "screen_seconds": 20,
  "ttl_seconds": 60
}
```

同一已保留编号及相同文字/效果返回原记录，不再次执行、不续有效期；同编号换内容拒绝。电脑与手表各保留至多128条去重记录，不承诺无限历史。同一提醒发生超时、断线或`outcome_unknown_local_failure`后，保留原编号，先调用`get_reminder_status`；不生成新编号自动补发，不在重连后重放。新提醒至少间隔两秒，振动未结束时可能返回忙。状态查询默认不主动通信；明确需要时才用`fresh:true`，不要高频轮询手表。

`issued`仅表示下发API，`displayed`表示页面取得显示，`acknowledgedAtMs`表示本人点过“知道了”；三者不能互换，点击也不是阅读全部文字的证明。`suppressedByDnd`、`screenSuppressedByLock`记录系统限制。发送返回`ok:false`时报告实际失败或不确定状态；本地查询返回`ok:true`仅表示找到记录，其中可以是之前失败的尝试，不能当作发送成功。

通过`get_capabilities`读取当前允许振动/亮屏、时长限额和同步状态。只有用户可在两端设置中编辑；设置离线保存、不自行连接，冲突时采用更严格值。新提醒在设置尚未确认同步或保存失败时拒绝；超限返回`reminder_policy_denied`及原因，不悄悄改波形。保存效果设置会结束当前提醒。页面到期退出并释放本应用常亮要求，整机实际熄屏仍受系统影响。声音已支持，服从前述许可与系统限制。

完整参数见 `tools.json`。结果中的 `receiptAgeSeconds`、`cacheAgeSeconds` 是电脑接收/缓存年龄，`sourceAgeVerified:false` 表明源采样年龄没有独立标定。旧结果必须作为缓存说明，不能称为当前新测量。离线、无接触、过期返回空值或明确状态；不以0代替。气压与运动只在监听及短时唤醒已释放后提供有效新数值。

常见状态：`local_service_unavailable` 表示仪表盘工具入口不可访问；`bridge_unavailable`/`watch_offline` 表示按需通道未就绪；`busy` 表示同一指标已有不兼容时长的采集或设备采集容量已满；`rate_limited` 应等待返回的重试时间；`disabled_by_settings` 表示指标被网页关闭。心率可因 `no_contact`、`stale` 等状态不可用，应报告实际返回的状态。

多个 Agent 共用电脑数据层：心率、气压、加速度、陀螺仪最多四项同时采集；相同指标与时长的在途请求共用一次采集，时长不同则返回该指标忙。每项独立清理，定向取消不影响其他指标；共用同一编号的调用会一起收到取消结果。同一指标的间隔可设0～300秒，默认0，不再阻挡其他指标；`fresh:true`不绕过用户设置。stdio适配器支持最多八个业务调用并发，取消另有处理通道。Hermes是否并行还受其当前客户端限制，因此现有Hermes配置保持 `supports_parallel_tool_calls:false`，推荐使用 `read_watch_data` 在一次工具调用内完成任意组合。气压/运动最多10秒，一次性心率最多15秒；心率首个有效回调即结束。默认综合读取优先用允许的有效心率缓存，无数据则并行等待最多5秒的新有效回调；需要更长等待可显式传 `heart_seconds:15`。独立 `measure_heart_rate` 仍默认最多15秒。缺失必须照实返回。新生成客户端建议90秒调用预算；已有60秒配置通常可覆盖正常状态观测，但故障清理可能更久。预算包含通信与清理，取消需由原请求结果确认资源释放。

当前默认工具不包含 GPS、计步、血氧、心电和厂商原始佩戴通道。定位已完成窗边诊断但未取得坐标，用户决定本轮跳过位置接入，保留为可选后续工作。不得从未验证原始通道推算健康或位置数据。

0.3.1起，Windows适配器启动时在源项目`runs/mcp-adapters`登记自己的进程编号、创建时间、解释器与入口文件；结束时删除自己的登记，不存工具参数、健康数据或凭据。原05～08资源入口可据此统计MCP适配器，Agent本体及模型进程不算入项目。正在运行的旧版本不会自动登记，需由用户在Agent里重新连接本服务；开发侧不修改正式Agent配置。异常结束留下的旧登记由资源工具按创建时间排除，不会误算复用编号的其他进程。

## 日常操作与数据去向

继续使用原桌面01～08和网页设置。02只查看，03结束本项目电脑服务，04只停心率接收。Agent 启动 MCP 不自动开启电脑接收或手表待命，停止服务后也不自动重启它们。恢复电脑服务后适配器可再次读取；不用重新配置本目录。

连接、心率广播和Agent交互各自控制；同一个Agent交互开关可在任意一端修改，离线改动保存待同步，不会擅自重连。关闭交互会取消按需任务及提醒效果，保留独立连接和心率。期限由已有仪表盘/手表设置控制，MCP无修改时长或开关工具。蓝牙不依赖同一Wi-Fi；选用Wi-Fi时仍需局域网互通。真实Wi-Fi及跨通道整合回归按用户要求延期，不能把共用协议检查当作Wi-Fi真机验收。

从0.4.0升级后，需在Agent中重新连接本服务或重新加载工具列表，原八项工具名称和参数结构保留，新增一次性心率工具；开发侧不改正式Agent配置或代替用户发送消息。

本目录不存健康日志或配对凭据，数据仍在源项目的既有位置。Agent 在本机运行不等于模型也在本机：如果 Agent 使用云端模型，工具结果可能随对话进入该提供方。接入 Agent 应解释自己的实际配置和去向，不擅自切换模型。

Windows 原生客户端直接使用生成命令；WSL/容器须实测路径、进程和数据入口可达性。不要把 Linux 的 `127.0.0.1` 当作 Windows 的同一地址。优先考虑从该环境启动 Windows Python 的 stdio 路线，但本包尚未验收这类环境。

## 升级、移除与搬家

源码唯一来源是 `<项目目录>`，部署包由 `agent/export_package.py` 生成。升级在源码提交、检查后执行下面命令；生成器不会注册 Agent，更新前保存旧包到本目录 `backups`，最后发布带逐文件哈希的 `VERSION.json`。导出覆盖的是生成文件，个人说明请另存文件，别直接修改生成 README 或代码。

```powershell
& '<Python解释器>' '<项目目录>\agent\export_package.py' --destination '<MCP输出目录>' --python '<Python解释器>'
& '<Python解释器>' '<MCP输出目录>\scripts\check.py'
```

项目、Python或部署目录搬家后，重新导出到实际路径并让各 Agent 更新本服务配置。移除时先让 Agent 从自身配置移除 `heartradio`、重新加载连接；不必删除数据或停止心率，也不要还原整个旧配置覆盖其他新增项。检查失败先根据返回状态核对文件、端口与服务，再看网页；不要通过自动开采集掩盖故障。
