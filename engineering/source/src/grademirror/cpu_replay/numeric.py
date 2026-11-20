"""Thin portable adapters; original Field/render and CPU service do the math."""
import hashlib
import time
from pathlib import Path
from ..common import atomic_json, strict_load
from .contract import need, authority


def attach(desc):
    import numpy as np
    from multiprocessing import shared_memory
    shm = shared_memory.SharedMemory(name=desc['name'], create=False)
    a = np.ndarray(desc['shape'], dtype=desc['dtype'], buffer=shm.buf)
    if desc.get('readonly'):
        a.flags.writeable = False
    return shm, a


def r0(payload):
    import numpy as np
    import torch
    from . import runtime
    from ..engine.independent_r0 import core, adapter as a
    from ..engine.cpu_r0_fit.optimizer_policy import configure_cpu
    from ..engine.cpu_r0_image.renderer import transfer
    root = Path(payload['work'])
    bundle = strict_load(Path(payload['bundle']) / 'manifest.json')
    need(np.__version__ == '1.26.4' and torch.__version__ == '2.5.1+cu124' and not torch.cuda.is_initialized(), 'R0_RUNTIME')
    sm, source = attach(payload['S'])
    om, out = attach(payload['O'])
    model = None
    counts = dict(render=0, model_construct=0, state_load=0, forward_batches=0, completed_tiles=0)
    cancel = lambda: Path(payload['cancel']).exists()
    try:
        need(not cancel(), 'CANCELLED', exit_code=8)
        with np.load(Path(payload['bundle']) / 'r0/parameters.npz', allow_pickle=False) as z:
            parameters = {key: z[key].copy() for key in z.files}
        need(a._parameter_hash(parameters) == bundle['mathematical']['r0_parameters']['value_sha256'], 'R0_PARAMETER_IDENTITY')
        need(source.dtype == np.uint8 and not source.flags.writeable and source.shape == out.shape and
             out.dtype == np.float32 and out.flags.writeable and not np.shares_memory(source, out), 'R0_SHARED_BUFFERS')
        configure_cpu()
        cfg = {key: value for key, value in bundle['mathematical']['settings'].items()
               if key not in ('seed', 'long_side', 'tile_side')}
        started = time.monotonic()
        counts['render'] = 1
        atomic_json(root / 'before-model.json', dict(reservation=payload['reservation'], counts=counts))
        # Original renderer.render calculation statements, in original order.
        model = core.Field(cfg, 43).to(device='cpu', dtype=torch.float32, memory_format=torch.channels_last)
        counts['model_construct'] = 1
        model.load_state_dict({key: torch.from_numpy(value.copy()) for key, value in parameters.items()}, strict=True)
        counts['state_load'] = 1
        loaded = a._parameter_hash({key: value.detach().numpy() for key, value in model.state_dict().items()})
        need(loaded == bundle['mathematical']['r0_parameters']['value_sha256'] and
             all(p.device.type == 'cpu' for p in model.parameters()), 'R0_LOADED_STATE')
        def progress(value):
            atomic_json(Path(payload['operation']) / 'progress.json', value)
        from ..adaptive_execution.calibration_contract import expected_tiles
        from ..cpu_first_fit.contract import selected
        total=expected_tiles(selected(),140)
        tiles = transfer(core.render_tiles(model, source, 512), out, total, counts, started, cancel, progress)
        need(not cancel() and not torch.cuda.is_initialized(), 'R0_CANCEL_OR_CUDA')
        result = dict(status='COMPLETE', counts=counts, tiles=tiles, loaded_parameter_sha256=loaded,
                      O_sha256=hashlib.sha256(memoryview(out).cast('B')).hexdigest(),
                      caller_owned=True, private_full_result_allocations=0, source_readonly=True,
                      seconds=time.monotonic()-started, cuda_initialized=False)
        atomic_json(root / 'r0-result.json', result)
        return result
    finally:
        atomic_json(root / 'actual-counts.json', counts)
        del model, source, out
        sm.close()
        om.close()


def cpu(payload_path):
    from ..engine.cpu_postprocess.service import worker
    # Original strict service consumes exactly its original payload shape.
    return worker(payload_path)


def frame_sender(payload):
    """TEST_ONLY framing actor, no Field, P3 or LLF call is reachable."""
    import numpy as np
    from ..gm003_ipc import connect, send_json, recv_json, send_array
    from ..winproc import current_identity
    from ..engine.cpu_postprocess.transport import FIELDS
    need(payload.get('TEST_ONLY') is True, 'TEST_FRAME_REQUIRED')
    m = payload['manifest']
    conn = connect(payload['address'], payload['auth_hex'])
    try:
        send_json(conn, dict(event='ready', identity=current_identity(), cuda_initialized=False))
        hashes = {key: hashlib.sha256() for key in FIELDS}
        h, w, _ = m['O']['shape']
        for start in range(0, h, m['core_rows']):
            end = min(h, start+m['core_rows'])
            base = np.arange(start*w*3, end*w*3, dtype=np.float32).reshape(end-start, w, 3)/np.float32(8192)
            blocks = {key: np.ascontiguousarray(base + np.float32(index/16)) for index, key in enumerate(FIELDS)}
            current = {key: hashlib.sha256(memoryview(value).cast('B')).hexdigest() for key, value in blocks.items()}
            send_json(conn, dict(event='block', job_id=m['job_id'], attempt_id=m['attempt_id'], start=start, end=end, hashes=current))
            for key, value in blocks.items():
                send_array(conn, value)
                hashes[key].update(memoryview(value).cast('B'))
                if payload.get('partial') and key == 'LO':
                    return dict(partial=True)
            reply = recv_json(conn)
            need(reply == dict(accepted=True, job_id=m['job_id'], attempt_id=m['attempt_id'], start=start, end=end, hashes=current), 'TEST_FRAME_ACK')
        result = dict(event='complete', hashes={k: v.hexdigest() for k, v in hashes.items()}, identity=current_identity(), cuda_initialized=False)
        send_json(conn, result)
        return result
    finally:
        conn.close()
