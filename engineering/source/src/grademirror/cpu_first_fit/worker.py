"""Thin CPU adapters. The original fit loop and renderer perform all math."""
import gc
import hashlib
import math
import time
from pathlib import Path
from ..common import atomic_json, strict_load
from ..cpu_replay.contract import need
from ..cpu_replay.numeric import attach
from .contract import check_session
from ..adaptive_execution.first_hooks import start as _application_start,ready as _application_ready


def preload_runtime(fit_runtime=False):
    """Resolve the original CPU setup/Adam delayed imports before mathematics."""
    import multiprocessing.shared_memory
    import cv2
    if fit_runtime:
        import torch._dynamo


def validate_payload(payload,role):
    fields={'work','S','reference' if role=='fit' else 'O','cancel','operation','reservation'}
    if role=='r0':fields.add('bundle')
    need(type(payload) is dict and set(payload)==fields,'FIRST_FIT_WORKER_PAYLOAD_FIELDS')
    work=Path(payload['work']).resolve();spec=check_session(work.parent);c=spec['execution']
    need(work.name==('fit' if role=='fit' else 'R0') and Path(payload['operation']).resolve()==Path(spec['operation_path']) and
         Path(payload['cancel']).resolve()==Path(spec['operation_path'])/'cancel.signal' and payload['reservation']==c['science'],
         'FIRST_FIT_WORKER_RESOURCE_POINTER')
    mappings=strict_load(work.parent/'shared-mappings.json')
    for key in ('S','reference' if role=='fit' else 'O'):
        desc=payload[key];expected={'name','shape','dtype'}|({'readonly'} if key!='O' else set())
        identity=c['reference_identity'] if key=='reference' else c['source_identity']
        need(type(desc) is dict and set(desc)==expected and desc['shape']==identity['shape'] and
             desc['dtype']==('float32' if key=='O' else identity['dtype']) and (key=='O' or desc['readonly'] is True),
             'FIRST_FIT_WORKER_SHARED_DESCRIPTOR')
        need(desc['name']==mappings[key]['name'],'FIRST_FIT_WORKER_MAPPING_OWNERSHIP')
    if role=='r0':
        expected=c.get("render_bundle") or spec['semantics']['bundle_directory']
        need(Path(payload['bundle']).resolve()==Path(expected).resolve(),'FIRST_FIT_WORKER_BUNDLE_POINTER')
    return spec


def _torch_build(context):
    from ..light.runtime import operation
    plan=operation()
    if plan is not None and context.get("light_plan")==plan["plan_id"] and plan["selection"]["backend"]=="cpu":
        profile=plan["numeric_profile"]
        return plan["environment"][profile]["versions"]["torch"]
    return "2.5.1+cu124"


