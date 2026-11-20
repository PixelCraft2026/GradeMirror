"""Strict provenance for this task's actual successful CG, no old golden inputs."""
from pathlib import Path
from ..cpu_replay.contract import binding,verify,need
from .first_contract import E,W,read
FIELDS={'schema','task','authorization','b','inputs','solver','solver_wait','context','qualification','spec','claim','root_wait','error','R0'}
def validate(q):
 row=q['partial_b'];need(Path(row['path']).resolve()==E/'resumes/actual-b-capability.json','FIRST_PARTIAL_POINTER');verify(row);v=read(row['path']);need(type(v)is dict and set(v)==FIELDS and v['schema']=='gm057-actual-cg-resume-v1' and v['task']=='GM-057','FIRST_PARTIAL_FIELDS')
 for k in FIELDS-{'schema','task'}:verify(v[k])
 expected=binding(E/'supplements/range-r3/authorization.json');need(set(v['authorization'])==set(expected) and Path(v['authorization']['path']).resolve()==Path(expected['path']).resolve() and all(v['authorization'][k]==expected[k] for k in ('bytes','sha256')) and read(v['authorization']['path'])['status']=='USER_APPROVED','FIRST_PARTIAL_AUTHORITY')
 c=read(v['context']['path']);spec=read(v['spec']['path']);claim=read(v['claim']['path']);sr=read(v['solver']['path']);wait=read(v['solver_wait']['path']);rw=read(v['root_wait']['path']);op=Path(v['spec']['path']).parent
 need(op.is_relative_to(W) and Path(v['b']['path'])==op/'session/P3/b.npz' and Path(v['inputs']['path'])==op/'session/P3/P3-inputs.json' and Path(v['solver']['path'])==op/'session/P3/solver/solver-result.json' and Path(v['solver_wait']['path'])==op/'session/P3/solver/solver-cleanup.json','FIRST_PARTIAL_EXACT_PATHS')
 need(c['schema']=='cpu-application-execution-v4' and c['phase']=='refine' and c['TEST_ONLY'] is False and c['qualification']==v['qualification'] and read(v['qualification']['path'])['phase']=='native','FIRST_PARTIAL_NATIVE_PRODUCER')
 need(all(c[k]==q[k] for k in ('source','reference','source_identity','reference_identity','settings','profile','color_policy')) and c['parent_bundle']==q['parent_package'] and spec['semantics']['instance_context']==v['context'] and spec['semantics']['executor_build']==c['executor_build']['sha256'],'FIRST_PARTIAL_SAME_INPUTS')
 need(claim['context']==v['context'] and claim['phase']=='refine' and Path(claim['operation_path'])==op and sr['status']=='PASS' and sr['counts']=={'scalar_CG':3} and sr['runtime']=={'numpy':'2.5.3','scipy':'1.18.1'},'FIRST_PARTIAL_REAL_CG')
 need(all(wait[k] is True for k in ('actual_wait','actual_worker_handle_wait','tree_empty')) and wait['exit_code']==0 and all(rw[k] is True for k in ('actual_root_wait','actual_all_wait','tree_empty','exact')),'FIRST_PARTIAL_COMPLETE_EXIT')
 from ..cpu_recipe.assets import parse_file
 b=parse_file(v['b']['path'],'b');need(b['value_sha256']==sr['coefficient_sha256'] and b['members']['b']['shape']==sr['shape'],'FIRST_PARTIAL_COMPLETE_B_IDENTITY')
 from .first_parent import _events_snapshot
 ev=_events_snapshot();need(sum(x['kind']=='completed' and x.get('reservation')==claim['reservation'] and x.get('component')=='CG' and x.get('receipt')==binding(v['solver_wait']['path']) for x in ev)==1,'FIRST_PARTIAL_ACTUAL_CG_COMPLETION')
 need(read(v['error']['path'])['error']=='FIRST_ROLE_OR_DRY_BOUNDARY:p3_region','FIRST_PARTIAL_KNOWN_ENGINEERING_FAILURE');return v
