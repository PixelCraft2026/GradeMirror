"""Independent, synthetic-qualified CPU rendering; no public task dispatch."""
from .contract import BACKEND_ID,implementation

def render(*args,**kwargs):
    from .executor import render as execute
    return execute(*args,**kwargs)
