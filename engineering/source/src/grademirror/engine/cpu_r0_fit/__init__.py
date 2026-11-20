"""Small-manifest CPU first-fit entry; absent from product dispatch."""
from .contract import BACKEND_ID,implementation

def fit(pair,request,*,cancelled=lambda:False,progress=None):
    from .executor import fit as execute
    return execute(pair,request,cancelled=cancelled,progress=progress)
