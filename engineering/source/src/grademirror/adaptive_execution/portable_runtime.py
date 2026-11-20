"""Package-only independent roles using original guard and actual R7 receipts."""
import os,sys
from pathlib import Path
from ..cpu_replay.contract import ROOT,need
from .first_runtime import Guard as FirstGuard
from . import portable_contract as k

def environment(role,work,*,context=None):
    need(role in ('control','numeric','solver','io','desktop'),'PORTABLE_ROLE')
    system=Path(os.environ.get('SystemRoot','C:/Windows'));rt=ROOT/'.runtime/gm059-v1'
    env={key:os.environ[key] for key in ('SystemRoot','WINDIR','COMSPEC','USERPROFILE','APPDATA','LOCALAPPDATA','SystemDrive') if key in os.environ}
    env.update(PATH=os.pathsep.join(map(str,(rt/role,rt/'base',system/'System32',system))),
        PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',CUDA_VISIBLE_DEVICES='-1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',NUMEXPR_NUM_THREADS='4',TEMP=str(work),TMP=str(work))
    if os.environ.get('GM052_OBSERVER_ROOT'):env['GM052_OBSERVER_ROOT']=os.environ['GM052_OBSERVER_ROOT']
    if context:env['GM055_CONTEXT']=context['manifest_path']
    return env

class Guard(FirstGuard):
    def __init__(self,c,role,dry):
        root=k.e(c);super().__init__(c,role,dry,namespace=(root,k.workspace(root),ROOT/'validation'))
        unit=Path(c['workspace']).name
        if unit in ('T1','T2','T3','T4','S1','S2','S3','S4','C4'):
            self.roots.extend(k.sidecar_base(root,unit,name) for name in ('x','r'))
        q=k.read(c['qualification']['path'])
        self.exact.update({root/'session.json',root/'fixed-inputs.json',root/'science-budget.jsonl',
            ROOT/'validation/capability.json',ROOT/'validation/resource-model.json',ROOT/'validation/fixed-template.json',ROOT/'portable-manifest.json'})
        from .t34r3_c1_route import QUALIFICATIONS,RESUME_QUALIFICATIONS,KEY,route_prefix,selected
        name=Path(q['manifest_path']).name
        if name in QUALIFICATIONS:
            check=root/'runs'/f'{route_prefix(name).upper()}-Check'/'managed'
            self.exact.update({root/'qualifications/T4.json',root/'runtime-inventory.json',
                               root/'runs/T4/managed/wait.json',root/'w/T4/.adaptive/context-index.json'})
            self.exact.update(check/leaf for leaf in ('entry.json','result.json','launch.json','wait.json'))
        if name in RESUME_QUALIFICATIONS:
            route=selected(q,Path(c['workspace']),KEY)
            self.exact.update(Path(route.a[key]['path']) for key in ('old_request','old_selection','old_context'))
            self.exact.update(Path(row['path']) for row in route.a['resume']['files'].values())
            from .t34r3_c2_history import CONTEXT
            self.exact.add(root/CONTEXT)
            if route.prefix == 'c3':
                self.exact.add(root/'w/T4/pool/admission.lock')
        from . import c2_fine_replay
        if name in c2_fine_replay.QUALIFICATIONS:
            self.exact.update(Path(row['path']) for row in c2_fine_replay.historical_paths(root))
        if q['parent_package']:self.exact.update(Path(x['path']).resolve() for x in q['parent_package']['files'])
        need(sys.flags.isolated==1 and not os.environ.get('PYTHONPATH') and not os.environ.get('PYTHONHOME'),'PORTABLE_ISOLATED_CONFIGURATION')
        for value in sys.path:
            p=Path(value).resolve();need(any(p.is_relative_to(base) for base in (ROOT/'.runtime',ROOT/'src',ROOT/'tools')),'PORTABLE_SYS_PATH_OUTSIDE:'+str(p))
    def audit(self,event,args):
        if event=='subprocess.Popen' and self.role=='host':
            from ..export_metadata import helper_argv_allowed
            if helper_argv_allowed(args[0],args[1]):return
        if event in ('open','os.rename','os.mkdir','os.remove','os.rmdir'):
            targets=args[:2] if event=='os.rename' else args[:1]
            for item in targets:
                if not isinstance(item,(str,bytes,os.PathLike)):continue
                if os.fsdecode(item).casefold()=='nul':continue
                p=Path(os.fsdecode(item)).absolute()
                need(not p.is_relative_to(ROOT/'validation/goldens'),'PORTABLE_GOLDENS_FORBIDDEN_TO_CALCULATOR')
                if event!='open':k.ancestors(p)
                if event=='open':
                    mode=args[1];flags=args[2] if len(args)>2 else 0
                    write=(isinstance(mode,str) and any(x in mode for x in 'wax+')) or bool(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
                    if write:k.ancestors(p);need(not p.is_relative_to(ROOT),'PORTABLE_PACKAGE_READ_ONLY')
        return super().audit(event,args)
    def profile(self,frame,event,arg):
        if event=='call':
            filename=frame.f_code.co_filename.replace('\\','/');name=frame.f_code.co_name
            need(not('/torch/cuda/' in filename and name in ('_lazy_init','is_available','device_count','current_device')),'PORTABLE_GPU_QUERY_OR_CONTEXT_FORBIDDEN')
        if event=='call' and self.c['request']['operation']=='replay':
            f=frame.f_code.co_filename.replace('\\','/');name=frame.f_code.co_name
            need(not(f.endswith('/group15_codec.py') and name in ('read_reference','inspect_reference')),'PORTABLE_REPLAY_REFERENCE_FORBIDDEN')
        return super().profile(frame,event,arg)
