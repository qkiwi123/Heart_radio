"""Three weak tiers from one bounded observation. No history or wearable-state detector."""
import math
import statistics

LABELS = {1:'可能在休息', 2:'清醒且可能不明显活动', 3:'清醒且活动较明显'}
RULE_VERSION = 'three-tier-v1'


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def percentile(values, part):
    values=sorted(values);position=(len(values)-1)*part;low=int(position)
    return values[low]+(values[min(low+1,len(values)-1)]-values[low])*(position-low)


def motion_bins(row, sensor_type):
    if not isinstance(row,dict) or not isinstance(row.get('sensor',{}),dict):
        raise ValueError('invalid_motion_summary')
    if not row.get('ok') or row.get('sensor',{}).get('type')!=sensor_type:
        raise ValueError(row.get('status','motion_unavailable'))
    if (row.get('summaryVersion')!=1 or row.get('requestedSeconds')!=30 or row.get('truncated') is not False
            or row.get('listenersReleased') is not True or row.get('requestWakeLockHeldAtResult') is not False):
        raise ValueError('incomplete_motion_window')
    count=row.get('sampleCount');elapsed=row.get('actualElapsedMs');span=row.get('arrivalSpanMs');gap=row.get('maximumArrivalGapMs')
    invalid=row.get('invalidEvents');callbacks=row.get('callbacks')
    if (type(count) is not int or not 72<=count<=4096 or type(invalid) is not int or invalid<0
            or type(callbacks) is not int or not all(number(x) for x in (elapsed,span,gap,row.get('rms')))
            or not 29500<=elapsed<=32000 or not 29000<=span<=elapsed or not 0<=gap<=500 or row['rms']<0
            or callbacks!=count+invalid):
        raise ValueError('motion_coverage_insufficient')
    bins={};total=0
    rows=row.get('bins')
    if not isinstance(rows,list) or len(rows)>30:raise ValueError('invalid_motion_summary')
    for item in rows:
        if not isinstance(item,dict):raise ValueError('invalid_motion_summary')
        sec=item.get('second');n=item.get('count');rms=item.get('rms')
        if (type(sec) is not int or not 0<=sec<30 or sec in bins or type(n) is not int or n<=0
                or not number(rms) or rms<0):raise ValueError('invalid_motion_summary')
        bins[sec]=item;total+=n
    if total!=count:raise ValueError('invalid_motion_summary')
    return {sec:item['rms'] for sec,item in bins.items() if item['count']>=3}


def heart_summary(observations, begin_ms, end_ms):
    events={}
    for row in observations:
        stamp=row.get('receivedAtMs');bpm=row.get('value')
        if (row.get('ok') is True and number(stamp) and begin_ms<=stamp<=end_ms
                and number(bpm) and bpm>0):events[(row.get('source'),stamp)]=bpm
    ordered=sorted((key[1],value) for key,value in events.items());values=[v for _,v in ordered]
    latest=(end_ms-ordered[-1][0])/1000 if ordered else None
    sufficient=(len(values)>=3 and (ordered[-1][0]-ordered[0][0])>=10000 and latest<=15)
    half=max(1,len(values)//2)
    stable=bool(sufficient and percentile(values,.9)-percentile(values,.1)<=12
                and abs(statistics.median(values[-half:])-statistics.median(values[:half]))<=5)
    return dict(windowSeconds=round((end_ms-begin_ms)/1000,1),validReceipts=len(values),
        meanBpm=round(statistics.mean(values),1) if values else None,
        rangeBpm=[min(values),max(values)] if values else None,stable=stable,
        latestReceiptAgeSeconds=round(latest,1) if latest is not None else None)


def classify(accel,gyro,observations,begin_ms,end_ms):
    result=dict(schemaVersion=1,operation='get_user_state',ok=False,status='insufficient_data',
        tier=None,label='暂不判断',source='local_heuristic',ruleVersion=RULE_VERSION,
        accuracy='unmeasured',wearBasis='service_available_assumption',sourceAgeVerified=False,
        requestedSeconds=30,completedAtMs=end_ms)
    try:
        a=motion_bins(accel,1);g=motion_bins(gyro,4)
        starts=[row.get('watchBeginElapsedNs') for row in (accel,gyro)]
        if not all(number(x) for x in starts) or abs(starts[0]-starts[1])>500_000_000:
            raise ValueError('motion_windows_not_aligned')
        seconds=sorted(a.keys() & g.keys())
        if len(seconds)<24:raise ValueError('motion_coverage_insufficient')
    except (ValueError,TypeError,KeyError) as error:
        result['reason']=str(error)[:80];return result
    quiet=sum(a[s]<=.1 and g[s]<=.05171 for s in seconds)/len(seconds)
    strong=sum(a[s]>=.8 or g[s]>=.4 for s in seconds)/len(seconds)
    heart=heart_summary(observations,begin_ms,end_ms)
    result.update(ok=True,motion=dict(validSeconds=len(seconds),quietFraction=round(quiet,3),
        strongFraction=round(strong,3),accelerationRms=round(accel['rms'],3),rotationRms=round(gyro['rms'],3)),heartRate=heart)
    if strong>=.5:tier=3;reason='sustained_clear_motion'
    elif quiet>=.8 and strong<.1:
        tier=1 if heart['stable'] else None
        reason='quiet_with_stable_recent_heart' if tier else 'quiet_but_heart_evidence_insufficient'
    else:tier=2;reason='intermediate_motion'
    result.update(tier=tier,label=LABELS.get(tier,'暂不判断'),reason=reason,
        status='classified' if tier else 'insufficient_evidence')
    evidence=(f'{quiet:.0%}有效时间手腕活动很少' if quiet>=.8 else
              f'{strong:.0%}有效时间有明显手腕动作' if tier==3 else '有少量或间歇手腕动作')
    heart_text=(f"；本次请求内有效心率平均{heart['meanBpm']:g}次/分" if heart['meanBpm'] is not None else '；本次请求内无有效心率')
    result['summary']=f'本次30秒观察，{evidence}{heart_text}。粗略推测，准确率未测定。'
    return result
