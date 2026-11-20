"""v4 role guard layered on the accepted runtime audit primitive."""
import os,sys,time,uuid
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import ROOT,binding,verify,need
from .first_contract import E,W,I,read,authorize
from .native_runtime import Guard as OriginalGuard,BoundedStderr,capture as original_capture
_guard=None
class Guard(OriginalGuard):
 def __init__(self,c,role,dry,*,namespace=None):
  scope_e,scope_w,scope_i=namespace if namespace is not None else (E,W,I)
  if namespace is not None:self.e=scope_e;self.w=scope_w;self.independent=False
  self.c=c;self.role=role.removeprefix('entry_');self.dry=dry;self.reads=set();self.writes=set();self.denied=[]
  self.roots=[Path(c[k]).resolve() for k in ('output_root','evidence_root','pool_root')]+[Path(c['workspace'])/x for x in ('.adaptive','store','exports')]+[scope_e/'science']
  ob=os.environ.get('GM052_OBSERVER_ROOT');need(ob and Path(ob).resolve().is_relative_to(scope_e/'runs'),'FIRST_OBSERVER_SCOPE');self.observer=Path(ob).resolve();self.roots.append(self.observer)
  self.inputs=[Path(c[k]['path']).resolve() for k in ('source','reference') if c[k]]+[Path(c['bundle']).resolve()]
  if c['parent_bundle']:self.inputs.append(Path(c['parent_bundle']['directory']).resolve())
  inv=read(c['runtime']['inventory']['path'])['files'];self.known={os.path.normcase(os.path.abspath(x['path'])):x['sha256'] for x in inv};self.known.update({os.path.normcase(str((ROOT/x['path']).resolve())):x['sha256'] for x in c['executor_build']['files']})
  self.exact={Path(x['path']).absolute() for x in inv}|{(ROOT/x['path']).resolve() for x in c['executor_build']['files']}
  self.exact.update({scope_i/x for x in ('interpreters.json','fixed-inputs.json','capacity.json','authorization.json','runtime-frozen.json')}|{scope_e/'qualification-index.json',scope_e/'allocations.json',Path(c['qualification']['path']).resolve(),Path(c['runtime']['inventory']['path']).resolve()})
  q=read(c['qualification']['path']);self.exact.update(Path(q[k]['path']).resolve() for k in ('authorization','taskbook'));self.exact.update(Path(b['path']).resolve() for b in c['interpreters'].values())
  self.exact.update({E/'completion-r7/science-addendum.json',E/'completion-r7/human-authority.json',E/'completion-r5/science-addendum.json',E/'completion-r5/human-authority.json',E/'completion-r6/science-addendum.json',E/'completion-r6/human-authority.json'})
  if q['p0'] is not None:self.exact.add(Path(q['p0']['path']).resolve())
  if q['parent_package'] is not None:self.exact.update(Path(x['path']).resolve() for x in q['parent_package'].get('producer',{}).values())
  if q['schema']=='adaptive-first-actual-b-recovery-v1':
   self.exact.add(Path(q['partial_b']['path']).resolve());cap=read(q['partial_b']['path']);self.exact.update(Path(cap[k]['path']).resolve() for k in cap if k not in ('schema','task'))
  if q['schema']=='adaptive-first-recovery-qualification-v1':
   self.exact.add(Path(q['recovery']['path']).resolve());saved=read(q['recovery']['path']);self.exact.update(Path(saved[k]['path']).resolve() for k in ('original_root_wait','original_error','result','reclaim'))
 def audit(self,event,args):
  if event=='subprocess.Popen' and args and Path(str(args[0])).name.lower() in ('cmd.exe','command.com'):
   frame=sys._getframe(1);platform_probe=False
   while frame:
    if frame.f_code.co_name=='_syscmd_ver' and frame.f_code.co_filename.replace('\\','/').endswith('/platform.py'):platform_probe=True;break
    frame=frame.f_back
   if platform_probe:
    self.denied.append('BEFORE_CREATEPROCESS_PLATFORM_PROBE:'+repr(args));raise PermissionError(13,'GM057 platform command refused; original native fallback remains available',str(args[0]))
  if event=='import' and args:
   mod=args[0].split('.')[0]
   if mod=='scipy':need(self.role=='solver','FIRST_SCIPY_ROLE');return
   if mod=='torch' and self.role in ('fit','prepare'):return
  if event in ('open','os.rename','os.mkdir','os.remove','os.rmdir'):
   for item in (args[:2] if event=='os.rename' else args[:1]):
    if isinstance(item,(str,bytes,os.PathLike)):
     text=Path(os.fsdecode(item)).absolute().as_posix().lower()
     need('/tests/workspaces/g56/' not in text and '/docs/evidence/gm-056/' not in text,'FIRST_OLD_EXECUTION_DATA_FORBIDDEN')
  return super().audit(event,args)
 def profile(self,frame,event,arg):
  if event!='call':return
  f=frame.f_code.co_filename.replace('\\','/');n=frame.f_code.co_name
  if '/torch/cuda/' in f and n=='_lazy_init':raise RuntimeError('FIRST_CUDA_CONTEXT_FORBIDDEN')
  if self.dry and '/numpy/' in f and n=='load' and 'npyio' in f:raise RuntimeError('FIRST_DRY_ARRAY_LOAD_FORBIDDEN')
  if '/grademirror/' not in f:return
  if self.c['phase']=='fine_recovery':
   need(not (f.endswith('/group15_codec.py') and n in ('read_reference','inspect_reference')),'FIRST_PARTIAL_REFERENCE_FORBIDDEN')
   need(not (f.endswith('/cpu_refine/worker.py') and n=='prepare') and not (f.endswith('/p3_solver.py') and n in ('fit','solve')) and not (f.endswith('/area.py') and n=='area_prefix'),'FIRST_PARTIAL_REFIT_HT_CG_FORBIDDEN')
  expected=None
  if f.endswith('/group15_codec.py') and n in ('read_source','read_reference','inspect_source','inspect_reference'):expected=('host',)
  elif (f.endswith('/cpu_r0_image/executor.py') and n=='_fit_loop'):expected=('fit',)
  elif f.endswith('/independent_r0/core.py') and n in ('__init__','forward','render_tiles'):expected=('fit','r0')
  elif f.endswith('/cpu_postprocess/core.py') and n=='p3_region':
   caller=frame.f_back;range_caller=bool(caller and caller.f_code.co_name=='range_scan' and caller.f_code.co_filename.replace('\\','/').endswith('/cpu_fine_image/execution.py'))
   expected=(('prepare',) if self.c['mode']=='fine' and self.c['phase'] in ('refine','fine_recovery') else ()) if range_caller else ('cpu',)
  elif f.endswith('/cpu_fine_image/execution.py') and n=='range_scan':expected=('prepare',) if self.c['mode']=='fine' and self.c['phase'] in ('refine','fine_recovery') else ()
  elif '/cpu_postprocess/' in f and n in ('execute','worker','scalar_footprint','_run'):expected=('cpu',)
  elif f.endswith('/p3_solver.py') and n in ('fit','solve'):expected=('solver',)
  elif f.endswith('/cpu_refine/worker.py') and n=='prepare':expected=('prepare',)
  if expected is not None:
   metadata=self.dry and self.role=='cpu' and f.endswith('/cpu_postprocess/service.py') and n=='worker' and frame.f_locals.get('entry_only') is True
   need(metadata or (not self.dry and self.role in expected),'FIRST_ROLE_OR_DRY_BOUNDARY:'+n)
