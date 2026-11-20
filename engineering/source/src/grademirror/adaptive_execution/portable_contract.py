"""Explicit GM064 package/session capability; old v5 solver permissions unchanged."""
import os
import re
from pathlib import Path
from ..common import strict_load as _read,reject_reparse
from ..cpu_replay.contract import ROOT,FLAGS,RESOURCE_ENVELOPE,binding,verify,need
from .portable_policy import POLICY
SCHEMA='gm064-portable-capability-v1'
ENVELOPE={key:value for key,value in RESOURCE_ENVELOPE.items() if key not in ('native_available','native_commit','system_physical_margin')}
ENVELOPE.update(admission_policy=POLICY,cold_min_system_commit=4*1024**3,physical_margin_min=536_870_912,physical_margin_max=1_000_000_000,physical_margin_rule='min(max, max(min, ceil(additional_RAM/5)))')
FAST_MATH='fe114f646419f2589a3d5bfbe684b081dd3665d81307d64b58d2e84bf2eb1446'
FINE_MATH='693974cbb38e00620e891d5e73b3b1b64db8dd812702016d9f377557b107c6ea'
def read(p):return _read(Path(p))
def ancestors(p):
    p=Path(os.path.abspath(p))
    for x in (*reversed(p.parents),p):
        if x.exists():reject_reparse(x)
    return p

PATH_LAYOUT = 'gm064-path-layout-v2'

def layout():
    value = read(package_member('validation/path-layout.json'))
    need(value == dict(schema=PATH_LAYOUT, base=['GradeMirror', 'V24'],
                       workspace='w', session_id='uuid4-hex', max_utf16=240), 'PORTABLE_PATH_LAYOUT')
    return value

def candidate_base():
    return Path(os.environ.get('LOCALAPPDATA','C:/__missing__'))/'GradeMirror'/'V24'

def in_session_base(path):
    return Path(os.path.abspath(path)).is_relative_to(candidate_base())

def session_base():
    return Path(os.environ['LOCALAPPDATA']).joinpath(*layout()['base'])

def workspace(root):
    return root/'w'

def session_path(p):
    p=ancestors(p)
    base=ancestors(session_base())
    need(p.is_relative_to(base),'PORTABLE_SESSION_OUTSIDE_DEFAULT')
    rel=p.relative_to(base)
    need(rel.parts and re.fullmatch(r'[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}', rel.parts[0]),
         'PORTABLE_SESSION_NAME')
    return base/rel.parts[0]

def session(p):
    root=session_path(p)
    marker=root/'session.json'
    need(marker.is_file() and read(marker).get('path_layout')==PATH_LAYOUT,'PORTABLE_SESSION_MARKER')
    return root

def operation_sidecar(directory, name):
    directory = Path(directory)
    raw = directory.absolute()
    if not in_session_base(raw):
        return None
    import base64
    import uuid
    root = session(raw)
    run, unit, store, operations, job, attempt, leaf = raw.relative_to(root).parts
    need((run, store, operations, leaf)==('w', 'store', 'gm004a-operations', 'session')
         and (re.fullmatch(r'[TS][1-4]', unit) or unit=='C4') and name in ('x','r'),
         'PORTABLE_OPERATION_SIDECAR_PATH')
    ids = [uuid.UUID(value) for value in (job, attempt)]
    need([str(value) for value in ids]==[job,attempt],'PORTABLE_EXPORT_TRANSACTION_ID')
    # Lower-case base32 retains both UUIDs without case aliases on Windows.
    token=base64.b32encode(b''.join(value.bytes for value in ids)).decode().lower().rstrip('=')
    return ancestors(sidecar_base(root,unit,name)/token)

def sidecar_base(root,unit,name):
    if unit=='C4':
        from .t34r3_c1_route import SESSION
        need(root.name==SESSION and name in ('x','r'),'C2_OPERATION_SIDECAR_SCOPE')
        return root/(name+'5')
    need(re.fullmatch(r'[TS][1-4]',unit) and name in ('x','r'),'PORTABLE_OPERATION_SIDECAR_SCOPE')
    return root/(name+unit[-1])

