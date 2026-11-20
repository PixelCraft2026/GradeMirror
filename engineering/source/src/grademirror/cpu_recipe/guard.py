"""Thread/context-local zero-algorithm boundary for metadata operations."""
import contextvars,os,sys
from contextlib import contextmanager
from pathlib import Path
from .primitives import RecipeError,GMError
_active=contextvars.ContextVar('cpu_recipe_metadata',default=False)
_denied=contextvars.ContextVar('cpu_recipe_denied_roots',default=())
def _audit(event,args):
    if not _active.get():return
    if event=='import' and args[0].split('.')[0]in {'numpy','torch','PIL','scipy','cv2','imagecodecs','tifffile','pynvml','cupy','tensorflow','jax'}:raise RecipeError('ALGORITHM_IMPORT_FORBIDDEN',args[0],stage='guard')
    if event in {'subprocess.Popen','os.system','os.spawn','os.posix_spawn'}:raise RecipeError('METADATA_CHILD_FORBIDDEN',event,stage='guard')
    if event=='ctypes.dlopen' and any(k in str(args[0]).lower()for k in ('nvcuda','cudart','nvml')):raise RecipeError('GPU_LIBRARY_FORBIDDEN',stage='guard')
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=Path(os.fsdecode(args[0])).absolute()
        if any(path.is_relative_to(root)for root in _denied.get()):raise RecipeError('ISOLATED_OLD_PATH_READ',str(path),stage='guard')
sys.addaudithook(_audit)
@contextmanager
def metadata_only(*,forbidden_reads=()):
    a=_active.set(True);b=_denied.set((*_denied.get(),*(Path(p).absolute()for p in forbidden_reads)))
    try:yield
    finally:_denied.reset(b);_active.reset(a)
def metadata_api(function):
    from functools import wraps
    @wraps(function)
    def call(*args,**kwargs):
        with metadata_only():
            try:return function(*args,**kwargs)
            except GMError as e:
                if not hasattr(e,'stage'):e.stage=function.__name__
                raise
            except (OSError,ValueError,TypeError)as e:raise RecipeError('RECIPE_IO_ERROR',str(e),stage=function.__name__)from e
    return call
