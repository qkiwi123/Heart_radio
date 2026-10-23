"""Shared reminder request bounds for both adapters. No I/O, retries or permissions."""
import hashlib
import json
import re

PROFILES={'light':dict(amplitude=96,pulseMs=120,gapMs=120,count=2),
          'standard':dict(amplitude=192,pulseMs=220,gapMs=160,count=2),
          'strong':dict(amplitude=255,pulseMs=360,gapMs=220,count=2)}
TEMPLATES={name:dict(vibrate=name not in ('notification_only','silent'),screen=name!='notification_only',sound=False,
                     vibration=dict(PROFILES.get(name,PROFILES['strong'])))
           for name in ('notification_only','silent','light','standard','strong')}
FIELDS={'reminderId','title','body','template','vibrate','screen','strength','vibration','screenSeconds','ttlMs','expiresAtMs','sound','soundSource'}


def integer(value,low,high,name):
    if type(value) is not int or not low<=value<=high:raise ValueError(f'{name} outside {low}..{high}')
    return value


def normalize(value):
    if not isinstance(value,dict) or set(value)-FIELDS:raise ValueError('Unknown reminder fields')
    result=dict(value)
    if not isinstance(result.get('reminderId'),str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',result['reminderId']):raise ValueError('Invalid reminderId')
    for key,limit in [('title',60),('body',500)]:
        text=result.get(key)
        if not isinstance(text,str) or not text.strip() or len(text)>limit or any(ord(c)<32 and c not in '\n\t' or 0xd800<=ord(c)<=0xdfff for c in text):
            raise ValueError(f'{key} must contain 1..{limit} plain text characters')
    template=result.get('template','strong')
    if not isinstance(template,str) or template not in TEMPLATES:raise ValueError('Invalid reminder template')
    strength=result.get('strength',template if template in PROFILES else 'strong')
    if not isinstance(strength,str) or strength not in PROFILES:raise ValueError('Invalid reminder strength')
    for key in ('vibrate','screen','sound'):
        result.setdefault(key,TEMPLATES[template][key])
        if type(result[key]) is not bool:raise ValueError(f'{key} must be boolean')
    result.setdefault('soundSource','system')
    if result['soundSource']!='system':raise ValueError('Custom sound is not implemented' if result['soundSource']=='custom' else 'Unknown sound source')
    custom=result.get('vibration',{})
    if not isinstance(custom,dict) or set(custom)-{'amplitude','pulseMs','gapMs','count'}:raise ValueError('Invalid vibration fields')
    pattern=dict(PROFILES[strength],**custom)
    for key,low,high in [('amplitude',1,255),('pulseMs',20,1000),('gapMs',0,2000),('count',1,8)]:integer(pattern[key],low,high,key)
    if pattern['pulseMs']*pattern['count']+pattern['gapMs']*(pattern['count']-1)>10000:raise ValueError('Vibration sequence exceeds ten seconds')
    if ('ttlMs' in result)==('expiresAtMs' in result):raise ValueError('Provide ttlMs or expiresAtMs')
    if 'ttlMs' in result:integer(result['ttlMs'],1,300000,'ttlMs')
    else:integer(result['expiresAtMs'],1,9007199254740991,'expiresAtMs')
    result['screenSeconds']=integer(result.get('screenSeconds',15),5,30,'screenSeconds')
    result.update(template=template,strength=strength,vibration=pattern)
    return result


def fingerprint(value):
    value=normalize(value);v=value['vibration']
    canonical=[value['title'],value['body'],value['vibrate'],value['screen'],v['amplitude'],v['pulseMs'],v['gapMs'],v['count'],value['screenSeconds']]
    if value['sound']:canonical.extend([True,value['soundSource']])
    return hashlib.sha256(json.dumps(canonical,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def validate_status(value):
    if not isinstance(value,dict) or set(value)!={'reminderId'} or not isinstance(value['reminderId'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',value['reminderId']):
        raise ValueError('Invalid reminder status identity')
    return value
