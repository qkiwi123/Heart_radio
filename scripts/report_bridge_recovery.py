"""Build a descriptive report from completed recovery phases and own-app evidence."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    folder = args.directory
    names = [('A1-heart-only', '仅原心率'), ('B-standby', '退出页面后的待命'),
             ('C-recovery', '连接恢复与Wi-Fi开关'), ('A2-heart-only', '停止待命后仅原心率')]
    phases = [(n, label, json.loads((folder / (n + '.json')).read_text(encoding='utf-8'))) for n, label in names]
    checks = json.loads((folder / 'wireless-recovery.json').read_text(encoding='utf-8'))
    network = json.loads((folder / 'wifi-off-on.json').read_text(encoding='utf-8'))
    local = timezone(timedelta(hours=8))
    stamp = lambda ms: datetime.fromtimestamp(ms / 1000, local).strftime('%H:%M:%S')
    lines = ['# 无线佩戴的桥接恢复与心率共存', '',
             '用户拔USB、正常佩戴并静坐，开启待命后返回表盘。四段记录使用电脑单调接收时间；恢复请求与电脑桥接重启不使用ADB，也不重启原心率服务。第三段明确包含人为Wi-Fi关闭/重新开启，须与稳定网络的阶段分别解释。', '',
             '| 阶段 | 北京时间 | 秒数 | 有效心率回调 | 最长间隔秒 | 30秒接收过期时长秒 | 心率断连 | 序号异常记录 |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, label, phase in phases:
        s = phase['summary']
        lines.append(f"| [{label}]({name}.json) | {stamp(phase['beginWallMs'])}–{stamp(phase['endWallMs'])} | "
                     f"{s['durationSeconds']:.2f} | {s['validHeartCallbacks']} | {s['maxBetweenValidCallbacksSeconds']} | "
                     f"{s['staleSeconds']:.2f} | {s['wifiDisconnects']} | {len(s['sequenceAnomalies'])} |")
    lines += ['', '心跳状态包不计为有效心率，断连与无效数据不补值；源采样年龄仍未独立确认。Wi-Fi人为中断可能造成预期的传输空档或序号缺口，这类记录不能直接解释成新增传感器导致心率失效。', '',
              '## 桥接恢复', '', '| 检查 | 恢复核对耗时秒 |', '| --- | ---: |']
    for check in checks['checks']:
        if 'recoverySeconds' in check:
            lines.append(f"| {check['name']} | {check['recoverySeconds']:.3f} |")
    lines += ['', f"恢复检查完整：{checks['complete']}。同一待命会话恢复、原期限减少、被中断测量明确失败、恢复后的空闲状态没有请求/锁，以及新气压测量均由 [wireless-recovery.json](wireless-recovery.json) 记录。", '',
              f"Wi-Fi开关观察完整：{network['complete']}。"]
    if network.get('complete'):
        lines += [f"从电脑首次观察到按需桥接离线，到两条通道恢复且收到离线之后的新有效心率，间隔 {network['observedOfflineUntilFreshHeartSeconds']:.3f} 秒。这个间隔包含用户保持关闭的时间，不是从点击开启开始计算的纯重连延迟。", '',
                  '完整逐次状态见 [wifi-off-on.json](wifi-off-on.json)。']
    fault = next(p for n, _, p in phases if n == 'C-recovery')['summary']
    lines += ['', f"本轮人为断网阶段最长有效心率间隔为{fault['maxBetweenValidCallbacksSeconds']:.3f}秒，其中{fault['staleSeconds']:.3f}秒超过30秒接收有效期，并记录{fault['wifiDisconnects']}次断连和{len(fault['sequenceAnomalies'])}次序号异常。该阶段不计为无中断稳定通过。它与上述桥接离线恢复间隔使用不同的计时起点：前者从上一条有效心率开始，后者从电脑观察到接口桥接离线开始。间隔与人工断网观察重叠，但不能据此还原用户实际点击开关的准确时间。详见 [C-gap-analysis.json](C-gap-analysis.json)。"]
    requests_path = folder / 'watch-requests-final.jsonl'
    if requests_path.exists():
        requests = [json.loads(line) for line in requests_path.read_bytes().splitlines()]
        check = next(c for c in checks['checks'] if c['name'] == 'active_sample_disconnect')
        target = check['during']['activeRequest']
        results = [r['data'] for r in requests if r.get('kind') == 'result' and r['data'].get('id') == target]
        result = results[-1] if results else None
        released = bool(result and result.get('status') == 'disconnected' and result.get('listenersReleased') is True
                        and result.get('requestWakeLockHeldAtResult') is False)
        if not released:
            raise RuntimeError('Own watch log did not confirm interrupted-sample cleanup')
        (folder / 'interrupted-sample-cleanup.json').write_text(json.dumps(dict(requestId=target,
            status=result['status'], callbacks=result.get('callbacks'), listenersReleased=result['listenersReleased'],
            requestWakeLockHeldAtResult=result['requestWakeLockHeldAtResult']), indent=2), encoding='utf-8')
        lines += ['', '采集中断后的释放也由手表自身日志确认：本次结果为disconnected，监听已释放，请求锁未持有。见 [interrupted-sample-cleanup.json](interrupted-sample-cleanup.json)。']
    watch_files = sorted(folder.glob('session-*.jsonl'))
    if watch_files:
        watch = [json.loads(line) for line in watch_files[-1].read_bytes().splitlines()]
        index = {}
        for row in watch:
            if row.get('kind') == 'wifi_packet': index.setdefault(row['hex'], []).append(row)
        evidence = {}
        diagnostics = []
        lines += ['', '## 手表日志核对', '', '| 阶段 | 唯一匹配有效包 | 熄屏生成 | 亮屏生成 |', '| --- | ---: | ---: | ---: |']
        for name, label, phase in phases:
            rows = [json.loads(line) for line in (folder / (name + '-heart.jsonl')).read_bytes().splitlines()]
            valid = [r for r in rows if phase['beginMonotonicNs'] <= r['pcMonotonicNs'] <= phase['endMonotonicNs']
                     and r.get('kind') == 'packet' and r['packet']['kind'] == 'sensor_event' and r['packet']['valid']]
            matches = [index.get(r['hex'], []) for r in valid]
            matched = [m[0] for m in matches if len(m) == 1]
            off = sum(m['screenInteractive'] is False for m in matched)
            on = sum(m['screenInteractive'] is True for m in matched)
            evidence[name] = dict(valid=len(valid), uniqueMatched=len(matched), screenOff=off, screenOn=on)
            lines.append(f'| {label} | {len(matched)} / {len(valid)} | {off} | {on} |')
            if matched:
                low, high = min(m['elapsedNs'] for m in matched), max(m['elapsedNs'] for m in matched)
                diagnostics.extend(dict(phase=name, **{k:d.get(k) for k in ('wallMs','elapsedNs','batteryPct','plugged',
                        'chargeCounterUah','screenInteractive')}) for d in watch
                        if d.get('kind') == 'capture_diagnostic' and low <= d['elapsedNs'] <= high)
        (folder / 'screen-validation.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        (folder / 'battery-observations.json').write_text(json.dumps(diagnostics, indent=2), encoding='utf-8')
        lines += ['', '按完整包内容匹配，不直接假定电脑与手表时钟相同。屏幕关闭不等于处理器深度休眠。', '',
                  '保留原心率服务已有的低频[电量诊断](battery-observations.json)，没有为电量记录新增唤醒。百分比与电荷计数只作原始观察，来源精度未校准；本轮不推算续航或声称待命省电。']
    service_file = folder / 'activity-services-final.txt'
    if service_file.exists():
        services = service_file.read_text(encoding='utf-8')
        wake = json.loads((folder / 'capability-wake-lock-final.json').read_text(encoding='utf-8'))['heldMatchingLines']
        lines += ['', '## 最终清理', '', f"原心率服务运行：{'com.example.heart_radio.CaptureService' in services}；新增桥接服务运行：{'com.example.heart_radio.CapabilityBridgeService' in services}；匹配请求锁：{len(wake)}。", '',
                  '最终导出才重新连接USB。资源证据见本目录服务、锁和手表桥接日志。实际安装包校验值见 tested-apk.json；本轮佩戴使用最初的1.3.2构建，剩余时间报告刷新与临时握手关闭分类的后续修正单独完成USB验证。']
    lines += ['', '本轮不验证用户强制停止后的恢复、手表重启自启动、厂商主动回收进程后的重建、电脑IP变化或全天稳定。']
    (folder / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(dict(phasesComplete=all(p['complete'] for _, _, p in phases),
                          recoveryComplete=checks['complete'], networkComplete=network['complete'])))


if __name__ == '__main__':
    main()