def fit(payload):
    from ..preview.startup_fixture import enabled as startup_fixture_enabled
    need(not startup_fixture_enabled(), "GM080_FIT_FORBIDDEN")
    import numpy as np
    import torch
    from ..engine.independent_r0 import adapter
    from ..engine.cpu_r0_image.executor import _fit_loop, Cancelled
    work = Path(payload['work'])
    spec = validate_payload(payload,'fit')
    c = spec['execution']
    preload_runtime(True)
    from ..cpu_replay.runtime import require_runtime
    require_runtime(work)
    need(c['phase'] == 'fit' and np.__version__ == '1.26.4' and torch.__version__ == _torch_build(c) and
         not torch.cuda.is_initialized(), 'FIRST_FIT_NUMERIC_RUNTIME')
    sm = rm = None
    model = None
    checks = 0
    def cancelled():
        nonlocal checks
        checks += 1
        if c['TEST_ONLY'] and c['cancel_after_updates'] == 1 and checks == 3:
            atomic_json(Path(payload['cancel']), dict(TEST_ONLY=True, after_update=1, before_next_sample=True))
        return Path(payload['cancel']).exists() or (work.parent / 'stop.signal').exists()
    def progress(value):
        if c.get("light_plan"):
            value=dict(value,gm066_monotonic=time.monotonic())
        atomic_json(Path(payload['operation']) / 'progress.json', value)
    try:
        sm, source = attach(payload['S'])
        rm, reference = attach(payload['reference'])
        if c['TEST_ONLY']:
            before, after = np.ascontiguousarray(source), np.ascontiguousarray(reference)
            geometry = dict(synthetic=True, training_hw=list(source.shape[:2]))
        else:
            # Exactly the original normalize-before-INTER_AREA helper; no Q8.
            _application_start(c,'normalize_source')
            before, sg = adapter.downscale_native(source, 512)
            _application_start(c,'normalize_reference')
            after, rg = adapter.downscale_native(reference, 512)
            need(before.shape == after.shape, 'FIRST_FIT_TRAINING_GRID_MISMATCH')
            geometry = dict(source=sg, reference=rg)
        need(before.dtype == after.dtype == np.float32 and before.shape == after.shape and
             np.isfinite(before).all() and np.isfinite(after).all(), 'FIRST_FIT_TRAINING_ARRAYS')
        atomic_json(work / 'training-inputs.json', dict(shape=list(before.shape), resize_geometry=geometry,
                    source_sha256=hashlib.sha256(memoryview(before).cast('B')).hexdigest(),
                    reference_sha256=hashlib.sha256(memoryview(after).cast('B')).hexdigest(),
                    normalizations=0 if c['TEST_ONLY'] else 2, INTER_AREA=0 if c['TEST_ONLY'] else 2,
                    full_normalized_temporaries='one source then one reference; freed before fit',
                    source_mapping_readonly=True))
        before.flags.writeable = after.flags.writeable = False
        _application_ready(c,'training',work)
        from ..adaptive_execution.portable_contract import enabled as portable_enabled, require_training_arrays
        if portable_enabled(c):
            require_training_arrays(before.shape, after.shape, c['settings'])
        _application_start(c,'fit')
        model, record, counts, sections = _fit_loop(before, after, c['settings'], cancelled, progress)
        params = adapter._parameters(model)
        parameter_hash = adapter._parameter_hash(params)
        np.savez(work / 'parameters.npz', **params)
        receipt = dict(schema='cpu-first-fit-receipt-v1', status='COMPLETE', job_id=spec['job_id'], attempt_id=spec['attempt_id'],
                       parameter_sha256=parameter_hash, producer_build=c['executor_build']['sha256'], counts=counts, record=record,
                       uninterruptible_sections=sections, cuda_initialized=torch.cuda.is_initialized(),
                       scheduler_construct=1, scheduler_initial_step=1, scheduler_training_step=counts['updates'],
                       Adam_instances=counts['optimizer_construct'], parameter_extractions=1, parameter_members=len(params))
        need(not receipt['cuda_initialized'], 'FIRST_FIT_CUDA_INITIALIZED')
        atomic_json(work / 'fit-receipt.json', receipt)
        atomic_json(work / 'actual-counts.json', receipt)
        return receipt
    except Cancelled as error:
        atomic_json(work / 'actual-counts.json', dict(status='CANCELLED', counts=error.counts, phase=error.phase,
                    scheduler_construct=error.counts['optimizer_construct'],scheduler_initial_step=error.counts['optimizer_construct'],scheduler_training_step=error.counts['updates'],
                    uninterruptible_sections=error.sections, terminal_forward=0, next_sample_started=False))
        raise
    finally:
        del model
        if sm: sm.close()
        if rm: rm.close()
        gc.collect()


