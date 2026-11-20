"""v4 accounting hooks only; original numerical expressions and order remain."""
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import binding,need
def start(c,component):
 if c and c.get('schema')=='cpu-application-execution-v4':
  from .first_science import start as begin
  begin(c,component)
def ready(c,stage,work):
 if not c or c.get('schema')!='cpu-application-execution-v4':return
 from .first_science import complete
 work=Path(work)
 if stage=='training':
  for component in ('normalize_source','normalize_reference'):complete(c,component,binding(work/'training-inputs.json'))
 elif stage=='P3inputs':
  for component in ('H','T'):complete(c,component,binding(work/'P3-inputs.json'))
 elif stage=='CG':
  from .first_contract import read
  proof=read(work/'solver/solver-cleanup.json');need(proof['actual_worker_handle_wait'] is True and proof['actual_wait'] is True and proof['tree_empty'] is True and proof['exit_code']==0,'FIRST_SOLVER_ACTUAL_WAIT_BEFORE_RANGE')
  complete(c,'CG',binding(work/'solver/solver-cleanup.json'))
 elif stage=='range':complete(c,'range',binding(work/'range-scan-actual.json'))
 else:need(False,'FIRST_ACCOUNTING_HOOK_STAGE')
def selected():
 from ..cpu_first_fit.contract import selected as get
 return get()
