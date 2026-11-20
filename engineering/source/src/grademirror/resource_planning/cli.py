"""Strict JSON file intake only. No input-image or project inspection."""
import json
from pathlib import Path
from .contract import MAX_BYTES, bounded, require
from .planner import plan_resources

def loads(raw):
    require(isinstance(raw,str) and len(raw.encode('utf-8'))<=MAX_BYTES,'PLAN_QUOTA')
    def pairs(items):
        out={}
        for k,v in items:
            require(k not in out,'DUPLICATE_JSON_KEY');out[k]=v
        return out
    def bad(value):require(False,'PLAN_NUMBER_INVALID',value)
    try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=bad)
    except (ValueError,RecursionError) as e:
        require(False,'PLAN_JSON_INVALID',str(e)[:256])
    bounded(value);return value

def _read_file(path):
    p=Path(path);require(p.is_absolute(),'PLAN_ABSOLUTE_SPEC_REQUIRED')
    try:
        require(p.is_file() and p.stat().st_size<=MAX_BYTES,'PLAN_QUOTA')
        with p.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
        require(len(raw)<=MAX_BYTES,'PLAN_QUOTA')
        value=loads(raw.decode('utf-8'))
    except (OSError,UnicodeError) as e:require(False,'PLAN_SPEC_UNREADABLE',str(e)[:256])
    return value

def from_file(path, snapshot_path=None):
    return plan_resources(_read_file(path),_read_file(snapshot_path) if snapshot_path is not None else None)
