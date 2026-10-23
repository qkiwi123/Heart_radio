"""Human-edited reminder limits; two-author causality, stricter concurrent values, no wall clock."""
import copy
import json
from pathlib import Path
import re
import uuid
from control import atomic_json

DEFAULTS=dict(vibrationEnabled=True,maxVibrationMs=6000,wakeEnabled=True,maxScreenSeconds=30,soundEnabled=False)


def stricter(a,b):return {k:a[k] and b[k] if type(v) is bool else min(a[k],b[k]) for k,v in DEFAULTS.items()}


def validate_settings(value):
    if not isinstance(value,dict) or set(value) not in (set(DEFAULTS),set(DEFAULTS)-{'soundEnabled'}):raise ValueError('Invalid reminder settings fields')
    value=dict(value);value.setdefault('soundEnabled',False)
    for key in ('vibrationEnabled','wakeEnabled','soundEnabled'):
        if type(value[key]) is not bool:raise ValueError('Reminder permission must be boolean')
    for key,low,high in [('maxVibrationMs',1000,10000),('maxScreenSeconds',5,30)]:
        if type(value[key]) is not int or not low<=value[key]<=high:raise ValueError('Reminder limit outside range')
    return dict(value)


def validate_wire(value):
    if not isinstance(value,dict) or set(value)!={'pc','watch'}:raise ValueError('Invalid policy register')
    value=copy.deepcopy(value)
    for e in value.values():
        if e is None:continue
        if not isinstance(e,dict) or set(e)!={'seq','seen','id','value'}:raise ValueError('Invalid policy event')
        for key,low in [('seq',1),('seen',0)]:
            if type(e[key]) is not int or not low<=e[key]<=2147483647:raise ValueError('Invalid policy version')
        if not isinstance(e['id'],str) or not re.fullmatch('[0-9a-f]{32}',e['id']):raise ValueError('Invalid policy identity')
        e['value']=validate_settings(e['value'])
    p,w=value['pc'],value['watch']
    if p and p['seen']>(w or {}).get('seq',0) or w and w['seen']>(p or {}).get('seq',0):raise ValueError('Missing policy history')
    if p and w and p['seen']>=w['seq'] and w['seen']>=p['seq']:raise ValueError('Causal policy cycle')
    return value


class PolicyRegister:
    def __init__(self,author):
        if author not in ('pc','watch'):raise ValueError('Invalid policy author')
        self.author=author;self.events=dict(pc=None,watch=None)
    def wire(self):return copy.deepcopy(self.events)
    def fingerprint(self):return ':'.join(f"{(e or {}).get('seq',0)}-{(e or {}).get('id','')}" for e in self.events.values())
    def effective(self):
        p,w=self.events.values()
        active=[e for e in (p,w) if e]
        if p and w:
            if p['seen']>=w['seq']:active=[p]
            elif w['seen']>=p['seq']:active=[w]
        if not active:return dict(DEFAULTS)
        return {k:all(e['value'][k] for e in active) if type(v) is bool else min(e['value'][k] for e in active) for k,v in DEFAULTS.items()}
    def edit(self,value):
        value=validate_settings(value);peer='watch' if self.author=='pc' else 'pc'
        seq=(self.events[self.author] or {}).get('seq',0)+1
        if seq>2147483647:raise ValueError('Policy version exhausted')
        self.events[self.author]=dict(seq=seq,seen=(self.events[peer] or {}).get('seq',0),id=uuid.uuid4().hex,value=value)
    def merge(self,value):
        incoming=validate_wire(value);candidate=self.wire()
        for author,e in incoming.items():
            old=candidate[author]
            if e and (old is None or e['seq']>old['seq']):candidate[author]=e
            elif e and old and e['seq']==old['seq'] and e!=old:raise ValueError('Conflicting policy identity')
        self.events=validate_wire(candidate)


class PolicyStore:
    def __init__(self,path,author='pc',save=atomic_json):
        self.path=Path(path);self.save=save;self.register=PolicyRegister(author);self.error=None
        if self.path.exists():
            try:
                if self.path.stat().st_size>16384:raise ValueError('Policy record too large')
                self.register.merge(json.loads(self.path.read_text(encoding='utf-8'))['state'])
            except (OSError,ValueError,KeyError,TypeError):self.error='提醒设置无法恢复，已暂停新提醒，请恢复设置备份'
    def transact(self,operation):
        if self.error:raise OSError(self.error)
        candidate=copy.deepcopy(self.register);operation(candidate)
        if candidate.events!=self.register.events or not self.path.exists():
            try:self.save(self.path,dict(state=candidate.wire()))
            except (OSError,ValueError):self.error='提醒设置保存失败，已暂停新提醒';raise
        self.register=candidate


def violation(params,settings):
    settings=validate_settings(settings);reason=None
    if params['vibrate']:
        v=params['vibration'];total=v['pulseMs']*v['count']+v['gapMs']*(v['count']-1)
        if not settings['vibrationEnabled']:reason='vibration_disabled_by_user'
        elif total>settings['maxVibrationMs']:reason='vibration_exceeds_user_limit'
    if params['screen']:
        if not settings['wakeEnabled']:reason='wake_disabled_by_user'
        elif params['screenSeconds']>settings['maxScreenSeconds']:reason='screen_exceeds_user_limit'
    if params.get('sound',False) and not settings['soundEnabled']:reason='sound_disabled_by_user'
    return dict(status='reminder_policy_denied',reason=reason,limits=settings) if reason else None
