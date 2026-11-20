"""Task-local registry using the unchanged v4 execution protocol structure."""
from pathlib import Path
from ..common import strict_load,atomic_json,reject_reparse
from ..cpu_replay.contract import FLAGS,binding,verify,need
from ..store import locked
from . import calibration_contract as k
from .first_context import FIELDS,paths


def phase(request):
    if request['operation']=='fit':return 'fit'
    if request['operation']=='refine':return 'refine'
    return 'fast_recovery' if request['quality']=='fast' else 'replay_fine'


def register(workspace,request,qualification,identifier,mode,**kw):
    need(set(kw)<={'fault','pause','parent','resource_selection'} and not any(kw.values()),'GM063_NO_TEST_INSTRUCTIONS')
    q=k.authorize(qualification,request);ws=Path(workspace).resolve();need(ws.is_relative_to(k.W),'GM063_WORKSPACE_SCOPE')
    reject_reparse(ws);out=ws/'operations'/identifier;out.mkdir(parents=True,exist_ok=False)
    p=ws/'.adaptive/contexts'/(identifier+'.json');ph=phase(request)
    a=strict_load(k.E/'allocations.json')['allocations'][q['allocations'][ph]]
    c=dict(schema='cpu-application-execution-v4',id=identifier,phase=ph,mode=mode,TEST_ONLY=False,metadata_only=False,
        workspace=str(ws),manifest_path=str(p),request=request,executor_build=q['executor_build'],output_root=str(out),
        evidence_root=str(ws/'.adaptive/logs'/identifier),pool_root=str(ws/'pool'),qualification=binding(qualification),
        flags=FLAGS,fault_point='',fault_action='raise',pause_at='',prepared_clock_shift=0,lease_seconds=120,
        request_seconds=120,force_grace_seconds=30,bundle=q['parent_package']['directory'] if request['operation']=='replay' else str(out/('fast-recipe' if ph=='fit' else 'fine-recipe')),
        paths=paths(ws,out),parent_operation=None,lease_policy=q['lease_policy'],runtime=q['runtime'],interpreters=q['interpreters'],
        science_allocation=q['allocations'][ph],science=a['budget'],cancel_after_updates=0,parent_bundle=q['parent_package'])
    c.update({key:q[key]for key in('source','reference','source_identity','reference_identity','settings','profile','color_policy')})
    atomic_json(p,c)
    with locked(ws/'.adaptive/registry.lock'):
        ixp=ws/'.adaptive/context-index.json';ix=strict_load(ixp)if ixp.exists()else{}
        need(identifier not in ix,'GM063_CONTEXT_REUSED');ix[identifier]=binding(p);atomic_json(ixp,ix)
    return c


def load(path,*,cleanup=False):
    p=Path(path).resolve();reject_reparse(p)
    need(p.is_relative_to(k.W) and p.parent.name=='contexts' and p.parent.parent.name=='.adaptive','GM063_CONTEXT_POINTER')
    c=strict_load(p);need(set(c)==FIELDS and c['schema']=='cpu-application-execution-v4' and c['manifest_path']==str(p),
        'GM063_CONTEXT_FIELDS')
    need(strict_load(p.parent.parent/'context-index.json').get(c['id'])==binding(p),'GM063_CONTEXT_INDEX')
    q=k.authorize(c['qualification']['path'],c['request'],cleanup=cleanup);ws=p.parent.parent.parent;out=ws/'operations'/c['id']
    ph=phase(c['request']);allocation=q['allocations'][ph];a=strict_load(k.E/'allocations.json')['allocations'][allocation]
    need(c['phase']==ph and c['mode']==c['request']['quality'] and c['TEST_ONLY'] is False and c['metadata_only'] is False and c['flags']==FLAGS,'GM063_CONTEXT_PHASE')
    need(c['workspace']==str(ws) and c['output_root']==str(out) and c['paths']==paths(ws,out) and c['pool_root']==str(ws/'pool') and
         c['evidence_root']==str(ws/'.adaptive/logs'/c['id']),'GM063_CONTEXT_PATHS')
    need(all(c[key]==q[key]for key in('source','reference','source_identity','reference_identity','settings','profile','color_policy','runtime','interpreters','executor_build','lease_policy')),
         'GM063_CONTEXT_BINDINGS')
    need(c['qualification']==binding(q['manifest_path']) and c['science_allocation']==allocation and c['science']==a['budget'] and c['parent_bundle']==q['parent_package'],
         'GM063_CONTEXT_ALLOCATION')
    expected=q['parent_package']['directory'] if c['request']['operation']=='replay' else str(out/('fast-recipe' if ph=='fit' else 'fine-recipe'))
    need(c['bundle']==expected and c['parent_operation'] is None and c['prepared_clock_shift']==0 and c['fault_point']=='' and c['pause_at']=='' and c['fault_action']=='raise' and
         c['cancel_after_updates']==0 and c['lease_seconds']==120 and c['request_seconds']==120 and c['force_grace_seconds']==30,'GM063_CONTEXT_LIMITS')
    return c


def semantics(c,sem):
    r=c['request'];sem.update(first_fit=r['operation']=='fit',cpu_refine=r['quality']=='fine',
        refine_prepare=r['operation']=='refine',parent_bundle=None if r['operation']=='fit' else r['recipe']['directory'])
    return sem


def operation(c,sem):
    expected=dict(first_fit=c['request']['operation']=='fit',cpu_refine=c['mode']=='fine',refine_prepare=c['request']['operation']=='refine')
    need(sem.get('adaptive_application') is True and sem.get('TEST_ONLY') is False and
         sem['executor_build']==c['executor_build']['sha256'] and sem['source_sha256']==c['source']['sha256'] and
         sem['output_root']==c['output_root'] and sem['mode']==c['mode'] and sem['bundle_directory']==c['bundle'] and
         all(sem.get(k)is v for k,v in expected.items()) and sem.get('adaptive_parent_operation') is None,'GM063_OPERATION_CONTEXT')
    return c
