"""Fine preparation is a thin explicit intent over the existing lifecycle."""
import os
from pathlib import Path
from ..cpu_replay.contract import BACKEND, need, digest, binding, cpu_error_boundary
from .contract import load, destination, validate_source, E


def _start(directory,source,reference,store,output,key,manifest,c,*,preparing):
    from ..cpu_recipe.validation import complete
    from ..cpu_replay import runtime
    from ..cpu_replay.api import _start as resident_start
    from ..cpu_first_fit.contract import active
    validate_source(source,c);destination(store,c);destination(output,c)
    m,_=complete(directory)
    need((m['mode']=='fast' and m['version'] in (2,3)) if preparing else
         (m['mode']=='fine' and m['version']==3),'REFINE_INPUT_BUNDLE_VERSION')
    need(m['provenance']['TEST_ONLY']==c['TEST_ONLY'],'REFINE_TEST_ONLY_PROVENANCE')
    expected=c['parent_bundle'] if preparing else c['bundle']
    need(Path(directory).resolve()==Path(expected['directory']).resolve() and
         (m['bundle_id'],m['math_id'])==(expected['bundle_id'],expected['math_id']), 'REFINE_BUNDLE_BINDING')
    need(m['mathematical']['inputs']['source_identity']==c['source_identity'] and
         m['mathematical']['inputs']['reference_identity']==c['reference_identity'],'REFINE_INPUT_IDENTITY')
    if preparing:
        got=binding(reference)
        need((got['bytes'],got['sha256'])==(c['reference']['bytes'],c['reference']['sha256']),'REFINE_REFERENCE_CHANGED')
        destination(c['bundle'],c)
    sem=dict(backend_id=BACKEND,mode='fine',cpu_refine=True,refine_prepare=preparing,
        instance_context=binding(manifest),executor_build=c['executor_build']['sha256'],
        source_path=str(Path(source).resolve()),source_sha256=c['source']['sha256'],
        reference_path=str(Path(reference).resolve()) if preparing else None,
        reference_sha256=c['reference']['sha256'] if preparing else None,
        output_root=str(Path(output).resolve()),parent_bundle=str(Path(directory).resolve()) if preparing else None,
        bundle_directory=str(Path(c['bundle'] if preparing else directory).resolve()),
        bundle_id=digest(dict(parent=m['bundle_id'],intent='refine')) if preparing else m['bundle_id'],
        math_id=digest(dict(parent=m['math_id'],intent='P3')) if preparing else m['math_id'],
        TEST_ONLY=c['TEST_ONLY'],core_rows=256)
    with active(manifest):
        work=E/'clients'/c['id']/str(os.getpid());runtime.enter('refine_client',work)
        try: result=resident_start(store,sem,key,c)
        except BaseException:
            runtime.observation(work,'refine_client','FAILED');raise
        runtime.observation(work,'refine_client','PASS');return result


@cpu_error_boundary
def refine_cpu_recipe(recipe,source,reference,store_root,output_root,*,bundle_directory,request_key,
                      execution_backend=None,allow_experimental=False,execution_manifest=None):
    need(execution_backend==BACKEND and allow_experimental is True and execution_manifest is not None,
         'EXPLICIT_CPU_REFINE_REQUIRED',exit_code=2)
    c=load(execution_manifest)
    need(c['phase']=='refine' and not c['TEST_ONLY'] and Path(bundle_directory).resolve()==Path(c['bundle']).resolve(),
         'PUBLIC_REFINE_CONTEXT',exit_code=2)
    return _start(recipe,source,reference,store_root,output_root,request_key,execution_manifest,c,preparing=True)


@cpu_error_boundary
def replay_fine(directory,source,store_root,output_root,*,request_key,execution_backend=None,
                allow_experimental=False,execution_manifest=None,cpu_core_rows=None):
    need(execution_backend==BACKEND and allow_experimental is True and execution_manifest is not None and
         cpu_core_rows in (None,256),'EXPLICIT_CPU_FINE_REPLAY_REQUIRED',exit_code=2)
    c=load(execution_manifest);need(c['phase']=='render' and not c['TEST_ONLY'],'PUBLIC_FINE_REPLAY_CONTEXT')
    return _start(directory,source,None,store_root,output_root,request_key,execution_manifest,c,preparing=False)
