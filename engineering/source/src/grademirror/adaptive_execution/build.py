"""Per isolated worker graph memo, with full byte revalidation on every use.

The Qt/API caller has no GM055_CONTEXT and never uses this memo. Its indexed
traversal is rebuilt with full selected-file hashes on each use. A changed
source, manifest, graph seed or extra file invalidates the graph; no timestamp
or size alone is accepted as proof of unchanged code.
"""
import copy
import os
from pathlib import Path
from ..common import sha_file
from .intent import need
_saved=None
_key=None
_seeds=None

def source_names(root):
    """Filename index only; not a whole-tree hash or an acceptance cache."""
    base=root/'src';pending=[base/'grademirror'];names=set()
    while pending:
        directory=pending.pop()
        with os.scandir(directory) as entries:
            for item in entries:
                info=item.stat(follow_symlinks=False)
                need(not item.is_symlink() and not getattr(info,'st_file_attributes',0)&0x400,'APPLICATION_SOURCE_REPARSE')
                if item.is_dir(follow_symlinks=False):pending.append(Path(item.path))
                elif item.name.endswith('.py') and item.is_file(follow_symlinks=False):
                    names.add(os.path.normcase(str(Path(item.path).relative_to(base))))
    return names

def current_closure(*,native=False,hash_file=sha_file):
    from ..cpu_replay.contract import _executor_closure
    if native and os.environ.get('GM055_CONTEXT'):
        return verified_closure(lambda:_executor_closure(indexed=True,native_scope=True),hash_file=hash_file)
    return _executor_closure(indexed=True,native_scope=native)

def seeds(root):
    patterns=('src/grademirror/cpu_refine/*.py','src/grademirror/adaptive_execution/*.py',
        'src/grademirror/cpu_first_fit/*.py','schemas/cpu-*.json','schemas/adaptive-*.json',
        'tools/gm05*_worker.py')
    return tuple(sorted(str(p.relative_to(root)) for pattern in patterns for p in root.glob(pattern)))

def verified_closure(builder,*,hash_file=sha_file):
    global _saved,_key,_seeds
    from ..cpu_replay.contract import ROOT
    context=Path(os.environ['GM055_CONTEXT']).resolve()
    key=(str(context),sha_file(context));current=seeds(ROOT)
    if _saved is None:
        _saved=builder();_key=key;_seeds=current
    else:
        need(key==_key,'APPLICATION_PROCESS_CONTEXT_REUSE')
        need(current==_seeds,'APPLICATION_BUILD_SEEDS_CHANGED')
        for row in _saved['files']:
            need(hash_file(ROOT/row['path'])==row['sha256'],'APPLICATION_EXECUTOR_CHANGED',row['path'])
    return copy.deepcopy(_saved)
