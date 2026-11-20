"""Self-contained fine v3; standard-library validation and atomic publication.

R0 comes from a complete generated fast v2. New coefficients are never inferred
from provenance or qualification labels. No numerical/image imports occur here.
"""
import copy
import hashlib
import io
import math
import os
import uuid
import zipfile
from pathlib import Path
from ..common import atomic_json, canonical_json
from ..store import locked
from .primitives import (root, read, read_bytes, fields, require, digest, inside,
                         file_record, verify_file, reject_reparse)
from .assets import npy, parse_file
from .guard import metadata_api
from . import generated

ASSETS = {'r0/parameters.npz', 'p3/b.npz', 'p3/solver-report.json',
          'provenance/fast-manifest.json', 'provenance/fast-COMPLETE.json',
          'provenance/fit-intent.json', 'provenance/fit-receipt.json',
          'provenance/refine-intent.json'}


def coefficients(data, reference_wh):
    require(type(reference_wh) is list and len(reference_wh)==2 and
            all(type(x) is int and x>0 for x in reference_wh), 'FINE_REFERENCE_GEOMETRY')
    shape=[(math.ceil(reference_wh[0]/8)+1)*(math.ceil(reference_wh[1]/8)+1),3]
    require(math.prod(shape)*8<=4*1024**2 and type(data) is bytes and len(data)<=4*1024**2,'FINE_COEFFICIENT_CAP')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries=archive.infolist()
        require(len(entries)==1 and entries[0].filename=='b.npy','FINE_COEFFICIENT_MEMBERS')
        entry=entries[0]
        require(not entry.flag_bits&1 and entry.compress_type in (0,8) and
                0<entry.file_size<=4*1024**2 and entry.file_size<=max(4096,entry.compress_size*200),
                'FINE_COEFFICIENT_COMPRESSION')
        body, info=npy(archive.read(entry),'<f8',shape)
    return dict(members={'b':info},value_sha256=hashlib.sha256(body).hexdigest())


def structure(m):
    fields(m,generated.MANIFEST_FIELDS)
    require(m['format']=='grademirror.cpu_recipe_bundle' and type(m['version']) is int and
            m['version']==3 and m['mode']=='fine','FINE_BUNDLE_VERSION')
    mathpart=m['mathematical']
    fields(mathpart,('profile','settings','normalization','inputs','color_policy','color_declaration',
                     'r0_parameters','math_files','p3'))
    p=mathpart['p3']
    fields(p,('b','reference_wh','H_sha256','T_sha256','ranges','constants','solver_policy'))
    fields(mathpart['inputs'],('source_identity','reference_identity'))
    for identity in mathpart['inputs'].values():
        fields(identity,('shape','dtype','bit_depth','file_sha256','file_size_bytes','pixel_sha256'))
        require(type(identity['shape']) is list and len(identity['shape'])==3 and identity['shape'][2]==3 and
                all(type(x) is int and x>0 for x in identity['shape']),'FINE_INPUT_SHAPE')
    reference_shape=mathpart['inputs']['reference_identity']['shape']
    require(p['reference_wh']==[reference_shape[1],reference_shape[0]],'FINE_REFERENCE_GEOMETRY_BINDING')
    require(p['constants']==dict(J=8,P3_h=8,sigma=.1,delta=1/64,
        composition='FP32(FP64(O)+(FP64(LP)-FP64(LO)))',range_margin=2/255), 'FINE_MATH_CONSTANTS')
    require(p['solver_policy']==dict(rtol=1e-8,atol=1e-12,maxiter=2000),'FINE_SOLVER_POLICY')
    require(type(p['ranges']) is list and len(p['ranges'])==3 and all(
        set(r)=={'kmin','kmax','count','min','max','delta','sigma','J'} and
        type(r['kmin']) is int and type(r['kmax']) is int and type(r['count']) is int and
        2<=r['count']<=257 and r['count']==r['kmax']-r['kmin']+1 and
        r['delta']==1/64 and r['sigma']==.1 and r['J']==8 and
        all(type(r[k]) in (int,float) and math.isfinite(r[k]) for k in ('min','max'))
        for r in p['ranges']), 'FINE_RANGES')
    fields(m['provenance'],('parent_bundle_id','parent_math_id','refine_intent_sha256',
                           'job_id','attempt_id','producer','TEST_ONLY'))
    require(m['provenance']['producer']=='original_P3_CPU256' and type(m['provenance']['TEST_ONLY']) is bool,
            'FINE_PRODUCER')
    fields(m['qualification'],('status','execution_qualified','general_input_qualified','CPU_ENABLED',
        'AUTO_ENABLED','PRODUCTION_ENABLED','D01_extended','user_visual'))
    require(m['qualification']['status']=='FINE_GENERATED_PENDING_SCOPED_QUALIFICATION' and
        m['qualification']['user_visual']=='PENDING' and all(m['qualification'][k] is False for k in
        ('execution_qualified','general_input_qualified','CPU_ENABLED','AUTO_ENABLED','PRODUCTION_ENABLED','D01_extended')),
        'FINE_QUALIFICATION_ESCALATION')
    fields(m['producer_build'],('files','sha256'))
    require(digest(m['producer_build']['files'])==m['producer_build']['sha256'],'FINE_PRODUCER_BUILD')
    names={a['path'] for a in m['assets']}
    require(type(m['assets']) is list and len(m['assets'])==len(names) and
            names in (ASSETS,ASSETS|{'provenance/recovery-source.json'}),'FINE_ASSET_SET')
    for a in m['assets']:
        fields(a,('path','bytes','sha256'))
        require(type(a['bytes']) is int and 0<a['bytes']<=8*1024**2,'FINE_ASSET_SIZE')
    require(digest(mathpart)==m['math_id'] and digest({k:v for k,v in m.items() if k!='bundle_id'})==m['bundle_id'],
            'FINE_SEMANTIC_IDENTITY')
    return m


