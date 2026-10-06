"""Generate a descriptive report from completed controlled-phase evidence."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    names = [('A1-heart-only', '仅原心率'), ('B-standby', '原心率＋接口待命'),
             ('C-bounded-samples', '原心率＋标准接口短时读取'), ('A2-heart-only', '停止桥接后仅原心率')]
    phases = [(name, label, json.loads((args.directory / (name + '.json')).read_text(encoding='utf-8')))
              for name, label in names]
    local = timezone(timedelta(hours=8))
    stamp = lambda ms: datetime.fromtimestamp(ms / 1000, local).strftime('%H:%M:%S')
    lines = ['# 连续佩戴的心率共存对照', '',
             '同一佩戴轮次依次记录四段；USB 断开、保持原 Wi-Fi、自然熄屏。用户确认正常佩戴并尽量坐着，未安排取下动作。原 CaptureService 未重启；新增待命由用户手动开启。只用电脑现有接收日志与本机请求入口划分阶段。', '',
             '| 阶段 | 北京时间 | 实际秒数 | 有效心率回调 | 次/分钟 | 最长有效回调间隔秒 | 超过30秒无新回调的时长 | 断连 | 序号异常 |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, label, phase in phases:
        summary = phase['summary']
        gap = summary['maxBetweenValidCallbacksSeconds']
        lines.append(f"| [{label}]({name}.json) | {stamp(phase['beginWallMs'])}–{stamp(phase['endWallMs'])} | "
                     f"{summary['durationSeconds']:.2f} | {summary['validHeartCallbacks']} | "
                     f"{summary['validHeartCallbacks'] * 60 / summary['durationSeconds']:.2f} | "
                     f"{gap if gap is not None else '无两个有效事件'} | {summary['staleSeconds']:.2f} 秒 | "
                     f"{summary['wifiDisconnects']} | {len(summary['sequenceAnomalies'])} |")
    complete = all(phase.get('complete') for _, _, phase in phases)
    fresh = all(phase['summary']['validHeartCallbacks'] > 0 and phase['summary']['staleSeconds'] == 0
                for _, _, phase in phases)
    lines += ['', f'四段记录完整：{complete}。四段均有有效心率且按30秒接收规则没有过期区间：{fresh}。', '',
              '这里的“有效心率回调”来自原20字节实时通道；心跳状态包不增加心率计数。表中间隔使用电脑单调接收时间，阶段开头和结尾的新鲜度也计入过期时长。源采样年龄仍未独立确认，不能将此表解释为真实生理数据延迟。', '',
              '## 短时请求', '',
              '每30秒轮换一次加速度、陀螺仪和气压，每次申请4秒；索引从本轮 inventory 的标准类型解析。没有读取厂商光学、标准心率或定位接口。', '',
              '| 次序 | 标准类型 | 结果 | 回调 | 手表实际毫秒 | 电脑往返毫秒 | 结果时锁持有 | 随后空闲锁持有 |',
              '| ---: | ---: | --- | ---: | ---: | ---: | --- | --- |']
    requests = phases[2][2]['requests']
    for i, request in enumerate(requests, 1):
        lines.append(f"| {i} | {request['type']} | [{request['status']}]({request['resultFile']}) | "
                     f"{request['callbacks']} | {request['actualElapsedMs']:.2f} | {request['pcRoundTripMs']:.2f} | "
                     f"{request['requestWakeLockHeldAtResult']} | {request['idleWakeLockHeld']} |")
    cleaned = bool(requests) and all(r['status'] == 'data_received' and r['listenersReleased'] is True
                                   and r['requestWakeLockHeldAtResult'] is False
                                   and r['idleWakeLockHeld'] is False and r['idleActiveRequest'] is None for r in requests)
    lines += ['', f'所有记录的短时请求收到数据、释放监听并回到无请求唤醒锁的空闲状态：{cleaned}。', '',
              '## 解释边界', '',
              '- 这是一次约18分钟稳定阶段的对照，点击待命按钮前后另留过渡；未测试全天运行、网络变化或耗电。',
              '- 逐阶段回调率受心率算法、精度状态、佩戴和环境影响；一次顺序对照不能证明任何微小差异的因果关系。',
              '- 如果基线没有有效回调，应先处理原心率恢复；如果新增阶段出现长停顿，新增接口不通过共存验收。',
              '- 这份报告描述短时共存证据；最终服务释放记录见下文，全天稳定性仍需单独验收。', '']
    watch_files = sorted(args.directory.glob('session-*.jsonl'))
    if watch_files:
        watch_packets = {}
        for line in watch_files[-1].read_bytes().splitlines():
            row = json.loads(line)
            if row.get('kind') == 'wifi_packet':
                watch_packets.setdefault(row['hex'], []).append(row)
        screen_evidence = {}
        for name, _, phase in phases:
            packets = [json.loads(line) for line in (args.directory / (name + '-heart.jsonl')).read_bytes().splitlines()]
            valid = [r for r in packets if phase['beginMonotonicNs'] <= r['pcMonotonicNs'] <= phase['endMonotonicNs']
                     and r.get('kind') == 'packet' and r['packet']['kind'] == 'sensor_event' and r['packet']['valid']]
            matches = [watch_packets.get(r['hex'], []) for r in valid]
            matched = [values[0] for values in matches if len(values) == 1]
            screen_evidence[name] = dict(validHeartPackets=len(valid), matchedOwnWatchPackets=len(matched),
                                        screenOff=sum(r['screenInteractive'] is False for r in matched),
                                        screenOn=sum(r['screenInteractive'] is True for r in matched))
        (args.directory / 'screen-off-validation.json').write_text(json.dumps(screen_evidence, indent=2), encoding='utf-8')
        lines += ['## 手表端亮屏状态核对', '',
                  '将每段电脑收到的有效心率包，按完整20字节内容匹配本应用私有日志中的 wifi_packet；不靠两台设备的时钟直接对齐。以下统计记录包生成时的屏幕状态，不能据此断言处理器进入了深度休眠。', '',
                  '| 阶段 | 有效心率包 | 唯一匹配手表日志 | 熄屏 | 亮屏 |',
                  '| --- | ---: | ---: | ---: | ---: |']
        for name, label, _ in phases:
            s = screen_evidence[name]
            lines.append(f"| {label} | {s['validHeartPackets']} | {s['matchedOwnWatchPackets']} | {s['screenOff']} | {s['screenOn']} |")
        lines.append('')
    service_file = args.directory / 'activity-services-final.txt'
    wake_file = args.directory / 'capability-wake-lock-final.json'
    if service_file.exists() and wake_file.exists():
        services = service_file.read_text(encoding='utf-8')
        wakes = json.loads(wake_file.read_text(encoding='utf-8'))['heldMatchingLines']
        heart_running = 'com.example.heart_radio.CaptureService' in services
        bridge_running = 'com.example.heart_radio.CapabilityBridgeService' in services
        released = heart_running and not bridge_running and not wakes
        lines += ['## 停止后的资源核对', '',
                  f'导出本应用服务记录时，原心率服务运行：{heart_running}；新增桥接服务运行：{bridge_running}；匹配测试请求的唤醒锁数量：{len(wakes)}。', '',
                  '证据：[本应用服务记录](activity-services-final.txt)、[请求唤醒锁记录](capability-wake-lock-final.json)、[手表请求日志](watch-requests-final.jsonl)。USB 仅用于结束后导出证据。', '',
                  f'短时共存验收（四段完整且有有效心率、没有30秒接收过期、短时请求清理完成、结束后新增服务和请求锁释放）：{complete and fresh and cleaned and released}。', '',
                  '此结果支持首版本机工具接入；它不证明先前约214秒停顿的原因，也不代表全天稳定或电量验收通过。', '']
    (args.directory / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(complete=complete, allPhasesFresh=fresh, requestsCleaned=cleaned, requestCount=len(requests))))


if __name__ == '__main__':
    main()
