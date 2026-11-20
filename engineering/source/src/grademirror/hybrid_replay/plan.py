"""Stable manual execution intent and conservative staged capacities."""
from pathlib import Path
from ..common import strict_load, sha_file, reject_reparse
from ..gpu_resources import fast_plan
from ..light.resources import pool_budget, scoped_gpu_plan, workspace_limit
from .contract import parent, read_manifest, execution_contract, digest, require, RUNTIME
from . import BACKEND_ID, CONTRACT_VERSION

GIB=1024**3;MIB=1024**2
BUDGET=dict(host=2*GIB,compute=6*GIB,control_ipc=512*MIB,pool=17*GIB//2)


def capacity(shape, bits):
    h,w,c=shape;require(c==3 and type(h)is int and type(w)is int and min(h,w)>0)
    s=h*w*3*(bits//8);f=h*w*3*4;field=min(h,512)*w*3*4
    # Shared S transfer may briefly retain the original integer input. R0
    # result -> O is also a documented transient double full float.
    return dict(source_bytes=s,O_bytes=f,Y_bytes=f,field_bytes=field,frame_bytes=4*MIB,
        decode=dict(additional_bytes=2*s+128*MIB,minimum_commit_headroom=2*s+512*MIB),
        R0=dict(additional_bytes=3*f+s+2*GIB,minimum_commit_headroom=3*f+s+3*GIB),
        CPU=dict(additional_bytes=f+field+512*MIB+512*MIB,minimum_commit_headroom=f+field+1536*MIB),
        output=dict(additional_bytes=3*s+256*MIB,minimum_commit_headroom=3*s+768*MIB),
        physical_margin=GIB,host_limit=BUDGET['host'],compute_limit=BUDGET['compute'])


def execution_device(provenance):
    """Use the frozen current device for packaged mixed operations."""
    from ..light.runtime import operation
    if operation() is None:
        return provenance
    from ..gm003_supervisor import _gpu_snapshot
    current = _gpu_snapshot()
    return {key: current[key] for key in ("name", "uuid", "driver_version")}


def resolve(project, manifest, source, reference, output, backend, experimental):
    require(experimental is True,'HYBRID_EXPERIMENTAL_OPT_IN_REQUIRED')
    require((project is not None)!=(manifest is not None),'HYBRID_SOURCE_EXCLUSIVE')
    if project is not None:
        require(backend==BACKEND_ID and source is None and reference is None,'HYBRID_BACKEND_REQUIRED')
        from ..portable_project import open_portable_project
        from ..engine.profiles import runtimes
        from ..gm004a_api import _inputs
        view=open_portable_project(Path(project),available_runtimes=runtimes())
        require(view.get('profile_id')=='independent-r0-p3-lld10-v1' and
                not view.get('algorithm_retired') and not view.get('read_only'),'HYBRID_FINE_PARENT_REQUIRED')
        directory=Path(view['recipe_dir']);recipe,assets=parent(directory)
        locators=_inputs(view);prior=None
    else:
        require(backend in (None,BACKEND_ID) and source is not None and reference is not None,
                'HYBRID_MANIFEST_LOCATORS_REQUIRED')
        prior,recipe=read_manifest(manifest);directory=Path(manifest).resolve().parent
        _,assets=parent(directory);locators={}
        for name,p in (('source',source),('reference',reference)):
            p=Path(p).resolve();reject_reparse(p);expected=recipe[name+'_identity']
            require(p.is_file() and p.stat().st_size==expected['file_size_bytes'] and
                    sha_file(p)==expected['file_sha256'],'HYBRID_INPUT_IDENTITY')
            locators[name]=dict(path=str(p),bytes=p.stat().st_size,sha256=sha_file(p))
    for name in ('source','reference'):
        require(locators[name]['sha256']==recipe[name+'_identity']['file_sha256'], 'HYBRID_INPUT_IDENTITY')
    source_id=recipe['source_identity'];h,w,_=source_id['shape'];mem=capacity(source_id['shape'],source_id['bit_depth'])
    jobs=pool_budget(BUDGET, 'mixed')
    mem.update(host_limit=jobs['host'],compute_limit=jobs['compute'])
    require(2*mem['source_bytes']+mem['O_bytes']+mem['Y_bytes']+mem['field_bytes']+256*MIB<=jobs['host'],
            'HYBRID_HOST_STATIC_CAPACITY')
    value=dict(backend_id=BACKEND_ID,execution_contract_version=CONTRACT_VERSION,
        execution_contract_sha256=execution_contract()[1],parent_recipe_sha256=recipe['recipe_sha256'],
        source_identity=source_id,reference_identity=recipe['reference_identity'],assets=assets,
        ranges=recipe['postprocess']['ranges'],core_rows=512,runtime=RUNTIME,device=execution_device(recipe['device']),
        resource_budget=jobs,capacities=mem,
        R0_gpu=scoped_gpu_plan(dict(width=w,height=h),fast_plan(w,h,operation='replay'),fine=False,operation='replay'),
        durations=dict(R0=900,CPU=3600,operation=5400,preview=120,export=300),
        tile_side=512,tiles=((h+511)//512)*((w+511)//512),fallback=False,
        migration='explicit-parent' if prior is None else'explicit-manifest')
    from ..engine.cpu_postprocess.geometry import plan as stripes, workspace_bound
    geometry=stripes(h,w,512);n=len(geometry);anchors=sum(r['count'] for r in value['ranges'])
    footprints=n*6;remaps=n*2*anchors;rows=sum(s.base_rows for s in geometry)
    value['CPU_budget']=dict(scalar_LLF_footprints=footprints,remap_pyramids=remaps,
        D_calls=(remaps+footprints)*7,U_calls=remaps*14+footprints*7,
        P3_scalar_evaluations=3*w*(h+rows),scalar_compositions=3*n)
    value['CPU_extra']=dict(CPU_global_scans=1,base_remap_pixels=2*anchors*w*rows)
    value['workspace_bound']=max(workspace_bound(s)['total_bytes'] for s in geometry)
    require(value['workspace_bound']<=workspace_limit(h,w,512),'HYBRID_CPU_WORKSPACE')
    intent=dict(backend_id=BACKEND_ID,execution_contract_version=CONTRACT_VERSION,
        execution_contract_sha256=value['execution_contract_sha256'],recipe_sha256=recipe['recipe_sha256'],
        source_sha256=locators['source']['sha256'],reference_sha256=locators['reference']['sha256'],
        output_root=str(Path(output).resolve()),resource_budget=jobs,core_rows=512,
        migration=value['migration'],parent_manifest_sha256=None if prior is None else prior['manifest_sha256'],
        revision='r000002',tiles=value['tiles'],profile_id=recipe['profile_id'],
        profile_contract_sha256=recipe['profile_contract_sha256'],core_sha256=recipe['core_sha256'])
    return value,intent,locators,directory,recipe,prior
