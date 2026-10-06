"""Offline report generation; tolerate an incomplete final JSONL line."""
from collections import Counter
import json
from pathlib import Path
import os
import uuid

GROUPS = ('receiver','dashboard','desktop_widgets','capability_bridge','mcp_adapter','total','monitor')
LABELS = dict(receiver='接收器',dashboard='仪表盘后台',desktop_widgets='桌面小窗',
              capability_bridge='按需桥接',mcp_adapter='MCP适配器',total='项目合计',monitor='采样工具（单列）')


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)


def read_rows(path):
    rows = []; invalid = 0
    if not path.exists(): return rows, invalid
    for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
        if not line.strip(): continue
        try: rows.append(json.loads(line))
        except ValueError: invalid += 1
    return rows, invalid


def process_key(row): return row['pid'],row['creationTicks']


def summarize(samples, conditions, status, events, invalid=0):
    cores = conditions['logicalProcessors']; interval = conditions['intervalSeconds']
    gaps = []; intervals = []; measured = 0
    for previous, current in zip(samples,samples[1:]):
        delta = current['elapsedSeconds']-previous['elapsedSeconds']
        if delta <= 0: continue
        if delta > interval*2.5:
            gaps.append(dict(start=previous['elapsedSeconds'],end=current['elapsedSeconds'],seconds=delta)); continue
        measured += delta; intervals.append((previous,current,delta))
    groups = {}
    for group in GROUPS:
        def selected(sample):
            return [p for p in sample['processes'] if (p['group']!='monitor' if group=='total' else p['group']==group)]
        series = []; cpu = []; missing = 0; fresh_cpu = []
        for sample in samples:
            values = selected(sample)
            if values:
                series.append(dict(time=sample['elapsedSeconds'],ws=sum(p['workingSetBytes'] for p in values),
                    private=sum(p['privateCommitBytes'] for p in values),threads=sum(p.get('threads') or 0 for p in values),
                    handles=sum(p['handles'] for p in values)))
        for previous,current,delta in intervals:
            before = {process_key(p):p for p in selected(previous)}
            after = {process_key(p):p for p in selected(current)}
            # Crossing a process start/exit cannot yield an exact interval total.
            affected = {issue.get('group') for sample in (previous,current) for issue in sample.get('issues',[])}
            uncertain = bool(affected & (set(GROUPS)-{'total','monitor'})) if group=='total' else group in affected
            if not before or before.keys()!=after.keys() or uncertain:
                missing += 1; continue
            spent = sum(after[key]['cpuSeconds']-before[key]['cpuSeconds'] for key in before)
            if spent < 0: missing += 1; continue
            percent = 100*spent/delta/cores
            cpu.append((percent,delta))
            if previous['activity'].get('freshValid') and current['activity'].get('freshValid') and current['activity'].get('validEvents',0)>0:
                fresh_cpu.append((percent,delta))
        if not series: continue
        def weighted(values):
            return sum(value*weight for value,weight in values)/sum(weight for _,weight in values) if values else None
        duration = sum(d for _,d in cpu)
        groups[group] = dict(cpuPercentAverage=weighted(cpu),cpuPercentMaxSample=max((v for v,_ in cpu),default=None),
            cpuMeasuredSeconds=duration,cpuExcludedIntervals=missing,cpuWithFreshDataAverage=weighted(fresh_cpu),
            memorySampleCount=len(series),workingSetMiBAverage=sum(r['ws'] for r in series)/len(series)/2**20,
            workingSetMiBMax=max(r['ws'] for r in series)/2**20,workingSetMiBStart=series[0]['ws']/2**20,
            workingSetMiBEnd=series[-1]['ws']/2**20,workingSetMiBChange=(series[-1]['ws']-series[0]['ws'])/2**20,
            privateCommitMiBAverage=sum(r['private'] for r in series)/len(series)/2**20,
            privateCommitMiBMax=max(r['private'] for r in series)/2**20,
            threadsMax=max(r['threads'] for r in series),handlesMax=max(r['handles'] for r in series))
    activity = {key:sum(s['activity'].get(key,0) for s in samples[1:]) for key in
                ('packets','sensorEvents','validEvents','connections','disconnects','sequenceGaps','parseErrors')}
    activity['sampleIntervalsWithValidEvents'] = sum(s['activity'].get('validEvents',0)>0 for s in samples[1:])
    activity['freshDataEndpointSecondsEstimate'] = sum(delta for before,after,delta in intervals
        if before['activity'].get('freshValid') and after['activity'].get('freshValid'))
    activity['maxObservedValidEventGapSeconds'] = max((s['activity'].get('maxValidEventGapSeconds',0) for s in samples),default=0)
    activity['longestObservedValidEventAgeSeconds'] = max((s['activity'].get('validEventAgeSeconds') or 0 for s in samples),default=0)
    state = status.get('state','interrupted')
    complete = state=='completed'
    return dict(schema=1,state=state,complete=complete,diagnostic=conditions.get('diagnostic',False),
                plannedSeconds=conditions['durationSeconds'],actualSeconds=samples[-1]['elapsedSeconds'] if samples else 0,
                samples=len(samples),observedIntervalSeconds=measured,gaps=gaps,invalidLines=invalid,
                activity=activity,groups=groups,eventCounts=dict(Counter(e['kind'] for e in events)))


