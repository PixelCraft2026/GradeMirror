"""Durable fine recipe completion is independent of RAM or supervisor success."""
from pathlib import Path
from ...common import strict_load,atomic_json
from ...store import locked
from .contract import need,binding,verify,digest,output,BACKEND,RECIPE,seed_binding
FIELDS={'schema','backend','backend_sha256','case','intent','seed','O','reference_wh','b','b_array','ranges','solver','H_sha256','T_sha256','hashes','color','execution','recipe_sha256'}
def recover(directory,intent=None):
    p=output(directory)
    with locked(p/'fine-publication.lock'):
        need((p/'COMPLETE.json').is_file(),'FINE_INCOMPLETE')
        mark=strict_load(p/'COMPLETE.json');need(set(mark)=={'schema','recipe','b','recipe_sha256'},'FINE_MARKER_FIELDS')
        need(mark['schema']=='cpu-fine-image-complete-v1','FINE_MARKER_VERSION');verify(mark['recipe']);verify(mark['b'])
        need(Path(mark['recipe']['path'])==p/'fine-recipe.json' and Path(mark['b']['path'])==p/'b.npz','FINE_MARKER_PATH')
        m=strict_load(p/'fine-recipe.json');need(set(m)==FIELDS and m['schema']==RECIPE and m['backend']==BACKEND,'FINE_RECIPE_FIELDS')
        body={k:v for k,v in m.items()if k!='recipe_sha256'};need(digest(body)==m['recipe_sha256']==mark['recipe_sha256'],'FINE_SEMANTIC_HASH')
        need(m['b']==mark['b'] and set(m['hashes'])=={'O','OP','LO','LP','Y'},'FINE_RECIPE_BINDING')
        if intent is not None:need(m['intent']==intent,'FINE_KEY_CONFLICT')
        if m['case']=='group15':need(m['seed']==seed_binding(),'FINE_EXTERNAL_SEED_CHANGED')
        # Recover after COMPLETE-before-supervisor crash; this code starts no worker.
        atomic_json(p/'publication-status.json',dict(state='PERSISTENT_COMPLETE',recipe_sha256=m['recipe_sha256'],resident=False,availability='REPLAY_REQUIRED',recovered_from_complete=True))
        return m
def publish(directory,metadata):
    p=output(directory);p.mkdir(parents=True,exist_ok=True)
    with locked(p/'fine-publication.lock'):
        need(not(p/'COMPLETE.json').exists(),'FINE_ALREADY_COMPLETE')
        need(set(metadata)==FIELDS-{'recipe_sha256'},'FINE_PUBLICATION_FIELDS')
        m=dict(metadata,recipe_sha256=digest(metadata));atomic_json(p/'fine-recipe.json',m)
        atomic_json(p/'COMPLETE.json',dict(schema='cpu-fine-image-complete-v1',recipe=binding(p/'fine-recipe.json'),b=binding(p/'b.npz'),recipe_sha256=m['recipe_sha256']))
    return recover(p,metadata['intent'])