def complete(directory):
    return _complete(directory)

def validate_pending(directory,mark):
    """Run the full existing saved-data checks before publishing a missing mark."""
    return _complete(directory,_pending_mark=mark)

def _complete(directory,*,_pending_mark=None):
    p=root(directory); mark=read(p/'COMPLETE.json') if _pending_mark is None else _pending_mark
    fields(mark,('format','bundle_id','math_id','manifest','operation_id','attempt_id'))
    require(mark['format']=='cpu-recipe-complete-v3' and mark['manifest']['path']=='manifest.json','FINE_COMPLETE_VERSION')
    verify_file(p/'manifest.json',mark['manifest']);m=structure(read(p/'manifest.json'))
    require((mark['bundle_id'],mark['math_id'])==(m['bundle_id'],m['math_id']),'FINE_COMPLETE_IDENTITY')
    for a in m['assets']: verify_file(inside(p,a['path']),a)
    parent=generated.structure(read(p/'provenance/fast-manifest.json'))
    recovered_parent=parent['version']==3 and parent['mode']=='fast'
    require(('provenance/recovery-source.json' in {a['path'] for a in m['assets']})==recovered_parent,'FINE_RECOVERY_ASSET_SCOPE')
    if recovered_parent:
        from .recovered import parameter_bytes, mathematical
        require(read(p/'provenance/recovery-source.json')==parent['recovery'] and
                parent['mathematical']==mathematical(parent['recovery'],parameter_bytes(read_bytes(p/'r0/parameters.npz'))),
                'FINE_RECOVERY_SOURCE_CHANGED')
    parent_mark=read(p/'provenance/fast-COMPLETE.json')
    require(parent_mark['format']==('cpu-recipe-complete-recovered-fast-v3' if recovered_parent else 'cpu-recipe-complete-v2'),
            'FINE_PARENT_VERSION')
    require(parent_mark['bundle_id']==parent['bundle_id'] and parent_mark['math_id']==parent['math_id'] and
            parent_mark['manifest']['sha256']==hashlib.sha256(read_bytes(p/'provenance/fast-manifest.json')).hexdigest(),
            'FINE_PARENT_COMMIT')
    require(parent['bundle_id']==m['provenance']['parent_bundle_id'] and
            parent['math_id']==m['provenance']['parent_math_id'],'FINE_PARENT_IDENTITY')
    for key,value in parent['mathematical'].items():
        if key!='math_files':require(m['mathematical'][key]==value,'FINE_PARENT_MATH_CHANGED')
    from .identity import math_closure
    require(m['mathematical']['math_files']==math_closure('fine'),'FINE_MATH_SOURCE_CHANGED')
    r0=parse_file(p/'r0/parameters.npz','r0')
    require({k:v for k,v in r0.items() if k!='expanded_bytes'}==m['mathematical']['r0_parameters'],'FINE_R0_CHANGED')
    for name in ('r0/parameters.npz','provenance/fit-intent.json','provenance/fit-receipt.json',*(['provenance/recovery-source.json'] if recovered_parent else [])):
        verify_file(p/name,next(a for a in parent['assets'] if a['path']==name))
    receipt=read(p/'provenance/fit-receipt.json');intent=read(p/'provenance/fit-intent.json')
    require(receipt['status']=='COMPLETE' and receipt['parameter_sha256']==r0['value_sha256'] and
            digest(intent)==parent['provenance']['fit_intent_sha256'],'FINE_PARENT_FIT_RECEIPT')
    parsed=coefficients(read_bytes(p/'p3/b.npz',4*1024**2),m['mathematical']['p3']['reference_wh'])
    report=read(p/'p3/solver-report.json')
    require(parsed==m['mathematical']['p3']['b'] and report['status']=='PASS' and
            report['coefficient_sha256']==parsed['value_sha256'] and
            report['shape']==parsed['members']['b']['shape'],'FINE_SOLVER_COEFFICIENT_IDENTITY')
    require(digest(read(p/'provenance/refine-intent.json'))==m['provenance']['refine_intent_sha256'],'FINE_REFINE_INTENT')
    total=0
    for file in p.rglob('*'):
        reject_reparse(file)
        if file.is_file():
            require(file.stat().st_size<=8*1024**2,'FINE_MEMBER_LIMIT');total+=file.stat().st_size
    require(total<=16*1024**2,'FINE_BUNDLE_LIMIT')
    return m,mark


