"""Strict role/operation/descriptor binding before any scientific function."""
from pathlib import Path
from ..common import strict_load
from ..cpu_replay.contract import need,binding,verify,worker_authority
from .contract import E


def authorize(role,path,payload,c):
    work=Path(path).resolve().parent
    need(type(payload) is dict,'REFINE_PAYLOAD_OBJECT')
    if role.startswith('entry_'):
        need(c['phase']=='entry' or (c['phase']=='render' and role=='entry_control'),'REFINE_ENTRY_PHASE')
        return
    if c['phase']=='small':
        need(c['TEST_ONLY'] is True and role in ('small','cpu'),'REFINE_SMALL_ROLE')
        if role=='small':
            need(set(payload) in ({'work','cancel','scope','expected','golden_fields','ranges'},
                                 {'work','cancel','scope','expected','golden_fields','ranges','saved'},
                                 {'work','cancel','scope','expected','golden_fields','ranges','saved','reuse_saved'}) and
                 payload['scope']==binding(E/'contexts'/(c['id']+'.json')) and
                 Path(payload['work']).resolve()==work and type(payload['cancel']) is bool,'REFINE_SMALL_PAYLOAD')
            verify(c['runtime']['TEST_ONLY_small_helper'])
        else:
            need(set(payload)=={'manifest','O_shared_memory','b_path','address','auth_hex','ranges'},'REFINE_SMALL_CPU_PAYLOAD')
            fixed=strict_load(E.parent/'20261030-plan-v1/fixed-inputs.json')['small_case']
            m=payload['manifest']
            need(m['O']==dict(shape=[771,67,3],dtype='float32',sha256=fixed['hashes']['O']) and
                 m['b']==dict(shape=[24,3],dtype='float64',sha256=fixed['hashes']['b']) and
                 m['reference_wh']==[39,23] and m['core_rows']==256 and
                 Path(m['output_root']).resolve()==work and all(m['budget'][k]<=c['science'][k] for k in m['budget']),
                 'REFINE_SMALL_CPU_BINDING')
        return
    need(role in ('r0','prepare','solver','cpu','frame_sender'),'REFINE_NATIVE_WORKER_ROLE')
    session=work.parent.parent if role=='solver' else work.parent
    ss=worker_authority(session);sem=ss['semantics']
    need(sem.get('cpu_refine') is True and ss['execution']==c and (session/'compute-go.json').is_file(),
         'REFINE_WORKER_OPERATION_BINDING')
    maps=strict_load(session/'shared-mappings.json')
    if role=='frame_sender':
        need(c['phase']=='frame' and c['TEST_ONLY'] and payload.get('TEST_ONLY') is True and
             set(payload)<= {'manifest','O_shared_memory','b_path','address','auth_hex','ranges','TEST_ONLY','partial'} and
             payload['O_shared_memory']==maps['O']['name'],'REFINE_FRAME_PAYLOAD')
        return
    need(not c['TEST_ONLY'] and c['phase'] in ('refine','render'),'REFINE_NATIVE_PHASE')
    shape=c['source_identity']['shape']
    if role=='r0':
        need(set(payload)=={'work','S','O','bundle','cancel','operation','reservation'} and
             Path(payload['work']).resolve()==work and Path(payload['operation']).resolve()==session.parent and
             payload['S']==dict(name=maps['S']['name'],shape=shape,dtype='uint8',readonly=True) and
             payload['O']==dict(name=maps['O']['name'],shape=shape,dtype='float32') and
             payload['S']['name']!=payload['O']['name'] and payload['reservation']==c['science'] and
             Path(payload['cancel']).resolve()==session.parent/'cancel.signal' and
             c['science']['R0_render']==1 and c['science']['Field_forward']==140,'REFINE_R0_PAYLOAD')
        directory=sem['parent_bundle'] if sem['refine_prepare'] else sem['bundle_directory']
        need(Path(payload['bundle']).resolve()==Path(directory).resolve(),'REFINE_R0_BUNDLE_PATH')
        from ..cpu_recipe.validation import complete
        m,_=complete(directory);expected=c['parent_bundle'] if sem['refine_prepare'] else c['bundle']
        need((m['bundle_id'],m['math_id'])==(expected['bundle_id'],expected['math_id']),'REFINE_R0_BUNDLE_IDENTITY')
    elif role=='prepare':
        need(set(payload)=={'work','O','reference','reference_wh','cancel'} and c['phase']=='refine' and
             Path(payload['work']).resolve()==work and c['science']['scalar_CG']==3 and
             payload['O']==dict(name=maps['O']['name'],shape=shape,dtype='float32',readonly=True),
             'REFINE_P3_PAYLOAD')
        reference=strict_load(session/'reference-mapping.json');rh,rw,_=c['reference_identity']['shape']
        need(payload['reference']==dict(name=reference['name'],shape=[rh,rw,3],dtype='uint8',readonly=True) and
             payload['reference_wh']==[rw,rh] and Path(payload['cancel']).resolve()==session.parent/'cancel.signal',
             'REFINE_P3_REFERENCE')
    elif role=='solver':
        rh,rw,_=c['reference_identity']['shape']
        need(set(payload)=={'address','auth_hex','native_wh','reference_wh','reference_shape','identity_control','solver_seconds'} and
             c['phase']=='refine' and c['science']['scalar_CG']==3 and payload['native_wh']==[shape[1],shape[0]] and
             payload['reference_wh']==[rw,rh] and payload['reference_shape']==[rh,rw,3] and
             payload['identity_control'] is False and payload['solver_seconds']==1000,'REFINE_SOLVER_PAYLOAD')
    elif role=='cpu':
        need(set(payload)=={'manifest','O_shared_memory','b_path','address','auth_hex','ranges'} and
             payload['O_shared_memory']==maps['O']['name'],'REFINE_CPU_PAYLOAD')
        from ..cpu_recipe.generated_fine import complete
        directory=Path(sem['bundle_directory']);m,_=complete(directory);p3=m['mathematical']['p3'];manifest=payload['manifest']
        authorization=strict_load(session/'render-authorization.json');verify(authorization['bundle_complete'])
        need(authorization['parent_context']==sem['instance_context'] and authorization['bundle_id']==m['bundle_id'] and
             authorization['math_id']==m['math_id'] and authorization['executor_build']==c['executor_build']['sha256'] and
             Path(payload['b_path']).resolve()==directory.resolve()/'p3/b.npz' and payload['ranges']==p3['ranges'] and
             manifest['O']['shape']==shape and manifest['b']==dict(shape=p3['b']['members']['b']['shape'],
                 dtype='float64',sha256=p3['b']['value_sha256']) and manifest['reference_wh']==p3['reference_wh'] and
             manifest['core_rows']==256 and all(manifest['budget'][k]<=c['science'][k] for k in manifest['budget']),
             'REFINE_CPU_FINE_BUNDLE_BINDING')