def export_transaction(directory, key_hash):
    sidecar=operation_sidecar(directory,'x')
    if sidecar is not None:
        need(re.fullmatch(r'[0-9a-f]{64}',key_hash),'PORTABLE_EXPORT_KEY_HASH')
        return ancestors(sidecar/key_hash)
    directory=Path(directory)
    legacy=directory/'.gm004a-exports'/key_hash
    short=preview_cpu_export(directory,key_hash)
    if short is None:
        return legacy
    # Failed mkdirs can leave an empty legacy directory. An existing
    # transaction keeps its original location for retry and recovery.
    if legacy.is_dir() and any((legacy/name).is_file()
                               for name in ('intent.json','prepared.json','commit.json','cancelled.json')):
        reject_reparse(legacy)
        need(not (short/'intent.json').exists(),'PREVIEW_EXPORT_TRANSACTION_AMBIGUOUS')
        return legacy
    return short

def preview_cpu_attempt(directory):
    """Identify an ordinary CPU preview session without aliasing its UUIDs."""
    from uuid import UUID

    local=os.environ.get('LOCALAPPDATA')
    if not local or not Path(local).is_absolute():
        return None
    cpu=Path(local)/'GradeMirror'/'LightPreview'/'cpu'
    runs=cpu/'runs'
    if not directory.is_absolute() or not directory.is_relative_to(runs):
        return None
    parts=directory.relative_to(runs).parts
    need(len(parts)==6,'PREVIEW_EXPORT_SESSION_PATH')
    run,store,operations,job,attempt,leaf=parts
    need((store,operations,leaf)==('store','gm004a-operations','session')
         and all(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value)
                 for value in (run,job,attempt)), 'PREVIEW_EXPORT_SESSION_PATH')
    reject_reparse(directory)
    return cpu, b''.join(UUID(v).bytes for v in (run,job,attempt))

def _base32(value):
    from base64 import b32encode
    return b32encode(value).decode('ascii').lower().rstrip('=')

def preview_cpu_export(directory, key_hash):
    session = preview_cpu_attempt(directory)
    if session is None:
        return None
    cpu, ids = session
    need(re.fullmatch(r'[0-9a-f]{64}', key_hash), 'PREVIEW_EXPORT_KEY_HASH')
    return ancestors(cpu/'x'/_base32(ids)/_base32(bytes.fromhex(key_hash)))

def receiver_directory(directory):
    from hashlib import sha256

    sidecar = operation_sidecar(directory, 'r')
    legacy = Path(directory) / 'receiver-store'
    if sidecar is not None:
        return sidecar
    session = preview_cpu_attempt(Path(directory))
    if session is None:
        return legacy
    cpu, ids = session
    # ReceiverStore accepts a 52-character base32 namespace. Bind it to
    # all three UUIDs so cleanup and the host compute the same address.
    compact = ancestors(cpu / 'r' / _base32(sha256(ids).digest()))
    old_store = legacy / 'gm004a-operations'
    reject_reparse(old_store)
    # A failed mkdir can leave just index.lock. Keep older operation
    # artifacts addressable by their original path.
    if old_store.is_dir() and any(entry.name != 'index.lock' for entry in old_store.iterdir()):
        need(not compact.exists(), 'PREVIEW_RECEIVER_STORE_AMBIGUOUS')
        return legacy
    return compact

def renewal_directory(directory):
    sidecar=operation_sidecar(directory,'r')
    return sidecar/'renewals' if sidecar is not None else Path(directory)/'renewals'

def pointer(p):
    if p is None:return False
    raw=Path(os.path.abspath(p));base=candidate_base()
    return raw.is_relative_to(base) and raw.parent.name=='qualifications' and raw.parent.parent.parent==base

