"""Strict user intent and canonical, independently versioned plan identities."""
import contextvars
from pathlib import Path
from ..common import GMError, canonical_json, sha_bytes, sha_file
from . import POLICY, REQUEST, PLAN

MIB = 1024**2
CURRENT = contextvars.ContextVar('guarded_resource_intent', default=None)

def require(ok, code='RESOURCE_PLAN_INVALID', detail='Resource execution binding invalid'):
    if not ok: raise GMError(code, detail, 5)

def digest(value):
    return sha_bytes(canonical_json(value))

def normalize(value, *, experimental):
    require(type(value) is dict, 'RESOURCE_REQUEST_INVALID')
    fields={'policy','ram_limit_mib','commit_limit_mib','vram_limit_mib','cpu_core_rows'}
    require(set(value)<=fields and value.get('policy')==POLICY, 'RESOURCE_POLICY_INVALID')
    require(experimental is True, 'RESOURCE_EXPERIMENTAL_OPT_IN_REQUIRED')
    result={'policy':POLICY}
    for key in sorted(fields-{'policy','cpu_core_rows'}):
        item=value.get(key)
        require(item is None or type(item) is int and 0<item<=(2**63-1)//MIB,
                'RESOURCE_LIMIT_INVALID', key+' must be a positive bounded integer MiB')
        result[key]=item
    if 'cpu_core_rows'in value:
        from ..hybrid_fit.tiers import rows
        if rows(value['cpu_core_rows'])==256:result['cpu_core_rows']=256
    return result

def policy_binding():
    root=Path(__file__).resolve().parents[3]
    from ..light.portable import source_files, source_sha
    files=source_files(Path(__file__).parent, '*.py')
    files += [root/'src/grademirror'/n for n in ('resource_policy.py','gpu_resources.py','fine_resources.py','image_limits.py')]
    files += sorted((root/'schemas').glob('resource-execution-*-v1.json'))
    files += source_files(root/'src/grademirror/resource_planning', '*.py')
    return dict(policy=POLICY, implementation={p.relative_to(root).as_posix():source_sha(p) for p in files})

def attach(semantics):
    intent=CURRENT.get()
    return semantics if intent is None else dict(semantics,resource_intent=intent)

def guarded(spec):
    return isinstance(spec.get('semantics',{}).get('resource_intent'),dict)

def check_key(ops, key):
    prior=ops.lookup_request(key)
    if prior is not None:
        require(prior[1]['semantics'].get('resource_intent')==CURRENT.get(),
                'REQUEST_KEY_CONFLICT','Resource intent differs for this request key')
    return prior
