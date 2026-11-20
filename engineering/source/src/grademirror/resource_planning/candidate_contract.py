"""V2 extends only saved-backend metadata; the V1 validator remains authoritative."""
import copy
from . import contract as old

REQUEST='gm029-plan-request-v2'
RESPONSE='gm029-resource-plan-v2'
HYBRID='cuda-r0-cpu-lld10-v1'
EXTRA=('backend_id','execution_manifest_schema','cpu_bindings_sha256')
REQUEST_V3='gm042-plan-request-v3'
RESPONSE_V3='gm042-resource-plan-v3'
EXTRA_V3=('core_rows','execution_manifest_sha256')

def runtime_shape(value, template):
    if isinstance(template,dict):
        old.fields(value,tuple(template))
        for key in template:runtime_shape(value[key],template[key])
    elif type(template)is int:old.integer(value,1)
    else:old.text(value)

def normalize_request(value):
    old.bounded(value)
    old.require(len(old.json.dumps(value,ensure_ascii=False,allow_nan=False).encode())<=old.MAX_BYTES,'PLAN_QUOTA')
    old.require(type(value)is dict and value.get('schema_version')==REQUEST,'PLAN_SCHEMA_UNSUPPORTED')
    base=copy.deepcopy(value);base['schema_version']=old.REQUEST
    recipe=base.get('recipe');extra={}
    if isinstance(recipe,dict):
        for key in EXTRA:
            if key in recipe:extra[key]=recipe.pop(key)
        backend=extra.get('backend_id','cuda-'+str(base.get('mode')))
        old.require(backend in ('cuda-fast','cuda-fine',HYBRID),'PLAN_BACKEND_INVALID')
        if backend==HYBRID:
            old.require(set(EXTRA)<=extra.keys() and 'execution_contract_sha256'in recipe,'HYBRID_VERSION_REQUIRED')
            old.text(extra['execution_manifest_schema']);old.hash_value(extra['cpu_bindings_sha256'])
            from ..hybrid_replay.contract import RUNTIME
            runtime_shape(recipe.get('runtime'),RUNTIME)
        else:
            old.require(set(extra)<= {'backend_id'})
            from ..engine.independent_r0 import profile as fast
            from ..engine.independent_r0_p3_lld10 import profile as fine
            runtime_shape(recipe.get('runtime'),fast.RUNTIME if base.get('mode')=='fast'else fine.RUNTIME)
    result=old.normalize_request(base)
    result['schema_version']=REQUEST
    if 'recipe'in result:result['recipe'].update(extra)
    return result

def legacy_request(request):
    result=copy.deepcopy(request);result['schema_version']=old.REQUEST
    if 'recipe'in result:
        for key in EXTRA:result['recipe'].pop(key,None)
        if request['schema_version']==REQUEST_V3:
            for key in EXTRA_V3:result['recipe'].pop(key,None)
    return result

def normalize_request_v3(value):
    """Reuse every V2 bound; only hybrid replay adds two required identities."""
    old.bounded(value)
    old.require(len(old.json.dumps(value,ensure_ascii=False,allow_nan=False).encode())<=old.MAX_BYTES,'PLAN_QUOTA')
    old.require(type(value)is dict and value.get('schema_version')==REQUEST_V3,'PLAN_SCHEMA_UNSUPPORTED')
    base=copy.deepcopy(value);base['schema_version']=REQUEST;extra={}
    recipe=base.get('recipe')
    if isinstance(recipe,dict):
        for name in EXTRA_V3:
            if name in recipe:extra[name]=recipe.pop(name)
        if recipe.get('backend_id')==HYBRID:
            old.require(set(extra)==set(EXTRA_V3),'HYBRID_MANIFEST_CORE_REQUIRED')
            old.integer(extra['core_rows'],1);old.hash_value(extra['execution_manifest_sha256'])
        else:old.require(not extra)
    result=normalize_request(base);result['schema_version']=REQUEST_V3
    if 'recipe'in result:result['recipe'].update(extra)
    return result
