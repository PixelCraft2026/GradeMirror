"""Frozen TEST_ONLY guard/diagnostic controls, never a public submit operation."""
from pathlib import Path
from ..common import strict_load,atomic_json
from .intent import need

def run(c,args):
    need(c['TEST_ONLY'] is True and max(c['source_identity']['shape'][:2])<=64,'APPLICATION_PROBE_FACTORY')
    out=Path(c['output_root']);proofs=[]
    def rejected(name,fn):
        try:fn()
        except (PermissionError,Exception) as e:
            proofs.append(dict(name=name,error=str(e),code=getattr(e,'code',None)));return
        raise ValueError('APPLICATION_GUARD_PROBE_ACCEPTED:'+name)
    if args['kind']=='domains':
        from ..cpu_replay.contract import ROOT
        rejected('foreign_write',lambda:(ROOT/'docs/evidence/gm-055/20261031-v1/forbidden-write.txt').write_text('forbidden'))
        rejected('old_data_read',lambda:(ROOT/'docs/evidence/gm-053/20261030-v1/authorization.json').read_bytes())
        rejected('unknown_device',lambda:__import__('pynvml'))
        return dict(status='GUARD_PROBE_PASS',proofs=proofs,science=0)
    if args['kind']=='diagnostic':
        from ..cpu_replay import runtime
        target=out/'diagnostic-refusal';target.mkdir();(target/'error.json').mkdir()
        runtime.error(target,OSError('GM055_PRIMARY_DIAGNOSTIC'))
        return dict(status='DIAGNOSTIC_PROBE_PASS',primary='GM055_PRIMARY_DIAGNOSTIC',science=0)
    if args['kind']=='refine_recipe':
        need(c['request']['operation']=='refine' and c['mode']=='fine','REFINE_PROBE_CONTEXT')
        from .recipes import prepare
        from . import runtime
        from ..cpu_replay.contract import binding
        checkpoints=[]
        # Transaction metadata only: this context ID is not an accepted job or a wait receipt.
        directory,m=prepare(c,dict(job_id=c['id'],attempt_id=c['id'],metadata_probe=True),checkpoint=checkpoints.append)
        q=strict_load(Path(c['qualification']['path']));factory_parameter=Path(q['parameter_file']).resolve()
        need(str(factory_parameter) not in runtime._guard.reads,'REFINE_READ_FACTORY_PARAMETERS')
        rejected('unused_factory_parameter',factory_parameter.read_bytes)
        need(proofs[-1]['error']=='APPLICATION_READ_SCOPE:'+str(factory_parameter),'REFINE_UNUSED_INPUT_WRONG_DENIAL')
        need(m['version']==3 and 'bundle_after_complete' in checkpoints,'REFINE_METADATA_NOT_COMMITTED')
        import sys
        need(not any(name in sys.modules for name in ('numpy','torch','PySide6','PIL','pynvml')),'REFINE_PROBE_ARRAY_OR_SCIENCE_IMPORT')
        return dict(status='REFINE_METADATA_GUARD_PASS',bundle_complete=binding(directory/'COMPLETE.json'),
            parent=c['request']['recipe'],checkpoints=checkpoints,negative=proofs,
            unused_factory_parameter_read=False,operation_record_created=False,
            metadata_transaction_only=True,array_library_loaded=False,science=0)
    if args['kind']=='bounded_wait':
        import time
        atomic_json(out/'timeout-probe-start.json',dict(TEST_ONLY=True,metadata_wait_only=True,seconds=50))
        time.sleep(50)
        return dict(status='WAIT_FINISHED',science=0)
    raise ValueError('APPLICATION_PROBE_KIND')
