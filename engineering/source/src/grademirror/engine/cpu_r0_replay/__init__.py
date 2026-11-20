"""Manifest-scoped CPU R0 v2; deliberately absent from production dispatch."""
from .contract import BACKEND_ID, implementation

def render(*args, **kwargs):
    from .executor import render as execute
    return execute(*args, **kwargs)