def enter(role,work,c):
 global _guard
 q=authorize(c['qualification']['path'],c['request'],cleanup=role in ('owner','application_control'));dry=role.startswith('entry_') or q['phase']=='dry';effective=role.removeprefix('entry_')
 need(effective in ('fit','r0','prepare','solver','cpu','owner','host','control','application_control'),'FIRST_WORKER_ROLE')
 if _guard is None:
  from .calibration_contract import measurement
  cls=Guard
  if measurement(c):
   from .calibration_runtime import Guard as cls
  from .portable_contract import enabled as portable_enabled
  if portable_enabled(c):
   from .portable_runtime import Guard as cls
  _guard=cls(c,role,dry);sys.addaudithook(_guard.audit);sys.setprofile(_guard.profile)
 else:need(_guard.c['id']==c['id'] and _guard.role==effective,'FIRST_PROCESS_REUSE')
 work=Path(work);work.mkdir(parents=True,exist_ok=True)
 from ..winproc import current_identity
 who=current_identity();atomic_json(work/'actual-entry.json',dict(identity=who,role=role,dry=dry));ep=_guard.observer/'entries'/uuid.uuid4().hex;atomic_json(ep/'entry.json',dict(identity=who,role=role,work=str(work),entry_only=dry));until=time.monotonic()+30
 while not (ep/'ack.json').exists():need(time.monotonic()<until,'FIRST_OUTER_HANDLE_ACK');time.sleep(.01)
 if effective in ('fit','r0','prepare','solver','cpu') or role.startswith('entry_'):
  until=time.monotonic()+30
  while not (work/'actual-retained.json').exists():need(time.monotonic()<until,'FIRST_PARENT_HANDLE_ACK');time.sleep(.01)
 atomic_json(work/'guard-graph.json',dict(role=role,dry=dry,write_roots=list(map(str,_guard.roots)),exact_inputs=list(map(str,_guard.inputs)),qualification=c['qualification']))
