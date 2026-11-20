"""Default-off GM063 launch declarations and read-only worker observations.
The original R7, jobs, numerical calls and resource admissions are unchanged.
"""
import os,sys,time,uuid,atexit,json
from pathlib import Path
from ..common import atomic_json,strict_load,sha_file
from ..cpu_replay.contract import ROOT,binding,need
from .calibration_contract import E,W,TASK_SHA


def config():
    value=os.environ.get('GM063_LAUNCH_CONFIG')
    if not value:return None
    p=Path(value).resolve();need(p.parent==E/'launch-configs','GM063_LAUNCH_POINTER')
    need(sha_file(p)==os.environ.get('GM063_LAUNCH_SHA256'),'GM063_LAUNCH_SHA')
    c=strict_load(p);need(c['task']=='GM-063' and c['task_sha256']==TASK_SHA and c['input_scope']=='fixed24' and c['backend']in('G','H','P0'),'GM063_LAUNCH_SCOPE')
    need(Path(c['workspace']).resolve().is_relative_to(W),'GM063_LAUNCH_WORKSPACE')
    return c


def declare(argv,cwd,*,role='existing_child'):
    c=config()
    if c is None:return
    from ..winproc import current_identity
    from ..cpu_replay.runtime import approve_plan
    argv=list(map(str,argv));exe=Path(argv[0]).resolve();known=[Path(x['path']).resolve()for x in c['interpreters'].values()]
    need(exe in known or exe==Path(c['nvidia_smi']['path']).resolve(),'GM063_LAUNCH_EXECUTABLE')
    observer=Path(os.environ['GM052_OBSERVER_ROOT']).resolve();need(observer.is_relative_to(E/'runs'),'GM063_LAUNCH_OBSERVER')
    p=observer/'plans'/(uuid.uuid4().hex+'.json')
    atomic_json(p,dict(argv=argv,cwd=str(cwd),role=role,parent=current_identity(),before_CreateProcess=True,allocation='GM063 complete graph prepaid'))
    control=c['interpreters']['control']
    if exe in known and exe!=Path(control['path']).resolve() and 'Scripts' in exe.parts:
        bp=p.with_name(p.stem+'-base.json');atomic_json(bp,dict(argv=[control['path'],*argv[1:]],role=role+'-base',cwd=str(cwd),expected_parent_executable=str(exe),before_CreateProcess=True,allocation='same prepaid venv/base/conhost graph'));approve_plan(bp)
    approve_plan(p)


def nvidia_smi():
    c=config()
    if c is None:return 'nvidia-smi'
    b=c['nvidia_smi'];need(binding(b['path'])==b,'GM063_SMI_CHANGED');return b['path']


def job_namespace():
    c=config()
    return None if c is None else Path(c['workspace'])/'jobs'