def render(payload):
    import numpy as np
    import torch
    from ..engine.independent_r0 import core, adapter as a
    from ..engine.cpu_r0_fit.optimizer_policy import configure_cpu
    from ..engine.cpu_r0_image.renderer import transfer
    from ..cpu_recipe.generated import complete
    root=Path(payload['work'])
    spec=validate_payload(payload,'r0')
    c=spec['execution']
    preload_runtime(False)
    from ..cpu_replay.runtime import require_runtime
    require_runtime(root)
    from ..preview.startup_fixture import active as startup_chain, enabled as startup_fixture_enabled, model_barrier
    if startup_fixture_enabled():
        need(startup_chain(), "GM080_FIXTURE_REQUIRES_FROZEN_PLAN")
        need(np.__version__ == "1.26.4" and torch.__version__ == _torch_build(c) and
             not torch.cuda.is_initialized(), "R0_RUNTIME")
        return model_barrier(root, spec, payload)
    if c.get("light_plan") and c["operation"]=="fine-replay":
        from ..cpu_recipe.generated_fine import complete as complete_fine
        bundle,_=complete_fine(payload['bundle'])
    else:
        bundle,_=complete(payload['bundle'])
    authorization=strict_load(root.parent/'render-authorization.json')
    from ..cpu_replay.contract import verify
    verify(authorization['bundle_complete'])
    need(authorization['parent_context']==spec['semantics']['instance_context'] and
         authorization['bundle_id']==bundle['bundle_id'] and authorization['math_id']==bundle['math_id'] and
         authorization['parameter_sha256']==bundle['mathematical']['r0_parameters']['value_sha256'] and
         authorization['executor_build']==c['executor_build']['sha256'],'FIRST_FIT_RENDER_AUTHORIZATION')
    need(np.__version__ == '1.26.4' and torch.__version__ == _torch_build(c) and not torch.cuda.is_initialized(), 'R0_RUNTIME')
    sm,source=attach(payload['S']); om,out=attach(payload['O'])
    model=None
    counts=dict(render=0,model_construct=0,state_load=0,forward_batches=0,completed_tiles=0)
    cancelled=lambda:Path(payload['cancel']).exists() or (root.parent/'stop.signal').exists()
    try:
        need(not cancelled(),'CANCELLED',exit_code=8)
        with np.load(Path(payload['bundle'])/'r0/parameters.npz',allow_pickle=False) as z:
            parameters={key:z[key].copy() for key in z.files}
        need(a._parameter_hash(parameters)==bundle['mathematical']['r0_parameters']['value_sha256'],'R0_PARAMETER_IDENTITY')
        from ..preview.contract import enabled as preview_enabled
        dtype = np.dtype(c['source_identity']['dtype']) if preview_enabled(c) else (np.float32 if c['TEST_ONLY'] else np.uint8)
        need(source.dtype == dtype and not source.flags.writeable and
             source.shape==out.shape and out.dtype==np.float32 and out.flags.writeable and not np.shares_memory(source,out),'R0_SHARED_BUFFERS')
        configure_cpu()
        cfg={key:value for key,value in bundle['mathematical']['settings'].items() if key not in ('seed','long_side','tile_side')}
        started=time.monotonic(); counts['render']=1
        atomic_json(root/'before-model.json',dict(reservation=payload['reservation'],counts=counts))
        # Same original Field/load_state_dict/transfer statements as renderer.
        model=core.Field(cfg,43).to(device='cpu',dtype=torch.float32,memory_format=torch.channels_last)
        counts['model_construct']=1
        model.load_state_dict({key:torch.from_numpy(value.copy()) for key,value in parameters.items()},strict=True)
        counts['state_load']=1
        loaded=a._parameter_hash({key:value.detach().numpy() for key,value in model.state_dict().items()})
        need(loaded==bundle['mathematical']['r0_parameters']['value_sha256'] and all(p.device.type=='cpu' for p in model.parameters()),'R0_LOADED_STATE')
        total=math.ceil(source.shape[0]/512)*math.ceil(source.shape[1]/512)
        def progress(value):
            if c.get("light_plan"):
                value=dict(value,gm066_monotonic=time.monotonic())
            atomic_json(Path(payload['operation'])/'progress.json',value)
        tiles=transfer(core.render_tiles(model,source,512),out,total,counts,started,cancelled,progress)
        need(not cancelled() and not torch.cuda.is_initialized(),'R0_CANCEL_OR_CUDA')
        result=dict(status='COMPLETE',counts=counts,tiles=tiles,loaded_parameter_sha256=loaded,
                    O_sha256=hashlib.sha256(memoryview(out).cast('B')).hexdigest(),caller_owned=True,
                    private_full_result_allocations=0,source_readonly=True,seconds=time.monotonic()-started,cuda_initialized=False)
        atomic_json(root/'r0-result.json',result)
        return result
    finally:
        atomic_json(root/'actual-counts.json',counts)
        del model,source,out
        sm.close();om.close()