def capture(work,c,*,terminal=False):
 from .c2_cpu_tail import enabled as c2_enabled
 protection=None
 if c2_enabled(c) and _guard.role=='cpu':
  from .c2_runtime_pins import RuntimePins
  protection=getattr(_guard,'cpu_protection',None)
  if protection is None:
   need(not terminal,'C2_PIN_ENTRY_REQUIRED')
   q=read(c['qualification']['path'])
   scope=[binding(c['manifest_path']),c['qualification'],q['authorization'],q['fixed_inputs'],q['resource_model'],
          q['taskbook'],*q['interpreters'].values(),*c['parent_bundle']['files'],*c['parent_bundle']['producer'].values()]
   if q['p0'] is not None:scope.append(q['p0'])
   need(read(c['manifest_path'])==c,'C2_PIN_CONTEXT_CHANGED')
   known=dict(_guard.known)
   known.update((os.path.normcase(row['path']),row['sha256']) for row in scope)
   protection=_guard.cpu_protection=RuntimePins(known,c['executor_build']['sha256'],c['runtime']['inventory'])
   _guard.cpu_protected_scope=scope
  protection.begin(c['executor_build']['sha256'],'terminal' if terminal else 'entry')
  for row in _guard.cpu_protected_scope:
   need(protection.bind(row['path'],row['sha256'])==row,'C2_PIN_SCOPE_BINDING')
 data=original_capture(work,c,guard=_guard,protection=protection)
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(c):
  from .portable_resources import hard_limits
  data['portable_resource_facts']=hard_limits(c,_guard.role)
 return data
def close_cpu_protection():
 protection=getattr(_guard,'cpu_protection',None)
 if protection is not None:protection.close()

def observation(work,role,status):
 from ..winproc import current_identity
 data=capture(work,_guard.c,terminal=True);atomic_json(Path(work)/'runtime-observation.json',dict(data,role=role,status=status,identity=current_identity(),dry=_guard.dry,reads=sorted(_guard.reads),writes=sorted(_guard.writes),denied=_guard.denied))
 from .calibration_contract import measurement
 if measurement(_guard.c):
  from .calibration_observation import stage
  stage(work,'worker_exit_'+role,status=status)