def generate(directory, status_override=None):
    directory = Path(directory)
    conditions = json.loads((directory/'conditions.json').read_text(encoding='utf-8'))
    try: status = json.loads((directory/'status.json').read_text(encoding='utf-8'))
    except (OSError,ValueError): status = {}
    if status_override: status = dict(status,state=status_override)
    samples,bad_samples = read_rows(directory/'samples.jsonl')
    events,bad_events = read_rows(directory/'events.jsonl')
    result = summarize(samples,conditions,status,events,bad_samples+bad_events)
    atomic_json(directory/'summary.json',result)
    names = dict(completed='已完成',stopped='提前结束',failed='异常结束',interrupted='中断／未完成',running='进行中（临时报告）',starting='启动中')
    a = result['activity']
    lines = ['# HeartRadio 电脑资源测试报告','',
        f"状态：**{names.get(result['state'],result['state'])}**；用途：**{'工具诊断，不属于真实数据资源测试' if result['diagnostic'] else '真实数据连接资源测试'}**。",
        f"计划 {result['plannedSeconds']/60:.2f} 分钟；实际记录跨度 {result['actualSeconds']/60:.2f} 分钟；样本 {result['samples']} 个；采样间隔 {conditions['intervalSeconds']} 秒。",
        f"开始：{conditions['startedUtc']}；逻辑处理器：{conditions['logicalProcessors']}。",'',
        '## 数据是否真实持续到达','',
        f"收到 {a['packets']} 包；心率事件 {a['sensorEvents']} 条，其中有效事件 **{a['validEvents']}** 条；连接事件 {a['connections']} 次、断开事件 {a['disconnects']} 次；序号异常 {a['sequenceGaps']} 次。",
        f"有有效事件的采样区间：{a['sampleIntervalsWithValidEvents']}。区间两端均显示新鲜有效数据的估计时长：{a['freshDataEndpointSecondsEstimate']:.1f} 秒。这个估计不证明区间内每一秒都有采样。",
        f"相邻有效事件最大已观察间隔：{a['maxObservedValidEventGapSeconds']:.1f} 秒；采样时有效事件年龄最大值：{a['longestObservedValidEventAgeSeconds']:.1f} 秒。",
        ('**本轮没有有效心率事件，不能作为真实收数时资源占用的结论。**' if not a['validEvents'] else '有效事件来自接收日志；状态包不算心率测量。'),'',
        '## 各组件占用','',
        '| 组件 | 平均 CPU | 最高区间 CPU | 平均驻留内存 MiB | 最大驻留 MiB | 首末变化 MiB | 平均私有提交 MiB |',
        '|---|---:|---:|---:|---:|---:|---:|']
    def number(v): return '无法统计' if v is None else f'{v:.4f}%'
    for group,value in result['groups'].items():
        lines.append(f"| {LABELS[group]} | {number(value['cpuPercentAverage'])} | {number(value['cpuPercentMaxSample'])} | {value['workingSetMiBAverage']:.2f} | {value['workingSetMiBMax']:.2f} | {value['workingSetMiBChange']:+.2f} | {value['privateCommitMiBAverage']:.2f} |")
    lines += ['', 'CPU 以全部逻辑处理器总能力为 100%；最高值是采样区间平均值，不是瞬时峰值。组件均值只覆盖该组件已观察的样本或可计算区间，未运行时不补零。内存平均是已观察样本的算术平均；共享页面可能被多进程重复计入，私有提交不等于物理内存。首末变化不能单独证明内存泄漏。', '',
              '## 数据质量与事件','',f"采样长间断 {len(result['gaps'])} 次；损坏或未完成行 {result['invalidLines']}；日志解析错误 {a['parseErrors']}。进程变动、访问失败、长间断区间不纳入对应 CPU 平均，避免误算。"]
    for group,value in result['groups'].items():
        lines.append(f"- {LABELS[group]}：可计算 CPU 的区间合计 {value['cpuMeasuredSeconds']:.1f} 秒；排除 {value['cpuExcludedIntervals']} 段；有新有效数据区间的 CPU 均值 {number(value['cpuWithFreshDataAverage'])}；最大线程 {value['threadsMax']}，最大句柄 {value['handlesMax']}。")
    lines += ['', '事件计数：'+json.dumps(result['eventCounts'],ensure_ascii=False)+'。',
        '细节见 events.jsonl；逐次资源、窗口显示/固定状态、日志大小和数据计数见 samples.jsonl。', '',
        '## 范围与使用条件','',
        '项目合计包含本轮已识别的接收器、仪表盘、小窗、按需桥接及MCP适配器；未运行的组件不补零，采样工具单列。MCP仅统计新版适配器自行登记并核对身份的进程；启动中的旧版须由用户重新连接才可登记。Agent本体、模型推理、浏览器/Codex共享进程、GPU、整机及手表功耗不在合计中。短暂进程和瞬时峰值可能遗漏。',
        '窗口显示状态表示窗口可见，不代表没有被其他应用遮挡；固定状态取保存的布局。电脑休眠或调度延迟形成的长间断会标出，不自动推断为全天稳定运行。',
        '结束资源采样不会停止接收器或手表采集。突然断电时最近尚未落到存储设备的数据仍可能丢失。', '',
        '这是一次观测结果；与旧版本空闲样本的差异同时包含版本、使用状态和采样间隔差异，不能直接归因于真实收数。']
    if status.get('error'): lines += ['', '异常原因：'+status['error']]
    (directory/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result
