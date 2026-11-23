# MCP调用契约

当前 0.13.0，共 13 项工具，配套电脑服务为 `1.18.0-stress-history`。包检查会比较 `VERSION.json` 中的 `requiredPcVersion` 与本机电脑服务版本。参数、必填项和技术上限见 [agent/tools.json](../agent/tools.json)。

| 工具 | 行为 | 门禁与限制 |
| --- | --- | --- |
| get_capabilities | 电脑指标、设置、已验证能力及活动请求ID | 不访问手表；available不是实时可用保证。 |
| get_heart_rate | 有效广播/一次性心率缓存，1～30秒年龄范围 | 不启动采样；有效值可能不存在。 |
| get_device_status | 电量、充电、连接等公开设备状态 | 可复用状态缓存；新读需要同步交互；电池温度不是体温。 |
| measure_heart_rate | 新采集第一个有效回调，`wait_seconds` 为 1～15 秒的等待上限，默认 15 秒。 | 兼容旧名 `seconds`，只传一个。收到有效值即结束并释放本次采集。 |
| measure_pressure | 1～10秒气压 | 单位hPa；不是血压。 |
| measure_motion | 1～10秒加速度/陀螺仪 | 含重力、保留至多64样本；不自动换算步数。 |
| read_watch_data | 用必填的 `metrics` 显式选择指标。心率缺少有效缓存时，按 `heart_wait_seconds` 等待，默认 5 秒。 | 兼容旧名 `heart_seconds`，只传一个。`seconds` 仅用于气压和运动。`compact:true` 可省略原始样本。 |
| cancel_measurement | 取消指定请求或调用瞬间全部临时请求 | 影响共用同一请求的等待者；取消提交不等于清理确认，不关闭连续广播。 |
| send_reminder | 显式立即文字提醒，可组合振动/亮屏/系统音 | 需要明确发送意图、唯一reminder_id、连接及同步交互和用户效果限额。无未来排程/自动补发。 |
| get_reminder_status | 默认查电脑记录；fresh=true查手表 | 只读查询，不重放效果；fresh需要交互门禁。 |
| get_user_state | 先查佩戴，再按 `metrics` 选查 `sleep`、`heart_rate`、`accelerometer`、`gyroscope`、`stress`。省略或传空列表时只查佩戴。 | 离腕时结束；确认在腕才启动新心率、运动或压力点测。佩戴未知时可返回许可范围内的已有心率或原厂睡眠报告。压力需显式选择且启用手表压力无障碍服务；会亮屏占用前台。 |
| read_watch_health_now | 单次读取原厂活动、佩戴或本轮最近的睡眠事件。 | 需同步交互和对应实时权限，回包返回前复核权限。没有有效事件时睡眠状态为未知。 |
| get_watch_health_history | 仅查询电脑已授权归档：`activity`、`wear`、`current_sleep`、`historical_sleep`、`heart_rate`、`stress`。手表可离线。 | 默认最近七个北京时间日期，最多 31 天；活动分钟、睡眠分期、原厂心率及压力点测使用对应分页字段。压力仅含本项目触发的成功点测。 |

`get_capabilities`、`get_device_status` 和 `read_watch_data` 的 `onDemand` 描述电脑当前的业务通道、交互就绪状态和未就绪原因。设备回包的 `bridgeLifecycleScope: wifi` 指明 `bridgeLifecycle` 只描述 Wi-Fi。即使 Wi-Fi 重试，蓝牙仍可能完成测量。每项结果以自己的 `ok` 和 `status` 为准。`get_capabilities` 和 `read_watch_data` 支持 `compact:true`。精简组合返回运动样本的三轴最小值和最大值，完整模式保留样本。

原厂睡眠事件的 `sourceAtMs` 表示事件时间，`eventReceivedAtMs` 表示手表接收时间。事件可能晚于实际状态变化。归档时，佩戴和睡眠观测用事件时间归日、计算首末时间和间隔，缺少事件时间时用手表读取时间。重复读取同一睡眠事件只计一次。新归档或更新的 `490` 明细用 `firstReadAtMs`、`lastReadAtMs` 保留仍有记录的最早、最晚手表快照时间。

压力点测使用 `get_user_state({"metrics":["stress"]})`，与其他显式选择的指标并行。已知佩戴者在活动时传 `wearer_state:"moving"` 跳过点测。`stress.ok:true` 时 `stress.score` 才是本轮有效分数；`stock_no_valid_reading` 表示原厂明确失败，`no_fresh_result` 表示 65 秒内没有新分数。二者皆不复用旧值、不自动重试。成功且开启归档时写入电脑；点测占用原厂页面，运动准确率尚未测定，不能从分数直接推断情绪。客户端调用预算建议 120 秒，显式压力点测的本机请求等待上限为 105 秒（含佩戴检查）。

历史工具的 `timeline` 视图查询单日、单指标。活动分小时列出 24 小时的步数与记录分钟数，无记录的小时 `steps:null`；活动分钟明细以 `nextMinuteOffset` 翻页。睡眠分期和原厂心率样本每页最多 100 条，分别使用 `nextSegmentOffset`、`nextSampleOffset`。压力日汇总提供数量及分数范围，单日明细使用 `nextObservationOffset`。原厂完整压力历史尚未接通；空记录不能当作零分或未睡眠。

新设备操作共用两端确认的Agent交互；关闭交互可取消临时任务，不删除已保存电脑数据。指标许可、缓存、同指标间隔与用户提醒效果上限仍生效。默认整组振动上限6秒（用户可设1～10秒）、页面上限默认30秒（5～30秒）。声音总许可和单条默认关闭，仅支持系统默认通知音、最多3秒，服从静音/勿扰/音量/通知限制；无自定义音源。

任务已获授权在需要本人介入时通知的，Agent 主动提醒前先用 `get_user_state({"metrics":[]})` 确认在腕。离腕或佩戴无法确认时，待处理事项留在电脑上。用户明确要求立即发送的，仍可直接调用 `send_reminder`。

同一次提醒意图使用同一个编号和完全相同内容/效果。PC先持久化意图再发送一次；超时、断连或结果不确定后查询原编号，不换新编号自动重发。两端各128条，满额替换过期条目，只在保留范围内去重，没有永久一次性保证。issued表示接口调用，displayedAtMs表示页面显示，acknowledgedAtMs才表示本人点确认；soundIssued/完成回调均不证明实际听见。

`attemptStatus` 是当次发送尝试的结果，`status` 是结合已有回执得出的当前已知状态。两者分别为 `issued` 和 `acknowledged` 时，表示当时已下发，后来用户点了确认。

手表删除后电脑仍保留原文，deleted为终态。日常重连及交互确认后电脑分批补查状态，不依赖Agent调用、不重放效果。not_found只表示手表当前无该编号，不推断此前已删除或未执行。watchRecordStatus/watchCheckedAtMs记录核对事实。网页历史仅覆盖当前账本，没有新增MCP提醒列表/状态历史工具。

监听及请求锁释放确认后才作为有效新测量返回；超时、无接触、未知、资源繁忙均可发生。关闭客户端或协议取消不替代资源释放回执。服务端并发不等于客户端并发；客户端示例默认不声明并行工具调用。模型数据去向由Agent实际配置决定，本机HTTP的Host/Origin检查也不是对同一电脑其他进程的身份认证。
