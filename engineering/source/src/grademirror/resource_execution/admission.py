"""Fresh locked admission and full process-tree observations; no CUDA context."""
import ctypes as ct,os,time
from contextlib import contextmanager
from pathlib import Path
from ..common import GMError,strict_load,atomic_json
from ..store import locked
from ..hybrid_replay.audit import append
from .contract import require,guarded,MIB
from . import binding,planning

def process_tree(roots):
    """Double Toolhelp inventory; every listed descendant needs exact identity/counters."""
    from .. import winproc as win
    from ..fine_observation import sample_processes
    class Entry(ct.Structure):
        _fields_=[('size',ct.c_ulong),('usage',ct.c_ulong),('pid',ct.c_ulong),('heap',ct.c_size_t),('module',ct.c_ulong),
            ('threads',ct.c_ulong),('parent',ct.c_ulong),('priority',ct.c_long),('flags',ct.c_ulong),('exe',ct.c_wchar*260)]
    k=ct.WinDLL('kernel32',use_last_error=True);k.CreateToolhelp32Snapshot.argtypes=[ct.c_ulong,ct.c_ulong];k.CreateToolhelp32Snapshot.restype=ct.c_void_p
    for name in ('Process32FirstW','Process32NextW'):
        fn=getattr(k,name);fn.argtypes=[ct.c_void_p,ct.POINTER(Entry)];fn.restype=ct.c_int
    def inventory():
        h=k.CreateToolhelp32Snapshot(2,0);require(h not in (None,ct.c_void_p(-1).value),'RESOURCE_TREE_UNKNOWN')
        try:
            e=Entry();e.size=ct.sizeof(e);ok=k.Process32FirstW(h,ct.byref(e));require(ok,'RESOURCE_TREE_UNKNOWN');rows={}
            while ok:rows[int(e.pid)]=int(e.parent);ok=k.Process32NextW(h,ct.byref(e))
            require(ct.get_last_error()==18,'RESOURCE_TREE_UNKNOWN');return rows
        finally:win.close(h)
    def members(rows):
        pids={r['pid'] for r in roots};last=None
        while last!=pids:last=set(pids);pids.update(p for p,parent in rows.items() if parent in pids)
        return pids
    a=inventory();pids=members(a);identities=[win.process_identity(p) for p in sorted(pids)]
    require(all(win.identity_key(i) is not None for i in identities) and members(inventory())==pids,'RESOURCE_TREE_UNKNOWN')
    require(all(any(win.identity_key(i)==win.identity_key(r) for i in identities) for r in roots),'RESOURCE_TREE_UNKNOWN')
    return sample_processes([('product',i) for i in identities])

def collect(pool, path):
    from ..resource_planning.snapshot import collect_snapshot
    from .. import winproc as win
    snapshot=collect_snapshot();roots=[win.current_identity()]
    with locked(pool.lock_path):
        row=pool._read()
        if row is not None and row['state']!='FREE':
            require(row['state']=='BOUND' and row.get('owner') and row.get('host'),'RESOURCE_RESIDENT_UNKNOWN')
            require(all(win.identity_alive(row[k]) is True for k in ('owner','host')),'RESOURCE_RESIDENT_UNKNOWN')
            require(Path(row['session_path']).parent.resolve()==Path(path).resolve(),'RESIDENT_POOL_BUSY')
            roots += [row['owner'],row['host']]
    tree=process_tree(roots)
    snapshot.update(product=dict(rss_bytes=tree['tree_rss_bytes'],private_bytes=tree['tree_private_commit_bytes'],coverage='full_tree' if tree['complete'] else 'unknown'),product_tree=tree)
    snapshot['complete']['product']=tree['complete'];snapshot['sources']['product']='Toolhelp full descendants + original pool identities + exact process counters'
    return snapshot

