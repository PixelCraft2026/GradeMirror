"""Bounded fine-stage observations; no numerical imports, allocations or guards."""
import json,time,weakref
from .winproc import current_identity,process_memory,identity_key

def sample_processes(roles,*,snapshots=(),required_roles=None,include_lifetime_peaks=True):
    roles=list(roles);required=set(required_roles if required_roles is not None else(role for role,_ in roles))
    unique={};unresolved=[];unknown_denominator=False;membership_complete=True
    for snapshot in snapshots:
        membership_complete=membership_complete and snapshot['complete']
        if snapshot['reported_count']is None:unknown_denominator=True
        unresolved.extend(dict(origin='job',**row)for row in snapshot['unresolved'])
        roles.extend(('job_member',who)for who in snapshot['known_identities'])
        if not snapshot['complete']and not snapshot['unresolved']:unresolved.append(dict(origin='job',reason='incomplete_snapshot'))
    present=set()
    for role,who in roles:
        key=identity_key(who)
        if key is None:
            unresolved.append(dict(origin='role',role=role,identity=who,reason='required_identity_unknown'));continue
        present.add(role)
        if key not in unique:unique[key]=dict(identity=who,roles=[],**process_memory(who))
        if role not in unique[key]['roles']:unique[key]['roles'].append(role)
    for role in required-present:
        if not any(row.get('role')==role for row in unresolved):unresolved.append(dict(origin='role',role=role,reason='required_role_missing'))
    unknown_count=0
    for row in unresolved:
        if row.get('count',1)is None:unknown_denominator=True
        else:unknown_count+=row.get('count',1)
    rows=list(unique.values());denominator=None if unknown_denominator else len(rows)+unknown_count
    if include_lifetime_peaks:
        from .adaptive_execution.calibration_observation import process as calibration_peaks
        for row in rows:
            peaks=calibration_peaks(row['identity'])
            if peaks is not None:row['OS_lifetime_peaks']=peaks
    rss_known=sum(r['rss_bytes']is not None for r in rows);commit_known=sum(r['private_commit_bytes']is not None for r in rows)
    memory_known=sum(r['rss_bytes']is not None and r['private_commit_bytes']is not None for r in rows)
    identity_complete=bool(rows and membership_complete and not unresolved and required<=present)
    complete=identity_complete and memory_known==len(rows)
    fraction=lambda value:value/denominator if denominator else None
    partial_rss=sum(r['rss_bytes']for r in rows if r['rss_bytes']is not None)
    partial_commit=sum(r['private_commit_bytes']for r in rows if r['private_commit_bytes']is not None)
    return dict(schema='gm010-process-sample-v1',processes=rows,unresolved=unresolved,member_snapshots=list(snapshots),
        job_membership_complete=membership_complete,identity_complete=identity_complete,complete=complete,
        rss_complete=identity_complete and rss_known==len(rows),
        known_processes=rss_known,total_processes=denominator,known_identity_count=len(rows),
        identity_coverage=fraction(len(rows)),rss_coverage=fraction(rss_known),private_commit_coverage=fraction(commit_known),memory_coverage=fraction(memory_known),coverage=fraction(memory_known),
        known_partial_rss_bytes=partial_rss if rows else None,known_partial_private_commit_bytes=partial_commit if rows else None,
        tree_rss_bytes=partial_rss if complete else None,tree_private_commit_bytes=partial_commit if complete else None)

class ProcessSamples:
    """At most five samples per second; exact identities deduplicate nested Jobs."""
    def __init__(self,path):self.path=path;self.last=-1.;self.count=0;self.seconds=0.;self.peak_rss=None;self.peak_private=None
    def take(self,stage,roles,*,job_highwater=None,members_complete=True,snapshots=(),required_roles=None,measurement=None):
        at=time.monotonic()
        if at-self.last<.2:return
        self.last=at;start=time.perf_counter()
        if not members_complete:
            snapshots=(*snapshots,dict(complete=False,reported_count=None,known_identities=[],unresolved=[dict(pid=None,count=None,reason='legacy_incomplete_members')]))
        if measurement is None:
            measurement=sample_processes(roles,snapshots=snapshots,required_roles=required_roles)
        row=dict(at=time.time(),stage=stage,job_lifetime_highwater_bytes=job_highwater,**measurement)
        from .adaptive_execution.calibration_observation import facts,cuda_peaks
        extra=facts()
        if extra is not None:row.update(calibration_system=extra,calibration_cuda=cuda_peaks())
        if row['complete']:
            self.peak_rss=max(self.peak_rss or 0,row['tree_rss_bytes']);self.peak_private=max(self.peak_private or 0,row['tree_private_commit_bytes'])
        row.update(complete_sample_peak_rss_bytes=self.peak_rss,complete_sample_peak_private_commit_bytes=self.peak_private)
        self.count+=1
        row.update(observer_samples=self.count,observer_seconds=self.seconds,short_peaks_may_be_missed=True)
        with self.path.open('a',encoding='utf-8')as stream:stream.write(json.dumps(row,separators=(',',':'))+'\n')
        self.seconds+=time.perf_counter()-start

class BufferObservation:
    """Weak refs only: aliases share one capacity entry, dead objects stay dead."""
    def __init__(self,path,*,role='numeric'):self.path=path;self.refs={};self.count=0;self.seconds=0.;self.role=role
    def track(self,name,array):self.refs[name]=weakref.ref(array)
    def event(self,event,cuda=None):
        start=time.perf_counter();arrays=[];buffers={}
        for name,ref in self.refs.items():
            a=ref()
            if a is None:arrays.append(dict(name=name,alive=False));continue
            address=int(a.__array_interface__['data'][0]);key=(address,a.nbytes)
            buffers[key]=a.nbytes
            arrays.append(dict(name=name,alive=True,object_id=id(a),buffer_address=address,bytes=a.nbytes,shape=list(a.shape),dtype=str(a.dtype),owns=bool(a.flags.owndata)))
            del a
        self.count+=1
        row=dict(event=event,at=time.time(),arrays=arrays,unique_live_capacity_bytes=sum(buffers.values()),
            process=sample_processes([(self.role,current_identity())]),cuda=cuda)
        row.update(observer_events=self.count,observer_seconds=self.seconds)
        with self.path.open('a',encoding='utf-8')as stream:stream.write(json.dumps(row,separators=(',',':'))+'\n')
        self.seconds+=time.perf_counter()-start
