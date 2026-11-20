"""Portable, input-bound fast parameters for a new fine project; no numeric imports."""
from pathlib import Path
from .common import GMError,atomic_json,sha_file,strict_load
from .engine import profiles
from .project import create_project,show_project,_resolve_project_relative_file
from .portable_project import open_portable_project,_canonical_digest

VERSION='fast-to-fine-v1'

def checked_seed(root,manifest):
    seed=manifest['extensions'].get('refinement_seed')
    if seed is None:return None
    keys={'version','origin_project_id','recipe_sha256','recipe_file_sha256','parameters_file_sha256','float32_sha256'}
    if not isinstance(seed,dict)or set(seed)!=keys or seed['version']!=VERSION or manifest['extensions'].get('algorithm_profile')!=profiles.FINE:
        raise GMError('REFINEMENT_SEED_INVALID','Invalid fine refinement parent',7)
    for name in keys-{'version','origin_project_id'}:
        value=seed[name]
        if not isinstance(value,str)or len(value)!=64 or any(c not in'0123456789abcdef'for c in value):raise GMError('REFINEMENT_SEED_INVALID','Invalid seed digest',7)
    recipe=_resolve_project_relative_file(root,'refinement/recipe.json');parameters=_resolve_project_relative_file(root,'refinement/parameters.npz')
    if recipe.stat().st_size>256*1024 or parameters.stat().st_size>256*1024 or sha_file(recipe)!=seed['recipe_file_sha256']or sha_file(parameters)!=seed['parameters_file_sha256']:
        raise GMError('REFINEMENT_SEED_CHANGED','Saved fast parameters changed',7)
    meta=strict_load(recipe)
    if (profiles.metadata_recipe_profile(meta)['id']!=profiles.R0 or meta['recipe_sha256']!=seed['recipe_sha256']or _canonical_digest(meta)!=seed['recipe_sha256']or meta['archive_sha256']!=seed['parameters_file_sha256']or any(meta[k+'_sha256']!=manifest[k]['byte_sha256']for k in('source','reference'))):
        raise GMError('REFINEMENT_SEED_INVALID','Fast parameters belong to different inputs or profile',7)
    from .fit_publication import validate_archive
    validate_archive(parameters,meta['parameter_sha256'],shapes=profiles.get_profile(profiles.R0)['shapes'])
    return dict(seed)

def create_refinement(project,destination):
    view=open_portable_project(project,available_runtimes=profiles.runtimes())
    if view.get('read_only')or view.get('profile_id')!=profiles.R0:raise GMError('REFINEMENT_REQUIRES_FAST','A completed writable fast project is required',4)
    root=Path(project).resolve();parent=strict_load(root/'project.json');recipe=Path(view['recipe_dir']);index=strict_load(Path(view['artifacts_index_path']))
    seed=dict(version=VERSION,origin_project_id=parent['project_id'],recipe_sha256=view['recipe_sha256'],recipe_file_sha256=sha_file(recipe/'recipe.json'),parameters_file_sha256=sha_file(recipe/'parameters.npz'),float32_sha256=index['result_float32_sha256'])
    create_project(Path(view['source']['canonical_path']),Path(view['reference']['local_path']),Path(destination),mode='fine')
    target=Path(destination).resolve();folder=target/'refinement';folder.mkdir()
    for name in('recipe.json','parameters.npz'):
        data=(recipe/name).read_bytes()
        if len(data)>256*1024:raise GMError('REFINEMENT_SEED_INVALID','Fast recipe quota exceeded',7)
        (folder/name).write_bytes(data)
    manifest=strict_load(target/'project.json');manifest['extensions']['refinement_seed']=seed
    for name in('source','reference'):manifest[name]['name']=parent[name]['name']
    checked_seed(target,manifest);atomic_json(target/'project.json',manifest)
    return show_project(target,available_runtimes=profiles.runtimes())

def expected_fit_count(semantic):
    return 0 if semantic.get('refinement_seed')is not None or semantic.get('saved_r0') is not None else 1
