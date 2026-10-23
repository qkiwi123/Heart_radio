"""Durable PC attempts and cached receipts. Sending is explicit and never replayed."""
import copy
import json
from pathlib import Path
import threading
import time
import uuid
from control import atomic_json
from reminder_protocol import normalize,fingerprint,TEMPLATES,validate_status


class Reminders:
    @staticmethod
    def _merge_receipt(receipt,row):
        old_sound=row.get('soundEventSeq',-1)<receipt.get('soundEventSeq',-1)
        receipt.update({k:v for k,v in row.items() if v is not None and not(old_sound and k.startswith('sound'))})
        # Deletion is terminal even if an older issued/displayed receipt arrives late.
        if receipt.get('deletedAtMs'):receipt['status']='deleted'
    def __init__(self,root,request,save=atomic_json,policy=None):
        self.path=Path(root)/'runs/reminders/ledger.json'
        self.request,self.save=request,save
        self.policy=policy
        self.lock=threading.RLock();self.rows={};self.error=None
        if self.path.exists():
            try:
                if self.path.stat().st_size>1500000:raise ValueError('Oversize reminder journal')
                value=json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(value,dict) or len(value)>128:raise ValueError('Invalid reminder journal')
                for key,row in value.items():
                    validate_status(dict(reminderId=key))
                    if (not isinstance(row,dict) or not isinstance(row.get('attemptStatus'),str) or row.get('reminderId')!=key
                            or type(row.get('createdAtMs')) is not int or type(row.get('pcExpiresAtMs')) is not int
                            or fingerprint(row.get('params'))!=row.get('payloadHash') or not isinstance(row.get('receipt',{}),dict)):
                        raise ValueError('Invalid reminder attempt')
                    if row['attemptStatus']=='sending':row['attemptStatus']='outcome_unknown_after_restart'
                self.rows=value
            except (OSError,ValueError):self.error='提醒记录无法读取，已暂停新发送'

    def _persist(self,candidate):
        try:self.save(self.path,candidate)
        except OSError:
            self.error='提醒记录保存失败，已暂停新发送';raise
        self.rows=candidate

    def observe(self,row):
        identity=row.get('reminderId')
        with self.lock:
            if identity not in self.rows:return
            candidate=copy.deepcopy(self.rows)
            record=candidate[identity]
            receipt=record.setdefault('receipt',{})
            if all(receipt.get(k)==v for k,v in row.items() if v is not None):return
            # Milestones are additive; a delayed issued reply cannot erase acknowledgement.
            self._merge_receipt(receipt,row)
            record.update(receiptReceivedAtMs=time.time_ns()//1000000)
            try:self._persist(candidate)
            except OSError:pass

    def send(self,value):
        if not isinstance(value,dict):raise ValueError('提醒必须为对象')
        value=dict(value);value.setdefault('reminderId',uuid.uuid4().hex)
        if 'ttlMs' not in value and 'expiresAtMs' not in value:value['ttlMs']=60000
        params=normalize(value);identity=params['reminderId'];signature=fingerprint(params)
        with self.lock:
            if self.error:raise OSError(self.error)
            existing=self.rows.get(identity)
            if existing:
                if existing['payloadHash']!=signature:raise ValueError('相同提醒编号已用于不同内容或参数')
                return dict(self._view(existing),duplicate=True)
            now=time.time_ns()//1000000
            if 'expiresAtMs' in params and not 0<params['expiresAtMs']-now<=300000:raise ValueError('提醒已过期或超过五分钟')
            expiry=now+params['ttlMs'] if 'ttlMs' in params else params['expiresAtMs']
            candidate=copy.deepcopy(self.rows)
            if len(candidate)>=128:
                old=next((key for key,row in candidate.items() if row['pcExpiresAtMs']<=now),None)
                if old is None:raise ValueError('提醒记录已满，请等待现有提醒到期')
                candidate.pop(old)
            candidate[identity]=dict(reminderId=identity,payloadHash=signature,params=params,createdAtMs=now,pcExpiresAtMs=expiry,attemptStatus='sending')
            self._persist(candidate)  # Intent is durable before the first wire request.
        try:
            from reminder_policy import violation
            reply=(violation(params,self.policy()) if self.policy else None) or self.request('remind',params)
        except (OSError,ValueError):reply=dict(status='outcome_unknown_disconnected')
        with self.lock:
            candidate=copy.deepcopy(self.rows);row=candidate[identity]
            row['attemptStatus']=reply.get('status','invalid_result')
            if reply.get('reason'):row['policyReason']=reply['reason']
            if reply.get('limits'):row['limits']=reply['limits']
            if reply.get('reminderId')==identity:
                self._merge_receipt(row.setdefault('receipt',{}),reply)
                row['receiptReceivedAtMs']=time.time_ns()//1000000
            self._persist(candidate)
            return self._view(self.rows[identity])

    def status(self,identity,fresh=False):
        validate_status(dict(reminderId=identity))
        if type(fresh) is not bool:raise ValueError('fresh must be boolean')
        if fresh:
            reply=self.request('reminder_status',dict(reminderId=identity))
            if reply.get('status')=='not_found':self.apply_checks([(identity,reply)])
            if reply.get('reminderId')!=identity:return dict(reminderId=identity,status=reply.get('status','invalid_result'),fresh=True)
            with self.lock:
                if identity not in self.rows:return dict(reminderId=identity,status='acknowledged' if reply.get('acknowledgedAtMs') else reply.get('status'),receipt=reply,fresh=True,cached=False,receiptAgeSeconds=0)
            self.apply_checks([(identity,reply)])
        with self.lock:
            return dict(self._view(self.rows[identity]),fresh=fresh) if identity in self.rows else dict(status='not_found',reminderId=identity,fresh=fresh)

    @staticmethod
    def _view(row):
        result=copy.deepcopy(row);receipt=result.get('receipt',{})
        result['status']=('deleted' if receipt.get('deletedAtMs') or receipt.get('status')=='deleted' else
                          'acknowledged' if receipt.get('acknowledgedAtMs') else 'cancelled' if receipt.get('cancelledAtMs') else
                          'expired' if receipt.get('expiredAtMs') else 'displayed' if receipt.get('displayedAtMs') else result['attemptStatus'])
        result['cached']=True
        result['receiptAgeSeconds']=max(0,(time.time_ns()//1000000-result['receiptReceivedAtMs'])/1000) if result.get('receiptReceivedAtMs') else None
        return result

    def apply_checks(self,results):
        """Commit one small reconciliation batch. Not found never means deleted."""
        with self.lock:
            candidate=copy.deepcopy(self.rows);changed=False;now=time.time_ns()//1000000
            for identity,reply in results:
                if identity not in candidate:continue  # May have been evicted during the query.
                row=candidate[identity]
                if reply.get('status')=='not_found':row['watchRecordStatus']='not_found'
                elif reply.get('reminderId')==identity:
                    receipt=row.setdefault('receipt',{})
                    if not all(receipt.get(k)==v for k,v in reply.items() if v is not None):
                        self._merge_receipt(receipt,reply);row['receiptReceivedAtMs']=now
                    row['watchRecordStatus']='found'
                else:continue
                row['watchCheckedAtMs']=now;changed=True
            if changed:self._persist(candidate)

    def sync_ids(self):
        with self.lock:
            return [row['reminderId'] for row in sorted(self.rows.values(),key=lambda row:row['createdAtMs'],reverse=True)
                    if self._view(row)['status']!='deleted']

    def history(self,params):
        if set(params)-{'page','pageSize','q','status'}:raise ValueError('Unknown history parameter')
        def number(name,default,maximum):
            raw=params.get(name,str(default))
            if not isinstance(raw,str) or not raw.isascii() or not raw.isdecimal():raise ValueError('Invalid '+name)
            value=int(raw)
            if not 1<=value<=maximum:raise ValueError('Invalid '+name)
            return value
        page=number('page',1,128);size=number('pageSize',20,50)
        query=params.get('q','');status=params.get('status','')
        if not isinstance(query,str) or len(query)>200 or not isinstance(status,str) or len(status)>64:raise ValueError('Invalid history filter')
        query=query.strip().casefold()
        with self.lock:
            rows=[self._view(row) for row in sorted(self.rows.values(),key=lambda row:row['createdAtMs'],reverse=True)]
            total=len(rows);statuses=sorted({row['status'] for row in rows})
            rows=[row for row in rows if (not status or row['status']==status) and (not query or
                  query in '\n'.join(str(value) for value in (row['reminderId'],row['params'].get('title',''),row['params'].get('body',''))).casefold())]
            pages=max(1,(len(rows)+size-1)//size);page=min(page,pages)
            return dict(reminders=rows[(page-1)*size:page*size],page=page,pageSize=size,pages=pages,matched=len(rows),
                        total=total,capacity=128,statuses=statuses,storageError=self.error)

    def state(self):
        with self.lock:return dict(templates=TEMPLATES,defaultTemplate='strong',storageError=self.error,
                                  total=len(self.rows),capacity=128,
                                  reminders=[self._view(row) for row in sorted(self.rows.values(),key=lambda row:row['createdAtMs'],reverse=True)[:20]])
