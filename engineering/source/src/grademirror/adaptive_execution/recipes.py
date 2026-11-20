"""TEST_ONLY recipe transaction fixture at the explicitly frozen boundary."""
from pathlib import Path
from ..common import strict_load
from ..cpu_recipe import generated,generated_fine
from ..cpu_recipe.assets import parse_file
from .intent import need

def prepare(c,spec,checkpoint=lambda name:None):
    q=strict_load(Path(c['qualification']['path']));r=c['request'];out=Path(c['output_root'])
    if r['operation']=='replay':
        directory=Path(r['recipe']['directory'])
        from ..cpu_recipe.validation import complete
        m,_=complete(directory)
        need(m['provenance']['TEST_ONLY'] is True and m['version']==(2 if c['mode']=='fast' else 3) and
            m['mathematical']['inputs']['source_identity']==c['source_identity'] and
            m['bundle_id']==r['recipe']['bundle_id'] and m['math_id']==r['recipe']['math_id'],'APPLICATION_RECIPE_SCOPE')
        return directory,m
    parent=Path(r['recipe']['directory']) if r['operation']=='refine' else out/'fast-recipe'
    if r['operation']=='refine':
        inherited=generated.complete(parent)[0]
        need(inherited['bundle_id']==r['recipe']['bundle_id'] and inherited['math_id']==r['recipe']['math_id'] and
            inherited['version']==2 and inherited['provenance']['TEST_ONLY'] is True and
            inherited['mathematical']['inputs']['source_identity']==c['source_identity'],'APPLICATION_REFINE_PARENT_SCOPE')
    else:
        parameter=Path(q['parameter_file']);value=parse_file(parameter,'r0')['value_sha256']
        intent=dict(TEST_ONLY=True,metadata_fixture=True,request=r)
        receipt=dict(status='COMPLETE',job_id=spec['job_id'],attempt_id=spec['attempt_id'],producer_build=c['executor_build']['sha256'],
            parameter_sha256=value,TEST_ONLY=True,metadata_fixture=True,counts=dict(fit=0,updates=0,forward=0,model_construct=0))
        generated.publish(parent,parameter,intent,receipt,c,checkpoint=checkpoint)
    if c['mode']=='fast':return parent,generated.complete(parent)[0]
    b=Path(q['b_file']);parsed=generated_fine.coefficients(b.read_bytes(),[8,8])
    report=dict(status='PASS',coefficient_sha256=parsed['value_sha256'],shape=[4,3],TEST_ONLY=True,actual_science=dict(P3=0,CG=0,LLF=0))
    ranges=[dict(kmin=-2,kmax=66,count=69,min=0.,max=1.,delta=1/64,sigma=.1,J=8) for _ in range(3)]
    p3=dict(b=parsed,reference_wh=[8,8],H_sha256='0'*64,T_sha256='0'*64,ranges=ranges,
        constants=dict(J=8,P3_h=8,sigma=.1,delta=1/64,composition='FP32(FP64(O)+(FP64(LP)-FP64(LO)))',range_margin=2/255),
        solver_policy=dict(rtol=1e-8,atol=1e-12,maxiter=2000))
    target=out/'fine-recipe';generated_fine.publish(target,parent,b,report,p3,
        dict(job_id=spec['job_id'],attempt_id=spec['attempt_id'],TEST_ONLY=True,metadata_fixture=True),c,checkpoint=checkpoint)
    return target,generated_fine.complete(target)[0]
