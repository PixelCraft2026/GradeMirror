"""Pinned GM062 advisory authority. It cannot grant a numerical lease."""
import hashlib
import json
from pathlib import Path
from .intent import normalize, need, digest
from ..cpu_replay.contract import ROOT

E = ROOT/'docs/evidence/gm-062/20261104-v1'
W = ROOT/'tests/workspaces/g62/20261104-v1'
SCHEMA = 'cpu-application-execution-v6'


def load():
    from ._gm062_authority import CANDIDATE_SHA256
    data=(E/'candidate-plan.json').read_bytes()
    need(hashlib.sha256(data).hexdigest()==CANDIDATE_SHA256,'RESOURCE_V3_AUTHORITY_CHANGED')
    value=json.loads(data)
    need(value['schema']=='gm062-candidate-plan-v1' and value['TEST_ONLY'] is False,
         'RESOURCE_V3_AUTHORITY_FIELDS')
    for row in value['build_files']+value['runtime_bindings']:
        p=Path(row['path']);b=p.read_bytes()
        need(len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],
             'RESOURCE_V3_BUILD_OR_RUNTIME_CHANGED',str(p))
    return value


def applies(request):
    """Only fixed same-photo replay changes the default planner version."""
    r=normalize(request)
    if r['operation']!='replay':return False
    if r['source']['file']['sha256']!='67af729b080439e96ec8485c8163a232624891b3cf33d30f739d1dfa2d5491b6':return False
    return r['source']['metadata']==dict(width=4672,height=7008,bits=8,format='JPEG')


def scope(request):
    r=normalize(request);q=load()
    if not applies(r):return 'ESTIMATE_ONLY_INPUT_OR_OPERATION_UNQUALIFIED'
    case=q['requests'][r['quality']]
    # Preferences/user caps may differ; mathematical input and recipe may not.
    keys=('schema','quality','operation','source','reference','recipe','output')
    if any(r[k]!=case[k] for k in keys):return 'INPUT_OR_RECIPE_MISMATCH'
    return 'FIXED_REPLAY_MODEL_UNCERTIFIED'


def context(plan,workspace):
    from .low_memory_plan import validate
    validate(plan);ws=Path(workspace).resolve()
    need(ws.is_relative_to(W),'RESOURCE_V3_WORKSPACE_SCOPE')
    return dict(schema=SCHEMA,TEST_ONLY=False,request=plan['request'],
                workspace=str(ws),output_root=str(ws/'output'),
                resource_plan=plan,resource_plan_id=plan['plan_id'])


def check_context(c):
    from .low_memory_plan import validate
    need(type(c)is dict and c.get('schema')==SCHEMA and c.get('TEST_ONLY') is False,
         'RESOURCE_V3_CONTEXT')
    p=validate(c['resource_plan'])
    need(c['resource_plan_id']==p['plan_id'] and c['request']==p['request'],
         'RESOURCE_V3_CONTEXT_CHANGED')
    need(Path(c['workspace']).resolve().is_relative_to(W),'RESOURCE_V3_WORKSPACE_SCOPE')
    return p


def admit(c,stage='native_start',*,snapshot=None,now=None):
    """All v6 entry points terminate here before old floors or allocation.

    There is intentionally no dormant route to a v5 worker. A later certified
    candidate needs a new pinned authority and execution implementation.
    """
    from .resource_policy_v3 import evaluate
    from .memory_snapshot import collect
    p=check_context(c)
    facts=collect(c['workspace']) if snapshot is None else snapshot
    row=evaluate(p['request'],facts,stage,now=now,model=p['allocation_model'])
    need(row['resource_feasible'] is True,'RESOURCE_NOT_READY',str(row))
    need(scope(p['request'])=='FIXED_REPLAY_MODEL_UNCERTIFIED','RESOURCE_V3_SCOPE_UNQUALIFIED')
    need(row['model_certified'] and row['target_fits'],'RESOURCE_V3_MODEL_UNQUALIFIED',str(row))
    # No observed/calibrated numeric success was issued under this negative
    # candidate. This is not a switch that a user-authored JSON can enable.
    need(False,'RESOURCE_V3_EXECUTION_NOT_QUALIFIED')