def assess(plan,snapshot,stage,*,now=None):
    now=(time.time(),time.monotonic()) if now is None else now
    require(snapshot['machine_id']==snapshot['clock']['machine_id'] and abs(now[0]-snapshot['clock']['wall'])<=5 and
        0<=now[1]-snapshot['collected_monotonic']<=5 and -1<=now[0]-snapshot['collected_wall']<=5,'RESOURCE_SNAPSHOT_STALE')
    require(all(snapshot['complete'].values()) and snapshot['product']['coverage']=='full_tree' and
        snapshot['product_tree']['complete'],'RESOURCE_SNAPSHOT_UNKNOWN')
    numbers=[snapshot['physical']['total_bytes'],snapshot['physical']['available_bytes'],*snapshot['commit'].values(),snapshot['product']['rss_bytes'],snapshot['product']['private_bytes']]
    require(all(type(n) is int and n>=0 for n in numbers),'RESOURCE_SNAPSHOT_UNKNOWN')
    require(snapshot['physical']['available_bytes']<=snapshot['physical']['total_bytes'],'RESOURCE_SNAPSHOT_UNKNOWN')
    require(snapshot['commit']['headroom_bytes']==snapshot['commit']['limit_bytes']-snapshot['commit']['used_bytes'],'RESOURCE_COMMIT_UNKNOWN')
    wanted=[g for g in snapshot['gpus'] if g['uuid']==plan['device']['uuid']]
    require(snapshot['cuda_available'] is not False and len(wanted)==1,'CUDA_DEVICE_UNAVAILABLE','CUDA unavailable; CPU R0 is not qualified')
    gpu=wanted[0];require(gpu['name']==plan['device']['name'] and gpu['driver']==plan['device']['driver_version'],'REPLAY_DEVICE_CHANGED')
    row=next((r for r in plan['stages'] if r['stage']==stage),None);require(row is not None,'RESOURCE_PLAN_STAGE')
    product=snapshot['product'];ram=row['incremental_physical_upper_bytes'];commit=row['incremental_commit_upper_bytes']
    if not row['incremental']:
        ram=max(0,ram-product['rss_bytes']);commit=max(0,commit-product['private_bytes'])
    total_ram=product['rss_bytes']+ram;total_commit=product['private_bytes']+commit
    for key,total in (('ram_limit_mib',total_ram),('commit_limit_mib',total_commit),('vram_limit_mib',row['vram_reserved_bytes'])):
        limit=plan['resource_intent'][key];require(limit is None or total<=limit*MIB,'RESOURCE_USER_LIMIT',stage+': '+key)
    raw=gpu.get('raw_cuda_free_bytes');nv=gpu['nvml_free_bytes'];total=gpu['total_bytes'];required=row['vram_required_free_bytes']
    require(type(nv) is int and type(total) is int and 0<=nv<=total,'RESOURCE_NVML_UNKNOWN')
    require(row['vram_reserved_bytes']*5<=total*4,'RESOURCE_ALLOCATOR_LIMIT')
    if raw is not None:require(gpu['raw_cuda_uuid']==gpu['uuid'] and 0<=raw<=total,'RESOURCE_RAWCUDA_IDENTITY')
    available=nv if raw is None else min(raw,nv)
    ok=snapshot['physical']['available_bytes']>=ram and snapshot['commit']['headroom_bytes']>=commit and available>=required
    return dict(admitted=ok,stage=stage,required_physical=ram,required_commit=commit,required_vram=required,
        available_physical=snapshot['physical']['available_bytes'],available_commit=snapshot['commit']['headroom_bytes'],available_vram=available,
        user_limits=plan['resource_intent'],tree_total_ram=total_ram,tree_total_commit=total_commit,
        source=row['estimate_source'],free_does_not_subtract_existing_twice=True,rawCUDA_worker_confirmation_required=raw is None)

def prepare(path,inputs,source,reference,*,pool,legacy_plan=None):
    path=Path(path);spec=strict_load(path/'spec.json')
    if not guarded(spec):return None
    snapshot=collect(pool,path)
    plan=planning.build(spec,inputs,source,reference,snapshot,legacy_plan=legacy_plan)
    binding.bind(path,spec,plan)
    recheck(path,plan['stages'][0]['stage'],pool=pool)
    return binding.pointer(path)

def remaining(path, original):
    path=Path(path)
    if not(path/'spec.json').exists() or not guarded(strict_load(path/'spec.json')):return original
    spec=strict_load(path/'spec.json');file=path/'guarded-wait.json';used=strict_load(file)['seconds'] if file.exists() else 0.
    left=min(float(original),30.-used,spec['operation_deadline_at']-time.time())
    require(left>0,'RESOURCE_WAIT_CAP','Guarded resource wait cumulative limit is 30 seconds')
    return left

@contextmanager
def wait_budget(path, original):
    path=Path(path);limit=remaining(path,original);active=(path/'spec.json').is_file() and guarded(strict_load(path/'spec.json'));start=time.monotonic()
    try:yield limit
    finally:
        if active:
            file=path/'guarded-wait.json';old=strict_load(file)['seconds'] if file.exists() else 0.
            atomic_json(file,dict(seconds=old+time.monotonic()-start,limit=30))

@contextmanager
def reservation_lock(path,lock,original=120):
    path=Path(path)
    if not guarded(strict_load(path/'spec.json')):
        with locked(lock,timeout=original):yield
        return
    acquired=None
    with wait_budget(path,original) as limit:
        start=time.monotonic()
        while acquired is None:
            if (path/'cancel.signal').exists():raise GMError('CANCELLED','Resource reservation cancelled',8)
            require(time.monotonic()-start<limit,'RESOURCE_WAIT_CAP','Global reservation lock')
            attempt=locked(lock,nonblocking=True)
            try:attempt.__enter__();acquired=attempt
            except GMError as exc:
                if exc.code!='LOCK_BUSY':raise
                time.sleep(.025)
    try:yield
    finally:acquired.__exit__(None,None,None)

def recheck(path,stage,*,pool=None):
    path=Path(path);spec=strict_load(path/'spec.json')
    if not guarded(spec):return None
    from ..gm004a_api import _resident_pool
    plan=binding.validate(path,spec,stage=stage);pool=_resident_pool() if pool is None else pool
    with wait_budget(path,30) as limit:
        start=time.monotonic();last_progress=-1.
        while True:
            if (path/'cancel.signal').exists():raise GMError('CANCELLED','Guarded resource wait cancelled',8)
            snapshot=collect(pool,path);result=assess(plan,snapshot,stage)
            with locked(path/'record.lock'):
                append(path/'resource-stage-checks.jsonl',dict(plan_sha256=plan['plan_sha256'],wall=time.time(),snapshot=snapshot,**result))
                if result['admitted'] or time.monotonic()-last_progress>=1:
                    from ..gm004a_operations import _journal,_append
                    current=dict(_journal(path)[-1]['snapshot'])
                    _append(path,dict(current,detail_stage='RESOURCE_ADMITTED' if result['admitted'] else 'RESOURCE_WAIT',
                        resource_wait=dict(plan_sha256=plan['plan_sha256'],**result)),
                        'RESOURCE_ADMITTED' if result['admitted'] else 'RESOURCE_WAIT')
                    last_progress=time.monotonic()
            if result['admitted']:return result
            require(time.monotonic()-start<limit,'RESOURCE_WAIT_CAP',stage)
            time.sleep(min(.25,max(0,limit-(time.monotonic()-start))))
