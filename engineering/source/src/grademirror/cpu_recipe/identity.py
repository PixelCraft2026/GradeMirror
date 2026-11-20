"""Explicit math, runtime, execution-build and historical qualification layers."""
import sys
from pathlib import Path
from .primitives import digest,file_record,require
from ..light.portable import source_files
ROOT=Path(__file__).resolve().parents[3]
R0_MATH=['engine/independent_r0/'+n for n in ('profile.py','core.py','color_lab.py','tensor_ops.py','_initialization.py','_sampling.py','m05_lab.py','mapping_penalty.py')]
FINE_MATH=['engine/independent_r0_p3_lld10/'+n for n in ('area.py','p3_solver.py')]+['engine/cpu_postprocess/core.py','engine/cpu_postprocess/geometry.py']
CODEC_MATH=['engine/group15_codec.py','export_pixels.py']
BUILD_EXTRA=['cli.py','__main__.py','__init__.py','common.py','store.py','winproc.py','gm003_ipc.py','gm004a_operations.py','fine_ipc.py','fine_resources.py','fine_contract.py','engine/profiles.py','engine/independent_r0/adapter.py','engine/cpu_r0_fit/optimizer_policy.py','engine/cpu_r0_render/executor.py']
def closure(names):
    return [dict(path='grademirror/'+name,sha256=file_record(ROOT/'src/grademirror'/name)['sha256'])for name in sorted(set(names))]
def math_closure(mode):
    files = closure(R0_MATH+CODEC_MATH+(FINE_MATH if mode=='fine'else[]))
    if mode == 'fine':
        files = scoped_math_closure(files)
    return files


def scoped_math_closure(files):
    from ..light.math_bridge import restore_core_guard
    return restore_core_guard(files, expected='40948d368a5fbf50f6d7cb6e59c8e7b09a64823f903f3771a84acd1f7a938f84')

def execution_build(mode):
    names=R0_MATH+CODEC_MATH+BUILD_EXTRA+['cpu_recipe/frozen-origin.json']+(FINE_MATH if mode=='fine'else[])
    for package in ('cpu_recipe','engine/cpu_r0_image','engine/cpu_fine_image','engine/cpu_postprocess','engine/independent_r0_p3_lld10'):
        names += [p.relative_to(ROOT/'src/grademirror').as_posix()for p in source_files(ROOT/'src/grademirror'/package,'*.py')]
    value=dict(format='cpu-recipe-execution-build-v1',files=closure(names),worker_files=[dict(path='tools/gm008_solver_worker.py',sha256=file_record(ROOT/'tools/gm008_solver_worker.py')['sha256']),dict(path='tools/gm050_stage_worker.py',sha256=file_record(ROOT/'tools/gm050_stage_worker.py')['sha256'])],execution_qualified=False)
    return dict(value,build_sha256=digest(value))
def runtime_status(requirements,inventory=None):
    observed=dict(python='.'.join(map(str,sys.version_info[:3])),python_ABI=sys.implementation.cache_tag)
    if inventory is None:return dict(status='UNKNOWN',observed=observed,requirements=requirements,numeric_runtime='NOT_IMPORTED_NOT_PROBED',execution_qualified=False)
    require(type(inventory)is dict,'RUNTIME_INVENTORY')
    return dict(status='DECLARED_MATCH_ONLY'if inventory==requirements else'INCOMPATIBLE',observed=observed,requirements=requirements,execution_qualified=False)
