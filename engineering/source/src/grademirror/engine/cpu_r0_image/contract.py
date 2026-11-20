"""Fixed real-pair qualification, independently versioned from all old GPU/CPU scopes."""
from pathlib import Path
from ..cpu_r0_render.contract import canonical,digest,file_hash,loads,bounded,fields,need,PYTHON,PYTHON_LAUNCHER_SHA256,PYTHON_BASE_SHA256
from ..independent_r0.profile import PROFILE_ID,PROFILE_CONTRACT_SHA256,CORE_SHA256,MATH_SHA256,RUNTIME
from ..profiles import settings_for,normalization

ROOT=Path(__file__).resolve().parents[4];I=ROOT/'docs/evidence/gm-049/20261029-plan-v1';E=ROOT/'docs/evidence/gm-049/20261029-v1';W=ROOT/'tests/workspaces/g49/20261029-v1'
BACKEND='r0-cpu-image-v1';RECIPE='cpu-r0-image-recipe-v1';SCOPE='gm049-fixed-group15-cpu-fast-20261029-v1'
FLAGS=dict(CPU_QUALIFIED=False,AUTO_SELECTION=False,PRODUCTION_ACTIVATION=False,D01_extended=False)

def binding(p):
    p=Path(p).resolve();return dict(path=p.as_posix(),bytes=p.stat().st_size,sha256=file_hash(p))

def bound_file(b,cap=16*1024**2):
    fields(b,('path','bytes','sha256'));need(type(b['path'])is str and Path(b['path']).is_absolute(),'CPU_IMAGE_FILE_PATH')
    need(type(b['bytes'])is int and 0<b['bytes']<=cap and type(b['sha256'])is str and len(b['sha256'])==64 and all(x in '0123456789abcdef'for x in b['sha256']),'CPU_IMAGE_FILE_SCHEMA')
    from grademirror.common import reject_reparse
    p=Path(b['path']);reject_reparse(p);need(binding(p)==b,'CPU_IMAGE_FILE_CHANGED');return p

def fixed_cases():
    p=loads((I/'pair-inputs.json').read_text('utf8'));s=p['small_pair'];n=p['native']
    small=dict(source_file=s['source_file'],reference_file=s['reference_file'],source_identity=s['source_identity'],reference_identity=s['reference_identity'],color_declaration=s['color_declaration'],resize_geometry=s['resize_geometry'],color_policy=s['color_policy'],io_runtime=s['io_runtime'],training_shape=[33,47,3],training_source_pixel_sha256=s['source_identity']['pixel_sha256'],training_reference_pixel_sha256=s['reference_identity']['pixel_sha256'])
    native={k:n[k]for k in ('source_identity','reference_identity','resize_geometry','color_policy','io_runtime','training_shape','training_source_pixel_sha256','training_reference_pixel_sha256')}
    native.update(source_file=p['source'],reference_file=p['reference'],color_declaration='gamma-encoded-sRGB-float32')
    return dict(small=small,native=native)

def settings(case):
    v=settings_for(PROFILE_ID,fixed_cases()[case]['training_shape'][:2]);return {**v,'updates':3}if case=='small'else v

def implementation():
    old=ROOT/'src/grademirror/engine';paths=list(Path(__file__).parent.glob('*.py'))+list((ROOT/'schemas').glob('cpu-r0-image-*-v1.json'))
    paths += [old/'independent_r0'/n for n in (*MATH_SHA256,'adapter.py','profile.py')]+[old/'profiles.py',old/'cpu_r0_fit/optimizer_policy.py',old/'cpu_r0_render/executor.py',old/'group15_codec.py',ROOT/'src/grademirror/export_pixels.py']
    value=dict(backend=BACKEND,scope=SCOPE,sources={p.relative_to(ROOT).as_posix():file_hash(p)for p in sorted(paths)},math=MATH_SHA256,profile=PROFILE_CONTRACT_SHA256,runtime=dict(RUNTIME,python=PYTHON),CPU_setup='seed43/threads4/interop1/cv2threads4/FP32/channels_last; original CPU RNG and instance optimizer policy',flags=FLAGS)
    return dict(contract=value,sha256=digest(value))

def manifest_check(b):
    p=bound_file(b);need(p.resolve()==(E/'qualification-manifest.json').resolve(),'CPU_IMAGE_MANIFEST_PATH');m=loads(p.read_text('utf8'))
    fields(m,('schema','id','backend_sha256','cases','flags','frozen_at'))
    need(m['schema']=='cpu-image-qualification-manifest-v1'and m['id']==SCOPE and m['backend_sha256']==implementation()['sha256'],'CPU_IMAGE_MANIFEST_IDENTITY')
    need(canonical(m['cases'])==canonical(fixed_cases())and canonical(m['flags'])==canonical(FLAGS)and type(m['frozen_at'])is int and m['frozen_at']>0,'CPU_IMAGE_MANIFEST_SCOPE');return m

def validate_request(r,operation):
    bounded(r);base=('schema_version','backend_id','backend_sha256','qualification_manifest','scope','pair','settings','runtime','execution_device','normalization')
    fields(r,base+(('output_intent',)if operation=='fit'else('recipe','output')))
    need(r['schema_version']=='cpu-r0-image-'+operation+'-request-v1'and r['backend_id']==BACKEND and r['backend_sha256']==implementation()['sha256'],'CPU_IMAGE_REQUEST_VERSION')
    m=manifest_check(r['qualification_manifest']);s=r['scope'];fields(s,('id','case','arm'))
    need(s['id']==SCOPE and type(s['case'])is str and s['case']in ('small','native'),'CPU_IMAGE_SCOPE')
    arms=(('new','cold')if s['case']=='small'else('new',))if operation=='fit'else('new','cold','cancel')
    need(s['arm']in arms and (operation=='fit'or s['case']=='native'),'CPU_IMAGE_ARM_SCOPE')
    row=m['cases'][s['case']];need(canonical(r['pair'])==canonical(row),'CPU_IMAGE_PAIR_BINDING')
    for label in ('source','reference'):bound_file(row[label+'_file'])
    need(canonical(r['settings'])==canonical(settings(s['case']))and canonical(r['runtime'])==canonical(dict(RUNTIME,python=PYTHON))and r['execution_device']=='cpu','CPU_IMAGE_SETTINGS_RUNTIME')
    need(canonical(r['normalization'])==canonical(normalization(row['source_identity'],row['reference_identity'])),'CPU_IMAGE_NORMALIZATION')
    if operation=='fit':
        o=r['output_intent'];fields(o,('directory','owner','overwrite'));expected=(W/'recipes'/(s['case']+'-'+s['arm'])).resolve()
        need(type(o['directory'])is str and Path(o['directory']).resolve()==expected and o['owner']=='caller'and o['overwrite']is False and not expected.exists(),'CPU_IMAGE_OUTPUT_INTENT')
    else:
        o=r['output'];need(canonical(o)==canonical(dict(shape=[7008,4672,3],dtype='float32',capacity_bytes=392896512,borrowed=True,owner='caller')),'CPU_IMAGE_OUTPUT_CAPACITY')
        q=r['recipe'];fields(q,('metadata','archive','recipe_sha256','parameter_sha256'));bound_file(q['metadata']);bound_file(q['archive'])
    return row
