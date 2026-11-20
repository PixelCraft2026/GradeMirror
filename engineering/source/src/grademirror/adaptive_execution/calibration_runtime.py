"""GM063 namespace adapter over accepted first/native role guards."""
import sys
from pathlib import Path
from .first_runtime import Guard as FirstGuard
from . import calibration_contract as k
from ..cpu_replay.contract import need


class Guard(FirstGuard):
    def __init__(self,c,role,dry):
        super().__init__(c,role,dry,namespace=(k.E,k.W,k.I))
        self.exact.update({k.E/'fixed-inputs.json',k.E/'science-budget.jsonl',k.E/'interpreters.json',k.E/'observation.json',k.E/'authorization.json'})
        q=k.strict_load(c['qualification']['path'])
        if q.get('admission_addendum') is not None:self.exact.add(Path(q['admission_addendum']['path']).resolve())
        if q.get('recovery') is not None:self.exact.add(Path(q['recovery']['path']).resolve())

    def audit(self,event,args):
        if event=='subprocess.Popen' and self.role=='host':
            from ..export_metadata import helper_argv_allowed
            if helper_argv_allowed(args[0],args[1]):return
        return super().audit(event,args)

    def profile(self,frame,event,arg):
        if event=='call' and self.c['request']['operation']=='replay':
            f=frame.f_code.co_filename.replace('\\','/');name=frame.f_code.co_name
            need(not(f.endswith('/group15_codec.py') and name in('read_reference','inspect_reference')),'GM063_REPLAY_REFERENCE_FORBIDDEN')
        return super().profile(frame,event,arg)
