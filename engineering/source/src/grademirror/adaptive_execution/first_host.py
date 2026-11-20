"""Delegate v4 preparation to original fit/refine host adapters."""
from ..cpu_replay.contract import binding,need
from .first_contract import read
from . import first_science as science
def prepare(host):
 from .portable_contract import enabled as portable_enabled
 if portable_enabled(host.context):
  from .portable_host import prepare as portable_prepare
  return portable_prepare(host)
 from .calibration_contract import measurement
 if measurement(host.context) and host.context['phase']=='replay_fine':
  import time
  q=read(host.context['qualification']['path']);host.deadline=time.monotonic()+q['limits']['fine_compute']
  from ..cpu_refine.host import prepare as original
  return original(host)
 q=read(host.context['qualification']['path']);LIMITS=q['limits']
 import time
 host.deadline=time.monotonic()+LIMITS['fast_compute' if host.context['mode']=='fast' else 'fine_compute']
 host.progress('read_source')
 if host.context['phase'] in ('fast_recovery','fast_recovery_tail'):
  from .first_recovery import prepare as original
 elif host.context['phase']=='fine_recovery':
  from .first_range_recovery import prepare as original
 elif host.context['phase']=='fit':
  from ..cpu_first_fit.host import prepare as original
 else:
  from ..cpu_refine.host import prepare as original
 return original(host)

def record_decode(host,name,info):
 if not host.context or host.context.get('schema')!='cpu-application-execution-v4':return
 from ..common import atomic_json
 c=host.context;need(info['pixel_sha256']==c[name+'_identity']['pixel_sha256'],'FIRST_DECODE_IDENTITY')
 receipt=host.path/('native-'+name+'-receipt.json');atomic_json(receipt,dict(file=c[name],pixel_sha256=info['pixel_sha256'],shape=c[name+'_identity']['shape'],decode_completed=True))
 science.complete(c,name,binding(receipt))
