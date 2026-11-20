"""Actual import and DLL closure checked against the exact GM054 inventory."""
import os
import sys
from pathlib import Path
from ..common import atomic_json,strict_load
from ..cpu_replay.contract import ROOT,need,binding,verify


def require_runtime(work,c):
    from ..cpu_replay.runtime import loaded_binaries
    verify(c['runtime']['inventory']);inventory=strict_load(Path(c['runtime']['inventory']['path']))
    known={os.path.normcase(str(Path(row['path']).resolve())):row['sha256'] for row in inventory['files']}
    product={row['path']:row['sha256'] for row in c['executor_build']['files']}
    rows=loaded_binaries()
    for name,module in sorted(sys.modules.items()):
        path=getattr(module,'__file__',None)
        if path and Path(path).is_file() and Path(path).suffix.lower() in ('.py','.pyd','.dll'):
            rows.append(dict(module=name,**binding(path)))
    differences=[]
    for row in rows:
        p=Path(row['path']).resolve()
        expected=product.get(p.relative_to(ROOT).as_posix()) if p.is_relative_to(ROOT/'src') or p.is_relative_to(ROOT/'tools') else known.get(os.path.normcase(str(p)))
        if row['sha256']!=expected: differences.append(dict(actual=row,expected=expected))
    atomic_json(Path(work)/'before-math-runtime.json',dict(actual=rows,differences=differences))
    need(not differences,'REFINE_RUNTIME_IMPORT_OR_DLL_CHANGED')


def zero_science_guard():
    def profile(frame,event,arg):
        if event!='call':return
        path=frame.f_code.co_filename.replace('\\','/');name=frame.f_code.co_name
        forbidden=(('/independent_r0/core.py',('__init__','forward','render_tiles')),
            ('/cpu_r0_image/executor.py',('_fit_loop',)),
            ('/independent_r0_p3_lld10/area.py',('area_prefix',)),
            ('/independent_r0_p3_lld10/p3_solver.py',('geometry','solve')),
            ('/cpu_postprocess/executor.py',('execute',)),('/cpu_postprocess/core.py',('p3_region','scalar_footprint','compose')),
            ('/cpu_postprocess/service.py',('worker',)),('/group15_codec.py',('read_source','read_reference','write_jpg')))
        if any(path.endswith(suffix) and name in names for suffix,names in forbidden):
            raise RuntimeError('GM054_ZERO_SCIENCE_GUARD:'+path+':'+name)
    sys.setprofile(profile)
