"""Explicit experimental CUDA fit followed by bounded CPU postprocessing."""
FIT_CONTRACT='gm023-hybrid-fit-v1'
RECIPE_SCHEMA='hybrid-fit-recipe-1.0.0'
MANIFEST_SCHEMA='hybrid-fit-manifest-1.0.0'
REVISION='hybrid-r000001'
ASSETS=('r0/recipe.json','r0/parameters.npz','p3.npz','recipe.json')

def is_fit_type(spec):
    from ..hybrid_replay import BACKEND_ID
    return (spec.get('kind')in ('fit','replay') and spec.get('semantics',{}).get('fit_contract')==FIT_CONTRACT
            and spec['semantics'].get('backend_id')==BACKEND_ID)