def enter(role,work):
    c=config()
    if c is None:return
    from ..winproc import current_identity
    from .calibration_observation import stage
    work=Path(work).resolve();need(work.is_relative_to(W) or c['unit'].startswith('P0-') and work.is_relative_to(E/'runs'),'GM063_ENTRY_WORK')
    observer=Path(os.environ['GM052_OBSERVER_ROOT']);entry=observer/'entries'/uuid.uuid4().hex
    atomic_json(entry/'entry.json',dict(identity=current_identity(),role=role,work=str(work),entry_only=False))
    deadline=time.monotonic()+30
    while not(entry/'ack.json').is_file():need(time.monotonic()<deadline,'GM063_ACTUAL_HANDLE_ACK');time.sleep(.01)
    inventory=strict_load(Path(c['runtime']['path']))['files'];known={os.path.normcase(str(Path(b['path']).resolve())):b['sha256']for b in inventory+c['product_files']}
    reads=set();writes=set();verified=set();platform_refusals=[];started=time.monotonic();math_seen=set();overhead=[0.]
    def audit(ev,args):
        if ev=='subprocess.Popen' and args and Path(str(args[0])).name.lower() in ('cmd.exe','command.com'):
            frame=sys._getframe(1);platform_probe=False
            while frame:
                if frame.f_code.co_name=='_syscmd_ver' and frame.f_code.co_filename.replace('\\','/').endswith('/platform.py'):platform_probe=True;break
                frame=frame.f_back
            if platform_probe:
                platform_refusals.append(dict(executable=str(args[0]),argv=str(args[1]),before_CreateProcess=True))
                raise PermissionError(13,'GM063 platform shell probe refused; original native fallback remains available',str(args[0]))
            need(False,'GM063_UNDECLARED_COMMAND_PROCESS')
        if ev in('open','os.mkdir','os.remove','os.rmdir','os.rename')and args:
            for target in(args[:2]if ev=='os.rename'else args[:1]):
                if not isinstance(target,(str,bytes,os.PathLike)):continue
                if os.fsdecode(target).casefold()=='nul':continue
                p=Path(os.fsdecode(target)).absolute().resolve()
                write=ev!='open'
                if ev=='open':
                    mode=args[1];flags=args[2];write=isinstance(mode,str)and any(x in mode for x in'wax+')or isinstance(flags,int)and bool(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
                if write:need(p.is_relative_to(E)or p.is_relative_to(W),'GM063_WRITE_SCOPE',str(p));writes.add(str(p))
                else:
                    if not p.exists():raise FileNotFoundError(2,'GM063 missing optional import probe',str(p))
                    if p.suffix=='.pyc' and os.path.normcase(str(p))not in known:raise FileNotFoundError(2,'GM063 unfrozen cache unavailable; bound source required',str(p))
                    key=os.path.normcase(str(p));allowed=key in known or p.is_relative_to(E)or p.is_relative_to(W)or p==Path(c['reference']['path']).resolve()or p.is_relative_to(ROOT/'vendor')
                    need(allowed,'GM063_READ_SCOPE',str(p));reads.add(str(p))
        if ev in('os.system','os.spawn','os.posix_spawn'):raise RuntimeError('GM063_UNDECLARED_PROCESS')
    def profile(frame,event,arg):
        if event!='call':return
        f=frame.f_code.co_filename.replace('\\','/');name=frame.f_code.co_name
        selected=('/independent_r0/core.py'in f and name in('__init__','render_tiles'))or('/independent_r0_p3_lld10/'in f and name in('fit','render'))or('/cpu_postprocess/'in f and name=='execute')
        key=f+':'+name
        if not selected or key in math_seen:return
        math_seen.add(key);t=time.monotonic()
        from ..cpu_replay.runtime import loaded_binaries
        actual=loaded_binaries()
        for module in list(sys.modules.values()):
            p=getattr(module,'__file__',None)
            if p and Path(p).is_file()and Path(p).suffix.lower()in('.py','.pyd','.dll'):actual.append(binding(p))
        diff=[b for b in actual if known.get(os.path.normcase(b['path']))!=b['sha256']]
        atomic_json(work/('runtime-before-'+str(len(math_seen))+'.json'),dict(actual=actual,differences=diff,role=role))
        need(not diff,'GM063_RUNTIME_DRIFT_BEFORE_SCIENCE')
        stage(work,'before_'+name,role=role);overhead[0]+=time.monotonic()-t
    sys.addaudithook(audit);sys.setprofile(profile)
    stage(work,'process_entry',role=role)
    def final():
        sys.setprofile(None)
        stage(work,'process_exit',role=role)
        atomic_json(work/'gm063-observation.json',dict(role=role,elapsed=time.monotonic()-started,readonly_observation_seconds=overhead[0],math_boundaries=sorted(math_seen),platform_shell_refusals=platform_refusals,reads=sorted(reads),writes=sorted(writes)))
    atexit.register(final)


def primary_runtime():
    c=config();need(c is not None and c['backend']=='H','GM063_PRIMARY_RUNTIME_SCOPE')
    selected=c['interpreters']['control'];actual=binding(selected['path'])
    need(actual['bytes']==selected['bytes']==107312 and actual['sha256']==selected['sha256']=='f8b45d63047da9d9adf09892e4cc4317b859e86a93a478cc9616bc69d3b176e5','GM063_ACTUAL_PRIMARY_RUNTIME_DRIFT')
    fixed=strict_load(E/'fixed-inputs.json');need(c['source']==fixed['source'] and c['reference']==fixed['reference'],'GM063_PRIMARY_EXACT_INPUT_SCOPE')
    return selected
