"""Owner-managed fixed CPU fine entry points; never silently train or replay."""
import gc,time
from dataclasses import dataclass
from pathlib import Path
from ...common import atomic_json,strict_load
from ...winproc import current_identity
from . import contract as c,publication,execution,jobs
@dataclass
class OwnedInputs:
    O:object
    mapping:object
    reference:object=None
    release_reference:object=None
    owner:object=None
    def validate(self,request):
        import numpy as np
        from ..cpu_postprocess.executor import array_hash
        c.need(self.owner==current_identity() and self.O.dtype==np.float32 and self.O.flags.c_contiguous and not self.O.flags.writeable and list(self.O.shape)==request['O']['shape'] and array_hash(self.O)==request['O']['sha256'],'FINE_OWNED_INPUT')
        c.need(self.mapping.size==self.O.nbytes,'FINE_MAPPING_CAPACITY')
def load_recipe(directory):
    import numpy as np
    from ..cpu_postprocess.executor import array_hash
    m=publication.recover(directory);f=c.fixed();c.need(m['case']=='group15' and m['backend_sha256']==c.implementation()['sha256'],'FINE_RECIPE_VERSION')
    c.need(m['reference_wh']==[1024,1536] and m['O']==dict(shape=[7008,4672,3],dtype='float32',sha256=f['expected_O_sha256']) and m['seed']==c.seed_binding(),'FINE_RECIPE_INPUTS')
    c.need(m['b_array']==dict(shape=[24897,3],dtype='float64',sha256=m['solver']['coefficient_sha256']) and len(m['ranges'])==3,'FINE_RECIPE_ARRAY')
    for r in m['ranges']:
        c.need(set(r)=={'kmin','kmax','count','min','max','delta','sigma','J'} and r['count']==r['kmax']-r['kmin']+1 and 2<=r['count']<=257 and (r['delta'],r['sigma'],r['J'])==(1/64,.1,8),'FINE_RECIPE_RANGES')
    with np.load(m['b']['path'],allow_pickle=False)as z:
        c.need(z.files==['b'] and z['b'].dtype==np.float64 and list(z['b'].shape)==[24897,3] and np.isfinite(z['b']).all() and array_hash(z['b'])==m['b_array']['sha256'],'FINE_RECIPE_B_HASH')
    return m
def _intent(request):return c.digest({k:v for k,v in request.items()if k not in ('core_rows','work_root','schema')})
def _save_b(p,b):
    import numpy as np
    c.need(b.dtype==np.float64 and b.flags.c_contiguous and np.isfinite(b).all(),'FINE_B_ARRAY')
    with p.open('xb')as f:np.savez(f,b=b)
    with np.load(p,allow_pickle=False)as z:c.need(z.files==['b'] and np.array_equal(z['b'],b),'FINE_B_READBACK')
def _reference64(reference):
    import numpy as np
    return np.ascontiguousarray(reference.astype(np.float64)/np.iinfo(reference.dtype).max)
def prepare(seed,request):
    from ...store import locked
    c.validate(request,'prepare');p=c.output(request['recipe_directory']);p.mkdir(parents=True,exist_ok=True)
    with locked(p/'prepare-owner.lock',timeout=.25):return _prepare(seed,request)
