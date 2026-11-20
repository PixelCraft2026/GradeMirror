"""CPU-only admission; old stage gates still run independently."""
import copy,math,time
from pathlib import Path
from .intent import need
G=1024**3

def checked(s):
    need(type(s) is dict and set(s)=={'schema','captured_at','captured_monotonic','physical','commit','legacy_process_commit_available','product','sources','diagnostics','pool','gpu','complete'} and s['schema']=='adaptive-resource-snapshot-v2','RESOURCE_SNAPSHOT_FIELDS')
    for t in ('captured_at','captured_monotonic'):need(type(s[t]) in (int,float) and math.isfinite(s[t]),'RESOURCE_CLOCK_INVALID')
    for group,keys in (('physical',('total_bytes','available_bytes')),('commit',('limit_bytes','used_bytes','headroom_bytes')),('product',('rss_bytes','private_bytes')),('pool',('resident_rss_bytes','resident_commit_bytes'))):
        for k in keys:need(s[group][k] is None or type(s[group][k]) is int and 0<=s[group][k]<2**63,'RESOURCE_COUNTER_INVALID')
    x=s['legacy_process_commit_available'];need(x is None or type(x) is int and 0<=x<2**63,'RESOURCE_COUNTER_INVALID')
    need(s['pool']['state'] in ('FREE','BUSY','RESIDENT','UNKNOWN') and (s['pool']['exited'] is None or type(s['pool']['exited']) is bool),'RESOURCE_POOL_INVALID')
    if s['pool']['exited'] is True:need(s['pool']['state']=='FREE' and s['pool']['proof'] is not None and s['pool']['resident_rss_bytes']==0 and s['pool']['resident_commit_bytes']==0,'RESOURCE_FREE_PROOF_INVALID')
    return s

def evaluate(request,snapshot,stage='plan',*,now=None):
    s=checked(snapshot);small=max(request['source']['metadata'][k] for k in ('width','height'))<=64
    resident=request['operation'] in ('resident_preview','export');ram,commit=(G,G) if resident else (4*G,6*G) if small else (6*G,16*G);peak=max(1024**2,request['source']['metadata']['width']*64*3*16) if resident else (3*G if small else 6*G)
    reasons=[];unknown=[];facts=(('physical',s['physical']['available_bytes'],ram),('system_commit',s['commit']['headroom_bytes'],commit),('legacy_process_commit',s['legacy_process_commit_available'],commit))
    if not -1<=(time.time() if now is None else now)-s['captured_at']<=60:unknown.append('SNAPSHOT_STALE')
    for name,available,needed in facts:
        if available is None:unknown.append(name.upper()+'_UNKNOWN')
        elif available<needed:reasons.append(name.upper()+'_BELOW_FLOOR:needed='+str(needed)+',available='+str(available))
    if not resident:
        if s['pool']['state']=='UNKNOWN' or s['pool']['exited'] is not True:unknown.append('POOL_EXIT_OR_USAGE_UNKNOWN')
        elif s['pool']['state']!='FREE':reasons.append('POOL_'+s['pool']['state'])
    for limit,caller,pool in (('ram_bytes','rss_bytes','resident_rss_bytes'),('commit_bytes','private_bytes','resident_commit_bytes')):
        cap=request['limits'][limit]
        if cap is not None:
            a,b=s['product'][caller],s['pool'][pool]
            if a is None or b is None:unknown.append('EXISTING_'+limit.upper()+'_UNKNOWN')
            elif a+b+peak>cap:reasons.append('USER_'+limit.upper()+'_TOO_SMALL:needed='+str(a+b+peak)+',available='+str(cap))
            elif s['product'].get('coverage')!='caller_and_all_known_workspace_processes':unknown.append('TOTAL_PRODUCT_COVERAGE_UNKNOWN')
    if request['limits']['vram_bytes'] is not None and s['gpu']['status'] not in ('ABSENT','KNOWN_CPU_NO_GPU_ALLOCATION'):
        unknown.append('EXISTING_VRAM_USAGE_UNKNOWN')
    feasible=False if reasons else None if unknown else True
    message=('资源不足：'+stage+' · '+'; '.join(reasons)) if reasons else ('读数未知，尚未开始：'+stage+' · '+'; '.join(unknown)) if unknown else '选用CPU省显存处理'
    return dict(stage=stage,resource_feasible=feasible,resource_reasons=reasons+unknown,message=message,minimum_available_ram=ram,minimum_available_commit=commit,additional_peak_bytes=peak,workspace_bytes=512*1024**2,stage_margin_bytes=G,total_limit_includes_existing=True)

def fresh(request,workspace,qualification,key,stage,*,context=None):
    from .memory_snapshot import collect
    actual=collect(workspace,pending_context=context if stage=='frame_start' else None,key=key);s=copy.deepcopy(actual)
    # Only the newly enumerated, fully authenticated factory may reduce facts.
    from .qualification import GM058_FIXTURE_FACTORY,factory_for,authorize
    if context is None:q=authorize(qualification,request)
    else:
        # register/load already validated exact build, runtime, inputs and projection.
        # Preserve the immutable qualification binding without a second full
        # executor traversal inside the original ten-second startup boundary.
        from ..cpu_replay.contract import verify
        from ..common import strict_load
        verify(context['qualification']);need(context['qualification']['path']==qualification,'RESOURCE_CONTEXT_QUALIFICATION')
        q=strict_load(Path(qualification))
    from .independent_contract import pointer as independent_pointer
    independent=independent_pointer(qualification)
    if independent and q['TEST_ONLY'] is False:override={}
    elif independent or factory_for(qualification)==GM058_FIXTURE_FACTORY:
        override=q.get('cases',{}).get(key,{}).get('resources_at_'+stage,{})
        need(type(override) is dict and set(override)<={'physical','system_commit','legacy_process_commit'},'TEST_ONLY_RESOURCE_FIELDS')
        for k,x in override.items():
            target,field=({'physical':('physical','available_bytes'),'system_commit':('commit','headroom_bytes'),'legacy_process_commit':(None,'legacy_process_commit_available')})[k]
            old=s[target][field] if target else s[field]
            need(x is None or type(x) is int and x>=0 and old is not None and x<=old,'TEST_ONLY_RESOURCE_INCREASE_FORBIDDEN')
            if target:s[target][field]=x
            else:s[field]=x
    result=evaluate(request,s,stage);result.update(snapshot=s,actual_snapshot=actual,TEST_ONLY=q['TEST_ONLY'])
    return result

def admit_context(c,stage,key=None):
    if 'resource_selection' not in c:return None
    from ..common import atomic_json,strict_load
    from ..cpu_replay.contract import verify
    verify(c['resource_selection']);row=strict_load(Path(c['resource_selection']['path']))
    value=fresh(c['request'],c['workspace'],c['qualification']['path'],key or row['request_key'],stage,context=c)
    atomic_json(Path(c['evidence_root'])/('resource-'+stage+'-'+__import__('uuid').uuid4().hex+'.json'),value)
    need(value['resource_feasible'] is True,'RESOURCE_NOT_READY',value['message']);return value