def enabled(c):return bool(c and isinstance(c,dict) and pointer(c.get('qualification',{}).get('path')))
def e(c):return session(c['qualification']['path'] if isinstance(c,dict) else c)


FINE_QUALIFICATION_ROWS = tuple((alpha, bits) for alpha in (0., .5, 1.) for bits in (8, 16))


def segmented_fine_qualification(c):
    return bool(enabled(c) and c['phase'] == 'replay_fine' and c['mode'] == 'fine'
                and c['parent_bundle']['math_id'] == FINE_MATH)
_package_index=None
_package_manifest=None

def package_member(relative):
    global _package_index,_package_manifest
    manifest=binding(ROOT/'portable-manifest.json')
    if _package_index is None or manifest!=_package_manifest:
        value=read(ROOT/'portable-manifest.json');rows=value['files'];need(len(rows)==len({x['path'].casefold()for x in rows}),'PORTABLE_MANIFEST_DUPLICATE')
        _package_index={x['path']:x for x in rows};_package_manifest=manifest
    need(relative in _package_index,'PORTABLE_UNMANIFESTED_CAPABILITY_MEMBER')
    row=_package_index[relative];verify(dict(path=str(ROOT/relative),bytes=row['bytes'],sha256=row['sha256']))
    return ROOT/relative

def capability():
    ancestors(ROOT);p=package_member('validation/capability.json');v=read(p)
    need(v['schema']==SCHEMA and v['task']=='GM-064' and v['policy']==POLICY and v['CPU_ENABLED'] is False and
         v['AUTO_ENABLED'] is False and v['PRODUCTION_ENABLED'] is False,'PORTABLE_CAPABILITY_FIELDS')
    need(v['math_ids']==[FAST_MATH,FINE_MATH] and v['source_sha256']=='64db59daa73c460dfdaa498c3ab29c865ea3032a39cd4e6d32beda092de4b60c' and
         v['reference_sha256']=='d2dd38ad47fd3ee484e07c087d2783edb10cddc8f50afacf0d78a01efece6358','PORTABLE_FIXED_INPUT_CAPABILITY')
    return v

def training_hw(shape, long_side):
    need(type(shape) is list and len(shape) == 3 and shape[2] == 3
         and all(type(n) is int and n > 0 for n in shape), 'PORTABLE_TRAINING_IMAGE_SHAPE')
    h, w = shape[:2]
    need(type(long_side) is int and 13 <= long_side <= max(h, w), 'PORTABLE_TRAINING_LONG_SIDE')
    scale = long_side / max(h, w)
    grid = [max(1, round(h * scale)), max(1, round(w * scale))]
    need(min(grid) >= 13, 'PORTABLE_TRAINING_GRID_MINIMUM')
    return grid


def fixed_settings(template, domain, source_shape, reference_shape):
    settings = dict(template)
    grid = training_hw(source_shape, settings['long_side'])
    need(grid == training_hw(reference_shape, settings['long_side']), 'PORTABLE_TRAINING_PAIR_GRID')
    if domain == 'small':
        settings.update(baseline_training_hw=grid, updates=4)
    else:
        need(domain == '24mp' and settings['baseline_training_hw'] == grid, 'PORTABLE_24MP_GRID_CHANGED')
    return settings


def require_training_arrays(before_shape, after_shape, settings):
    grid = settings['baseline_training_hw']
    need(list(before_shape[:2]) == list(after_shape[:2]) == grid, 'PORTABLE_ACTUAL_TRAINING_GRID_MISMATCH')


def qualification_index(root,path):
    from .t34r3_c1_route import QUALIFICATIONS,route_prefix
    if Path(path).name in QUALIFICATIONS:
        return root/'science'/f't34r3-{route_prefix(path)}-qualification-index.json'
    return root/'qualification-index.json'