def _prepare(seed,request):
    import numpy as np
    from ..independent_r0_p3_lld10.area import area_prefix
    from ..cpu_postprocess.executor import ArrayProvider,array_hash
    from ..cpu_postprocess.core import Gate
    from ..independent_r0_p3_lld10.ledger import Ledger
    c.validate(request,'prepare');intent=_intent(request);p=c.output(request['recipe_directory']);p.mkdir(parents=True,exist_ok=True)
    if (p/'COMPLETE.json').exists():publication.recover(p,intent);return dict(persistent_complete=True,resident=False,availability='REPLAY_REQUIRED')
    c.need(not(p/'fine-recipe.json').exists() and not(p/'b.npz').exists(),'FINE_INCOMPLETE_NO_RETRY');seed.validate(request)
    c.need(request['case']=='group15','FINE_PREPARE_NATIVE_ONLY')
    ref=seed.reference;c.need(ref is not None and ref.dtype==np.uint8 and list(ref.shape)==[1536,1024,3] and array_hash(ref)==c.fixed()['native']['reference_identity']['pixel_sha256'],'FINE_REFERENCE')
    run=c.output(request['work_root']);run.mkdir(parents=True,exist_ok=True)
    h=area_prefix(seed.O,request['reference_wh'],lambda:False)
    # Original service.reference_float64 order: integer -> FP64 -> divide.
    target=_reference64(ref)
    hh,th=array_hash(h),array_hash(target);atomic_json(run/'P3-inputs.json',dict(H_sha256=hh,T_sha256=th,shape=list(h.shape),H_bytes=h.nbytes,T_bytes=target.nbytes,normalization='uint8.astype(float64)/255',identity_control=False))
    b,report=jobs.solve(run,h,target,[seed.O.shape[1],seed.O.shape[0]]);del h,target,ref;seed.reference=None
    if seed.release_reference is not None:seed.release_reference()
    gc.collect();c.need(list(b.shape)==[24897,3],'FINE_B_SHAPE');_save_b(p/'b.npz',b)
    L=Ledger(run/'range-scan-actual.json',dict(P3_scalar_evaluations=3*seed.O.shape[0]*seed.O.shape[1]),deadline=time.monotonic()+600)
    ranges=execution.range_scan(ArrayProvider(seed.O,b),request,Gate(L.take,lambda:False,lambda **x:None,time.monotonic()+600));L.save('COMPLETE');del b
    atomic_json(run/'ranges-frozen.json',dict(ranges=ranges,b=c.binding(p/'b.npz'),before_LLF=True))
    r=execution.execute(seed.mapping,seed.O,p/'b.npz',request,ranges,run/'CPU512',core_rows=512)
    try:
        result=strict_load(r.path/'executor-result.json');meta=dict(schema=c.RECIPE,backend=c.BACKEND,backend_sha256=request['backend_sha256'],case=request['case'],intent=intent,seed=request['seed'],O=request['O'],reference_wh=request['reference_wh'],b=c.binding(p/'b.npz'),b_array=dict(shape=[24897,3],dtype='float64',sha256=report['coefficient_sha256']),ranges=ranges,solver=report,H_sha256=hh,T_sha256=th,hashes=dict(O=request['O']['sha256'],**result['hashes']),color=c.fixed()['native']['color_policy'],execution=dict(first_core_rows=512,mathematical_constants=__import__('grademirror.engine.cpu_postprocess.contract',fromlist=['CONSTANTS']).CONSTANTS,solver_cleanup=c.binding(run/'solver-cleanup.json'),receiver_result=c.binding(r.path/'executor-result.json')))
        publication.publish(p,meta);execution.activate(r);return r
    except BaseException:r.release();raise
def replay(fine_recipe,request):
    c.validate(request,'replay');m=load_recipe(request['recipe_directory']);c.need(fine_recipe==m,'FINE_REPLAY_RECIPE');return _replay_owned(m,request)
def _replay_owned(m,request):
    # The caller explicitly supplies the new independently rendered O by binding
    # it through a context manager. No automatic fit/CG/reference decode exists.
    seed=_OWNED.get(request['key']);c.need(seed is not None,'FINE_REPLAY_OWNED_INPUT_REQUIRED');seed.validate(request)
    c.need(m['seed']==request['seed'] and m['O']==request['O'] and m['reference_wh']==request['reference_wh'],'FINE_REPLAY_INPUTS')
    r=execution.execute(seed.mapping,seed.O,Path(request['recipe_directory'])/'b.npz',request,m['ranges'],request['work_root'],core_rows=256)
    try:
        result=__import__('grademirror.common',fromlist=['strict_load']).strict_load(r.path/'executor-result.json');c.need(dict(O=request['O']['sha256'],**result['hashes'])==m['hashes'],'FINE_REPLAY_FULL_HASH');execution.activate(r);return r
    except BaseException:r.release();raise
_OWNED={}
from contextlib import contextmanager
@contextmanager
def owned_replay(seed,request):
    c.need(request['key']not in _OWNED,'FINE_REPLAY_OWNER_BUSY');_OWNED[request['key']]=seed
    try:yield
    finally:del _OWNED[request['key']]