@metadata_api
def inspect(directory, *, runtime_inventory=None):
    from .validation import _locator
    from .identity import runtime_status
    p=root(directory);m,mark=complete(p)
    return dict(format=m['format'],version=3,mode='fine',bundle_id=m['bundle_id'],math_id=m['math_id'],
        execution_build_id=m['producer_build']['sha256'],persistent_complete=True,assets_verified=True,
        state='PERSISTENT_COMPLETE',source_status=_locator(p,'source'),reference_status=_locator(p,'reference'),
        runtime_compatibility=runtime_status(m['runtime_requirements'],runtime_inventory),
        execution_qualification=m['qualification'],resident=False,availability='REPLAY_REQUIRED',
        operation_id=mark['operation_id'],attempt_id=mark['attempt_id'])


@metadata_api
def publish(directory,parent_directory,b_path,report,p3,intent,context,*,checkpoint=lambda name:None):
    parent,_=generated.complete(parent_directory)
    target=root(directory,write=True);target.mkdir(parents=True,exist_ok=True)
    control=target/'.pack';control.mkdir(exist_ok=True)
    payloads={name:read_bytes(Path(parent_directory)/name,8*1024**2) for name in
              ('r0/parameters.npz','provenance/fit-intent.json','provenance/fit-receipt.json')}
    if parent['version']==3 and parent['mode']=='fast':
        payloads['provenance/recovery-source.json']=read_bytes(Path(parent_directory)/'provenance/recovery-source.json')
    payloads.update({'provenance/fast-manifest.json':read_bytes(Path(parent_directory)/'manifest.json'),
        'provenance/fast-COMPLETE.json':read_bytes(Path(parent_directory)/'COMPLETE.json'),
        'p3/b.npz':read_bytes(b_path,4*1024**2),'p3/solver-report.json':canonical_json(report)+b'\n',
        'provenance/refine-intent.json':canonical_json(intent)+b'\n'})
    parsed=coefficients(payloads['p3/b.npz'],p3['reference_wh'])
    require(parsed==p3['b'] and report['coefficient_sha256']==parsed['value_sha256'],'FINE_PUBLISH_COEFFICIENT')
    from .identity import math_closure
    mathematical=copy.deepcopy(parent['mathematical']);mathematical['p3']=p3
    mathematical['math_files']=math_closure('fine')
    m=dict(format=parent['format'],version=3,mode='fine',mathematical=mathematical,math_id=digest(mathematical),
        runtime_requirements=context['runtime'],producer_build=context['executor_build'],
        qualification=dict(status='FINE_GENERATED_PENDING_SCOPED_QUALIFICATION',execution_qualified=False,
            general_input_qualified=False,CPU_ENABLED=False,AUTO_ENABLED=False,PRODUCTION_ENABLED=False,
            D01_extended=False,user_visual='PENDING'),
        assets=[dict(path=k,bytes=len(v),sha256=hashlib.sha256(v).hexdigest()) for k,v in sorted(payloads.items())],
        provenance=dict(parent_bundle_id=parent['bundle_id'],parent_math_id=parent['math_id'],
            refine_intent_sha256=digest(intent),job_id=intent['job_id'],attempt_id=intent['attempt_id'],
            producer='original_P3_CPU256',TEST_ONLY=context['TEST_ONLY']))
    m['bundle_id']=digest(m);structure(m)
    with locked(control/'target.lock',timeout=60):
        if (target/'COMPLETE.json').is_file():
            existing,_=complete(target);require(existing==m,'FINE_TARGET_CONFLICT')
            atomic_json(control/'record.json',dict(state='SUCCEEDED',intent=digest(intent),recovered=True))
            return inspect(target)
        record=control/'record.json'
        if record.is_file(): require(read(record)['intent']==digest(intent),'FINE_INTENT_CONFLICT')
        atomic_json(record,dict(state='RUNNING',intent=digest(intent)))
        try:
            for name,data in payloads.items():
                destination=inside(target,name);destination.parent.mkdir(parents=True,exist_ok=True)
                expected=next(a for a in m['assets'] if a['path']==name)
                if destination.exists(): verify_file(destination,expected);continue
                temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.partial')
                with temporary.open('xb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
                verify_file(temporary,expected);os.rename(temporary,destination)
            if (target/'manifest.json').exists(): require(read(target/'manifest.json')==m,'FINE_MANIFEST_CONFLICT')
            else: atomic_json(target/'manifest.json',m)
            checkpoint('bundle_before_complete')
            atomic_json(target/'COMPLETE.json',dict(format='cpu-recipe-complete-v3',bundle_id=m['bundle_id'],
                math_id=m['math_id'],manifest=file_record(target/'manifest.json',name='manifest.json'),
                operation_id=intent['job_id'],attempt_id=intent['attempt_id']))
            checkpoint('bundle_after_complete');complete(target)
            atomic_json(record,dict(state='SUCCEEDED',intent=digest(intent)))
            return inspect(target)
        except BaseException as error:
            atomic_json(control/'failures'/(uuid.uuid4().hex+'.json'),dict(error=repr(error),
                intent=digest(intent),complete_exists=(target/'COMPLETE.json').is_file()))
            atomic_json(record,dict(state='INTERRUPTED',intent=digest(intent)))
            raise


@metadata_api
def recover(directory):
    """Caller is the operation record-lock coordinator; no algorithm is reachable.

The target lock serializes two coordinators with the publisher. A valid COMPLETE
repairs only the bookkeeping state; absence of COMPLETE becomes INTERRUPTED.
"""
    target=root(directory,write=True);control=target/'.pack'
    if not control.is_dir():return dict(recipe_state='INTERRUPTED_OR_UNCOMMITTED',recipe_available=False)
    with locked(control/'target.lock',timeout=60):
        record=read(control/'record.json') if (control/'record.json').is_file() else None
        if (target/'COMPLETE.json').is_file():
            m,_=complete(target)
            if record and record['state']!='SUCCEEDED':atomic_json(control/'record.json',dict(record,state='SUCCEEDED',recovered_from_complete=True))
            return dict(recipe_state='PERSISTENT_COMPLETE',recipe_available=True,recipe_bundle_id=m['bundle_id'],
                        recipe_math_id=m['math_id'],recipe_directory=str(target))
        if record and record['state']=='RUNNING':atomic_json(control/'record.json',dict(record,state='INTERRUPTED',recovered_without_complete=True))
        return dict(recipe_state='INTERRUPTED_OR_UNCOMMITTED',recipe_available=False)
