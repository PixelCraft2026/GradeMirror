"""Exact dry entry probes of original range call graph, before first math body."""
import sys,copy
from pathlib import Path
from types import SimpleNamespace
from ..common import atomic_json
from ..cpu_replay.contract import need

def run(path,c):
 from .first_runtime import Guard,_guard
 from ..engine.cpu_fine_image.execution import range_scan
 from ..engine.cpu_postprocess.core import p3_region
 class Reached(Exception):pass
 class Provider:
  o=SimpleNamespace(shape=(1,1,3));b=None
  def read(self,*args):return None
 rows=[]
 def one(role,dry,phase,mode,direct,success):
  pc=dict(c,phase=phase,mode=mode);g=object.__new__(Guard);g.c=pc;g.role=role;g.dry=dry;entered=[]
  def prof(frame,event,arg):
   g.profile(frame,event,arg)
   if event=='call' and frame.f_code is p3_region.__code__:entered.append('p3_region');raise Reached('ORIGINAL_RANGE_TO_P3_BEFORE_BODY')
  try:
   sys.setprofile(prof)
   if direct:p3_region(None,None,None,None,None,None,None,None)
   else:range_scan(Provider(),dict(reference_wh=[1024,1536]),None)
  except Reached:need(success and entered==['p3_region'],'RANGE_PROBE_FALSE_ALLOW');rows.append(dict(role=role,dry=dry,phase=phase,mode=mode,direct=direct,decision='ALLOW_BEFORE_BODY',math=0))
  except BaseException as e:need(not success and getattr(e,'code','').startswith('FIRST_ROLE_OR_DRY_BOUNDARY'),'RANGE_PROBE_FALSE_DENY:'+str(e));rows.append(dict(role=role,dry=dry,phase=phase,mode=mode,direct=direct,decision='STRICT_REJECT',error=str(e),math=0))
  else:raise AssertionError('RANGE_PROBE_BODY_RETURNED')
  finally:sys.setprofile(_guard.profile)
 one('prepare',False,'fine_recovery','fine',False,True)
 one('prepare',False,'refine','fine',False,True)
 one('prepare',True,'fine_recovery','fine',False,False)
 for role in ('cpu','host','r0','solver','control'):one(role,False,'fine_recovery','fine',False,False)
 one('prepare',False,'fit','fast',False,False)
 one('prepare',False,'fine_recovery','fine',True,False)
 one('cpu',False,'fine_recovery','fine',True,True)
 need(sys.getprofile()==_guard.profile,'RANGE_PROBE_PROFILE_RESTORED');atomic_json(Path(path).parent/'range-guard-proof.json',dict(status='PASS',rows=rows,source='ORIGINAL_RANGE_SCAN_REAL_P3_CALL_FRAME',first_body_executed=False,science=0,photo_decode=0,guard_restored=True))
