"""Read-only, bounded receipt reconciliation after routing or interaction changes."""
from collections import deque
import threading
import time


class ReminderSync:
    BATCH_SIZE=4
    MAX_ATTEMPTS=3
    RETRY_SECONDS=15

    def __init__(self,reminders,context,clock=time.monotonic):
        self.reminders,self.context,self.clock=reminders,context,clock
        self.lock=threading.RLock();self.worker=None;self.stopped=False
        self.token=None;self.pending=deque();self.retry=[];self.retry_at=0
        self.summary=dict(status='waiting',total=0,checked=0,notFound=0,failed=0,lastCompletedAtMs=None,error=None)

    def state(self):
        with self.lock:return dict(self.summary,pending=len(self.pending)+len(self.retry))

    def tick(self):
        token=self.context()
        with self.lock:
            if self.stopped or self.worker and self.worker.is_alive():return
            if token is None:
                self.summary['status']='waiting';return
            if token==self.token and not self.pending:
                if self.retry and self.clock()<self.retry_at:
                    self.summary['status']='retrying';return
                if not self.retry:
                    self.summary['status']='partial' if self.summary['failed'] else 'complete';return
            self.worker=threading.Thread(target=self.step,name='reminder-receipt-sync',daemon=True)
            self.worker.start()

    def stop(self):
        with self.lock:self.stopped=True

    def step(self):
        token=self.context()
        with self.lock:
            if self.stopped:return
            if token is None:
                self.summary['status']='waiting';return
            if token!=self.token:
                self.token=token;self.pending=deque((identity,0) for identity in self.reminders.sync_ids());self.retry=[];self.retry_at=0
                self.summary.update(status='syncing',total=len(self.pending),checked=0,notFound=0,failed=0,error=None)
            if not self.pending and self.retry and self.clock()>=self.retry_at:
                self.pending=deque(self.retry);self.retry=[]
            if not self.pending:
                self.summary['status']='retrying' if self.retry else 'partial' if self.summary['failed'] else 'complete'
                return
            self.summary['status']='syncing'
        results=[]
        for _ in range(self.BATCH_SIZE):
            if self.stopped or self.context()!=token:break
            with self.lock:
                if not self.pending:break
                identity,attempt=self.pending.popleft()
            try:reply=self.reminders.request('reminder_status',dict(reminderId=identity))
            except (OSError,ValueError):reply=dict(status='outcome_unknown_disconnected')
            # A reply from the previous route must not become the new route's truth.
            if self.stopped or self.context()!=token:
                with self.lock:self.pending.appendleft((identity,attempt))
                break
            success=(reply.get('status')=='not_found' or reply.get('reminderId')==identity and
                     reply.get('status') not in ('interaction_disabled','interaction_sync_pending','watch_offline','outcome_unknown_disconnected','outcome_unknown_timeout'))
            with self.lock:
                if success:
                    results.append((identity,reply));self.summary['checked']+=1
                    if reply.get('status')=='not_found':self.summary['notFound']+=1
                elif attempt+1<self.MAX_ATTEMPTS:
                    self.retry.append((identity,attempt+1));self.retry_at=self.clock()+self.RETRY_SECONDS
                else:self.summary['failed']+=1
        if results:
            try:self.reminders.apply_checks(results)
            except OSError:
                with self.lock:
                    self.summary.update(status='storage_error',error=self.reminders.error)
                    # Fail closed until a service restart repairs the journal.
                    self.stopped=True
                return
        with self.lock:
            if not self.pending:
                self.summary['status']='retrying' if self.retry else 'partial' if self.summary['failed'] else 'complete'
                if not self.retry:self.summary['lastCompletedAtMs']=time.time_ns()//1000000
