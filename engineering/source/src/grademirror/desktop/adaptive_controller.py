"""Qt owns presentation epochs; the application service owns all execution."""
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6.QtCore import QObject,Signal,QTimer

class AdaptiveController(QObject):
    updated=Signal(object)
    completed=Signal(object)
    failed=Signal(object)
    _delivered=Signal(object)
    def __init__(self,backend,parent=None):
        super().__init__(parent);self.backend=backend;self.epoch=0;self.workspace=None;self.request=None
        self.qualification=None;self.result=None;self.key=None;self.active=None;self.busy=False
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='adaptive-ui');self.futures=set()
        self.lease_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='adaptive-lease')
        self.renew_due=None;self.renew_inflight=False
        self.started=None;self.last_event=None;self.polling=False;self.preview_generation=0
        self.cancel_requested=False;self.cancel_inflight=False;self.cancel_completed=False;self.active_child=None;self.last_progress=None;self.progress_event=None
        self._delivered.connect(self._accept);self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self._tick);self.timer.start()
    def configure(self,request,workspace,qualification=None):
        previous=(self.workspace,self.result)
        self.epoch+=1;self.request=request;self.workspace=Path(workspace);self.qualification=qualification
        self.result=None;self.active=None;self.key=None;self.busy=False;self.preview_generation+=1
        self.renew_due=None;self.renew_inflight=False
        self.cancel_requested=False;self.cancel_inflight=False;self.cancel_completed=False;self.polling=False;self.active_child=None;self.last_progress=None;self.progress_event=None;self.started=None;self.last_event=None
        if previous[0] and previous[1] and previous[1].get('resident'):
            value=previous[1];self._call('retire',lambda:self.backend.adaptive_call(previous[0],'release',value['job_id'],value['attempt_id'],binding=value['binding']))
        self.updated.emit(dict(state='READY',native_execution_qualified=False))
    def _call(self,action,function,*,token=None):
        epoch=self.epoch;workspace=self.workspace
        executor=self.lease_pool if action=='keep_alive' else self.pool
        future=executor.submit(function);self.futures.add(future)
        def done(f):
            try:value=f.result();error=None
            except BaseException as e:value=None;error=dict(error_code=getattr(e,'code','ADAPTIVE_UI_ERROR'),message=str(e),execution_stage=getattr(e,'execution_stage',None),preserved_recipes=getattr(e,'preserved_recipes',[]),notes=getattr(e,'__notes__',[]))
            self._delivered.emit(dict(epoch=epoch,workspace=workspace,action=action,value=value,error=error,token=token,_future=f))
        future.add_done_callback(done);return future
    def submit(self,key=None):
        if self.busy or self.futures:return
        self.key=key if isinstance(key,str) else str(uuid.uuid4());self.busy=True;self.started=time.monotonic();self.last_event=self.started
        request=self.request;workspace=self.workspace;qualification=self.qualification;key=self.key;self.cancel_requested=False;self.cancel_completed=False
        self._call('submit',lambda:self.backend.adaptive_submit(self.backend.adaptive_plan(request,qualification=qualification,workspace=workspace),key,workspace,experimental=True,qualification=qualification))
    def _accept(self,row):
        # The GUI owns the pending set until its queued callback is delivered.
        self.futures.discard(row.get('_future'))
        row={k:v for k,v in row.items() if k!='_future'}
        if row['epoch']!=self.epoch:
            value=row['value']
            if row['action']=='submit' and value and value.get('resident'):
                self._call('retire',lambda:self.backend.adaptive_call(row['workspace'],'release',value['job_id'],value['attempt_id'],binding=value['binding']))
            return
        action=row['action']
        if action=='keep_alive':self.renew_inflight=False
        if action=='poll':self.polling=False
        if action=='cancel':self.cancel_inflight=False
        if action=='preview' and row['token']!=self.preview_generation:return
        if row['error']:
            if action=='keep_alive' or (action in ('preview','export') and row['error']['error_code'] in (
                'RESULT_NOT_RESIDENT','RESULT_LEASE_EXPIRED','CPU_RESULT_BINDING_STALE','RESULT_OWNER_UNKNOWN')):
                self._resident_lost(row['error'])
                return
            if action=='cancel':self.cancel_completed=True
            if action in ('submit','release'):self.busy=False
            self.failed.emit(dict(row['error'],stage=row['error'].get('execution_stage') or action,preserved_recipe=(self.result or {}).get('recipe'),next_step='检查资格或资源；已提交配方可重开，不自动重算。'));return
        value=row['value']
        if action=='cancel':self.cancel_completed=not(value.get('cancel_pending') or value.get('cancel_disposition')=='STALE_CHILD_NOT_CANCELLED')
        if action=='poll':
            self.active_child=value.get('active_child')
            if value['event_id']!=self.progress_event:self.last_event=time.monotonic();self.progress_event=value['event_id']
            self.last_progress=value
            return
        self.last_event=time.monotonic()
        if action=='submit':
            self.result=value;self.active=(value['job_id'],value['attempt_id']);self.busy=False
            self.renew_due=time.monotonic()+30 if value.get('resident') and value.get('lease_policy',{}).get('schema') in ('application-idle-lease-v1','application-native-idle-lease-v1') else None
        if action=='keep_alive':self.renew_due=time.monotonic()+30
        if action=='release':self.result=None;self.active=None;self.busy=False;self.renew_due=None
        if action=='submit' and self.cancel_requested and value.get('resident'):
            self.release()
        self.updated.emit(dict(state=action,result=value));self.completed.emit(row)
    def _tick(self):
        elapsed=0 if self.started is None else time.monotonic()-self.started
        waited=0 if self.last_event is None else time.monotonic()-self.last_event
        tick=dict(ui_elapsed_seconds=elapsed,ui_waited_seconds=waited,worker_heartbeat=False)
        if self.busy and self.last_progress:tick.update(state='progress',result=self.last_progress)
        self.updated.emit(tick)
        if (self.result and self.result.get('resident') and self.renew_due is not None and
            time.monotonic()>=self.renew_due and not self.renew_inflight and not self.cancel_requested):
            self.renew_inflight=True;workspace=self.workspace;value=self.result
            self._call('keep_alive',lambda:self.backend.adaptive_call(workspace,'keep_alive',
                value['job_id'],value['attempt_id'],binding=value['binding']))
        if self.busy and self.cancel_requested and not self.cancel_inflight and not self.cancel_completed:self._cancel_current()
        if not self.busy or self.polling or not self.workspace:return
        self.polling=True;workspace=self.workspace;key=self.key
        def progress():
            from ..adaptive_execution.service import progress as read_progress
            return read_progress(workspace,key)
        self._call('poll',progress)
    def cancel(self):
        self.renew_due=None
        self.cancel_requested=True;self.cancel_completed=False
        if not self.cancel_inflight:self._cancel_current()
    def _cancel_current(self):
        if self.cancel_completed or not self.workspace or not self.key:return
        self.cancel_inflight=True
        workspace=self.workspace;key=self.key;expected=None if self.active_child is None else dict(self.active_child)
        def cancel_current():
            from ..adaptive_execution.service import locate
            pair=locate(workspace,key)
            if pair is None:return dict(state='NOT_STARTED',cancel_pending=True)
            return self.backend.adaptive_call(workspace,'cancel',*pair,expected_child=expected)
        self._call('cancel',cancel_current)
    def preview(self,alpha):
        if not self.result or not self.result.get('resident'):return
        self.preview_generation+=1;generation=self.preview_generation;value=self.result;workspace=self.workspace
        destination=workspace/'exports'/('preview-'+str(uuid.uuid4())+'.jpg')
        self._call('preview',lambda:self.backend.adaptive_call(workspace,'preview',value['job_id'],value['attempt_id'],
            binding=value['binding'],alpha=alpha,destination=str(destination),request_id=str(uuid.uuid4())),token=generation)
    def export(self,destination,alpha):
        if not self.result:return
        value=self.result;workspace=self.workspace
        self._call('export',lambda:self.backend.adaptive_call(workspace,'export',value['job_id'],value['attempt_id'],
            binding=value['binding'],alpha=alpha,destination=str(destination),request_key=str(uuid.uuid4())))
    def release(self):
        if not self.result:return
        self.renew_due=None
        value=self.result;workspace=self.workspace
        self._call('release',lambda:self.backend.adaptive_call(workspace,'release',value['job_id'],value['attempt_id'],binding=value['binding']))
    def shutdown(self):
        self.renew_due=None;self.timer.stop();self.pool.shutdown(wait=False,cancel_futures=False)
        self.lease_pool.shutdown(wait=False,cancel_futures=False)

    def _resident_lost(self,error):
        value=self.result;self.renew_due=None;self.preview_generation+=1
        if value:
            self.result=dict(value,resident=False,availability='REPLAY_REQUIRED')
            workspace=self.workspace
            self._call('resident_cleanup',lambda:self.backend.adaptive_call(workspace,'release',
                value['job_id'],value['attempt_id'],binding=value['binding']))
        self.updated.emit(dict(state='结果已释放，需要重放',result=self.result))
        self.failed.emit(dict(error,stage=error.get('execution_stage') or 'resident',
            original_message=error['message'],message=error['message']+'；结果已释放，需要重放',
            preserved_recipe=(value or {}).get('recipe'),next_step='可重开已保存配方，不自动重算。'))