def authorize(path,request,*,cleanup=False):
    from .intent import normalize
    from .build import current_closure
    from .first_contract import LIMITS,POLICY as IDLE_POLICY
    need(pointer(path),'PORTABLE_QUALIFICATION_POINTER');p=ancestors(path);root=session(p);q=read(p)
    cap=capability();mark=read(root/'session.json')
    from .t34r3_c1_route import selected,KEY,QUALIFICATIONS,route_workspace
    route=selected(q,route_workspace(root,p),KEY) if p.name in QUALIFICATIONS else None
    need(p.name not in QUALIFICATIONS or route is not None,'C1_ROUTE_SCOPE')
    from . import c2_fine_replay
    if p.name in c2_fine_replay.QUALIFICATIONS:
        c2_fine_replay.validate_scope(q,root)
    else:
        need(not mark.get('c2_fine'),'C2_REPLAY_QUALIFICATION_REQUIRED')
    index=qualification_index(root,p)
    ancestors(index)
    need(read(index).get(str(p))==binding(p),'PORTABLE_QUALIFICATION_INDEX')
    manifest=route.old_window['manifest'] if route is not None else binding(ROOT/'portable-manifest.json')
    need(mark['package_root']==str(ROOT) and mark['capability']==binding(ROOT/'validation/capability.json') and
         mark['policy']==POLICY and mark['package_manifest']==manifest,'PORTABLE_SESSION_PACKAGE_CHANGED')
    fields={'source','reference','source_identity','reference_identity','settings','profile','color_policy','schema','portable_schema','task','TEST_ONLY','manifest_path','phase','domain','authorization','taskbook','executor_build','runtime','interpreters','paths','resource_envelope','limits','lease_policy','allocations','parent_package','p0','fixed_inputs','request','recovery','policy','resource_model'}
    need(type(q)is dict and set(q)==fields and q['domain']in('24mp','small'),'PORTABLE_QUALIFICATION_SCHEMA')
    need(not mark.get('path_only') or q['phase']=='dry','PORTABLE_PATH_ONLY_NO_SCIENCE')
    need(not mark.get('local_only') or q['domain']=='small' or q['phase']=='dry' or q['request']['operation']=='replay','PORTABLE_LOCAL_NO_NATIVE_FIT')
    need(q['schema']=='adaptive-first-qualification-v1' and q['task']=='GM-064' and q['portable_schema']==SCHEMA and
         q['manifest_path']==str(p) and q['phase'] in ('dry','native') and q['TEST_ONLY'] is False,'PORTABLE_QUALIFICATION_FIELDS')
    need(q['paths']==dict(evidence=str(root),workspace=str(workspace(root))) and q['resource_envelope']==ENVELOPE and
         q['limits']==LIMITS and q['lease_policy']==dict(IDLE_POLICY,scope='GM064_FIXED_DOMAIN_ONLY') and q['policy']==POLICY,'PORTABLE_RESOURCE_SCOPE')
    need(q['authorization']==binding(root/'session.json') and q['fixed_inputs']==binding(root/'fixed-inputs.json') and
         q['taskbook']==binding(package_member('validation/taskbook.md')) and q['resource_model']==binding(package_member('validation/resource-model.json')),'PORTABLE_AUTHORITY_BINDING')
    fixed=read(q['fixed_inputs']['path'])[q['domain']]
    for key in ('source','reference','source_identity','reference_identity','settings','profile','color_policy'):
        need(q[key]==fixed[key],'PORTABLE_FIXED_BINDING:'+key)
    template=read(package_member('validation/fixed-template.json'))
    expected_settings=fixed_settings(template['settings'],q['domain'],q['source_identity']['shape'],q['reference_identity']['shape'])
    need(q['settings']==expected_settings and q['profile']==template['profile'],'PORTABLE_ALL_FROZEN_MATH_PARAMETERS')
    if q['domain']=='24mp':
        need(all(q[key]==template[key] for key in ('source_identity','reference_identity','color_policy')),'PORTABLE_FROZEN_PIXEL_AND_COLOR_IDENTITIES')
    else:
        need(q['source']['sha256']==q['reference']['sha256']=='8d610b352c11dfc394b77c4e80c1a545e7b960237bb2d529bf5a4e0db34abb7c' and
             q['source_identity']['pixel_sha256']==q['reference_identity']['pixel_sha256']=='6d925cee32f6eeb33b9dbba7a2e071f10fd5c6928ce2d401dc0794cdb5397719','PORTABLE_EXISTING_SMALL_PIXELS')
    shape=q['source_identity']['shape'];need(shape==([6000,4000,3] if q['domain']=='24mp' else [513,65,3]) and
        q['settings']['updates']==(1000 if q['domain']=='24mp' else 4) and q['settings']['seed']==43 and q['settings']['tile_side']==512,
        'PORTABLE_FIXED_GEOMETRY_OR_MATH')
    need(Path(q['source']['path']).resolve()==ROOT/('validation/inputs/benchmark-24mp.jpg' if q['domain']=='24mp' else 'validation/inputs/small.jpg'),
        'PORTABLE_SOURCE_PATH')
    if q['domain']=='24mp':need(q['source']['sha256']==cap['source_sha256'] and q['reference']['sha256']==cap['reference_sha256'],'PORTABLE_SOURCE_BYTES')
    r=normalize(request);need(r==q['request'] and r['execution_preference']=='cpu' and r['operation'] in ('fit','refine','replay') and
        r['quality']==('fast' if r['operation']=='fit' else 'fine' if r['operation']=='refine' else r['quality']),'PORTABLE_EXACT_REQUEST')
    source_meta=dict(width=shape[1],height=shape[0],bits=8,format='JPEG')
    reference_shape=q['reference_identity']['shape'];reference_meta=dict(width=reference_shape[1],height=reference_shape[0],bits=8,format='PNG' if q['domain']=='24mp' else 'JPEG')
    need(r['source']==dict(file=q['source'],metadata=source_meta) and
         (r['reference'] is None if r['operation']=='replay' else r['reference']==dict(file=q['reference'],metadata=reference_meta)),
         'PORTABLE_REQUEST_INPUTS')
    need(Path(q['reference']['path']).resolve()==ROOT/('validation/inputs/reference.png' if q['domain']=='24mp' else 'validation/inputs/small.jpg'),'PORTABLE_REFERENCE_PATH')
    pkg=q['parent_package'];need((pkg is None)==(r['operation']=='fit'),'PORTABLE_PARENT_PHASE')
    if pkg is not None:
        need(r['recipe']=={key:pkg[key] for key in ('directory','bundle_id','math_id','version')},'PORTABLE_PARENT_REQUEST')
        from ..cpu_recipe.validation import complete
        m,_=complete(pkg['directory']);validate_parent(pkg,q,m,cleanup=cleanup,continuation=route)
    verify(q['runtime']['inventory']);need(q['runtime']['status']=='KNOWN_EXACT_RUNTIME' and
        Path(q['runtime']['inventory']['path']).resolve()==(route.runtime_inventory if route is not None else root/'runtime-inventory.json'),'PORTABLE_RUNTIME_SCOPE')
    if route is not None:route.verify_inventory()
    for role,b in q['interpreters'].items():
        need(role in ('control','numeric','io','solver','desktop') and Path(b['path']).resolve()==ROOT/'.runtime/gm059-v1'/role/'python.exe','PORTABLE_ROLE_PATH');verify(b)
    need(set(q['interpreters'])=={'control','numeric','io','solver','desktop'},'PORTABLE_ROLES_COMPLETE')
    need(cleanup or q['executor_build']==current_closure(native=True),'PORTABLE_BUILD_CHANGED')
    if q['phase']=='native' and not cleanup:
        verify(q['p0']);proof=read(q['p0']['path']);need(proof['status']=='GM064_ZERO_SCIENCE_PASS' and
            proof['executor_build']==(route.old_qualification if route is not None else q)['executor_build']['sha256'],'PORTABLE_P0_REQUIRED')
    if not cleanup:verify(q['source']);verify(q['reference'])
    return q

