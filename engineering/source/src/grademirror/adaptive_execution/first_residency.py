"""Process-scoped full-entry proof; rehash bytes, never trust mtime or sizes alone."""
import contextvars
from contextlib import contextmanager
from pathlib import Path
from ..cpu_replay.contract import binding,verify,need
from .first_contract import read,E,I
_proof=contextvars.ContextVar('gm057_verified_residency',default=None)
@contextmanager
def scope(c):
 q=read(c['qualification']['path'])
 from .portable_contract import enabled as portable_enabled,e as portable_e
 if portable_enabled(c):
  from ..cpu_replay.contract import ROOT
  rows=[binding(c['manifest_path']),c['qualification'],c['runtime']['inventory'],q['authorization'],q['taskbook'],q['fixed_inputs'],q['resource_model'],binding(portable_e(c)/'allocations.json'),binding(ROOT/'validation/capability.json'),*c['interpreters'].values()]
 else:
  rows=[binding(c['manifest_path']),c['qualification'],c['runtime']['inventory'],q['authorization'],q['taskbook'],*[binding(I/x) for x in ('fixed-inputs.json','interpreters.json','capacity.json')],*c['interpreters'].values()]
 from .calibration_contract import measurement
 if measurement(c):
  from .calibration_contract import E as calibration_E
  rows=[binding(c['manifest_path']),c['qualification'],c['runtime']['inventory'],q['authorization'],q['taskbook'],q['fixed_inputs'],binding(calibration_E/'interpreters.json'),binding(calibration_E/'allocations.json'),*c['interpreters'].values()]
 if q['p0']:rows.append(q['p0'])
 if q['parent_package']:rows.extend(q['parent_package']['files']);rows.extend(q['parent_package']['producer'].values())
 if q.get('partial_b'):
  rows.append(q['partial_b']);v=read(q['partial_b']['path']);rows.extend(v[k] for k in v if k not in ('schema','task'))
 unique={str(Path(x['path']).resolve()):x for x in rows};token=_proof.set((c,[binding(c['manifest_path']),c['qualification']],list(unique.values())))
 try:yield
 finally:_proof.reset(token)
def active_context(spec):
 from ..cpu_first_fit.contract import _active
 p=_proof.get();c=_active.get()
 if not p or not c or c.get('schema')!='cpu-application-execution-v4':return None
 need(p[0] is c,'RESIDENCY_PROCESS_CONTEXT_MISMATCH')
 from .first_science import check_accepted
 q=check_accepted(spec,c)
 for row in p[1]:verify(row)
 from .calibration_contract import measurement
 index=E/'qualification-index.json'
 from .portable_contract import enabled as portable_enabled,e as portable_e
 if portable_enabled(c):
  from .portable_contract import qualification_index
  index=qualification_index(portable_e(c),q['manifest_path'])
 if measurement(c):
  from .calibration_contract import E as calibration_E
  index=calibration_E/'qualification-index.json'
 cp=Path(c['manifest_path']);need(read(cp.parent.parent/'context-index.json').get(c['id'])==binding(cp) and read(index).get(q['manifest_path'])==c['qualification'],'RESIDENCY_REGISTERED_BYTES')
 return c

def verify_inputs(spec):
 c=active_context(spec);need(c is not None,'RESIDENCY_FULL_ENTRY_PROOF_REQUIRED')
 for row in _proof.get()[2]:verify(row)
 return c
