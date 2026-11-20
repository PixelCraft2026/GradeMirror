"""Bounded counters and cooperative cancellation; no experiment queue."""
import time
from pathlib import Path
from ...common import atomic_json

class Ledger:
    def __init__(self,path,caps,*,cancel_path=None,deadline=None):
        self.path=Path(path);self.caps=dict(caps);self.counts={};self.cancel_path=Path(cancel_path)if cancel_path else None;self.deadline=deadline
        atomic_json(self.path,dict(status='PREPAID',caps=self.caps,actual={},prepaid_wall=time.time()))
    def check(self):
        if self.cancel_path and self.cancel_path.exists():raise InterruptedError('FINE_CANCELLED')
        if self.deadline and time.monotonic()>self.deadline:raise TimeoutError('FINE_DEADLINE')
    def take(self,**counts):
        self.check()
        for k,v in counts.items():
            if type(v)is not int or v<0 or k not in self.caps or self.counts.get(k,0)+v>self.caps[k]:raise RuntimeError('FINE_BUDGET_EXCEEDED:'+k)
        for k,v in counts.items():self.counts[k]=self.counts.get(k,0)+v
    def save(self,status='RUNNING'):
        atomic_json(self.path,dict(status=status,caps=self.caps,actual=self.counts))