def expected_tiles(c):
    h,w,_=c['source_identity']['shape'];return ((h+511)//512)*((w+511)//512)

def validate_parent(pkg,q,m,*,cleanup=False,continuation=None):
    directory=Path(pkg['directory']).resolve();root=session(q['manifest_path'])
    need((m['bundle_id'],m['math_id'],m['version'])==tuple(pkg[k] for k in ('bundle_id','math_id','version')) and
         m['provenance']['TEST_ONLY'] is False and m['mathematical']['inputs']==dict(source_identity=q['source_identity'],reference_identity=q['reference_identity']) and
         m['mathematical']['settings']==q['settings'],'PORTABLE_PARENT_MATH_AND_INPUT')
    for item in pkg['files']:verify(item)
    from . import c2_fine_replay
    if Path(q['manifest_path']).name in c2_fine_replay.QUALIFICATIONS:
        return c2_fine_replay.validate_saved_parent(pkg,q,m,root)
    if directory in (ROOT/'validation/recipes/fast',ROOT/'validation/recipes/fine'):
        expected=FAST_MATH if directory.name=='fast' else FINE_MATH
        need(q['domain']=='24mp' and q['request']['operation']=='replay' and m['math_id']==expected and
             pkg['producer']=={},'PORTABLE_IMPORTED_PRODUCER_ADAPTER')
    else:
        need(directory.is_relative_to(workspace(root)) and q['request']['operation'] in ('refine','replay'),'PORTABLE_NEW_PARENT_SCOPE')
        if m['version']==3 and m['mode']=='fast':
            from .saved_fit_recovery import validate_parent as validate_recovered_parent
            return validate_recovered_parent(pkg,q,m,cleanup=cleanup,continuation=continuation)
        proof=pkg['producer'];need(set(proof)=={'context','spec','fit_receipt','fit_wait','reclaim'},'PORTABLE_PARENT_PROOF_FIELDS')
        for row in proof.values():verify(row)
        pc=read(proof['context']['path']);spec=read(proof['spec']['path']);fit=read(proof['fit_receipt']['path']);wait=read(proof['fit_wait']['path']);reclaim=read(proof['reclaim']['path'])
        need(pc['phase']=='fit' and spec['semantics']['instance_context']==proof['context'] and
             m['producer_build']==pc['executor_build'] and fit['status']=='COMPLETE' and
             fit['counts']==dict(fit=1,updates=q['settings']['updates'],forward=2*q['settings']['updates']+1,model_construct=1,optimizer_construct=1) and
             fit['parameter_sha256']==m['mathematical']['r0_parameters']['value_sha256'],'PORTABLE_ACTUAL_NEW_FIT')
        need(wait['actual_worker_handle_wait'] is True and wait['tree_empty'] is True and wait['exit_code']==0 and
             reclaim['actual_wait'] is True and reclaim['tree_empty'] is True and reclaim['identities'] and
             all(x['exited'] is True for x in reclaim['identities']),'PORTABLE_PARENT_ACTUAL_RELEASE')
    return True

def recipe_scope(c,spec,directory,m):
    q=authorize(c['qualification']['path'],c['request'],cleanup=True)
    need(Path(directory).resolve()==Path(c['bundle']).resolve()==Path(spec['semantics']['bundle_directory']).resolve(),'PORTABLE_RESULT_DIRECTORY')
    if c['request']['operation']=='replay':return validate_parent(q['parent_package'],q,m,cleanup=True)
    need(m['producer_build']==c['executor_build'] and m['provenance']['TEST_ONLY'] is False and
         all(m['provenance'][key]==spec[key] for key in ('job_id','attempt_id')) and
         m['mathematical']['inputs']==dict(source_identity=c['source_identity'],reference_identity=c['reference_identity']) and
         m['mathematical']['settings']==c['settings'] and m['version']==(2 if c['phase']=='fit' else 3),'PORTABLE_RESULT_PRODUCER')
    return True
