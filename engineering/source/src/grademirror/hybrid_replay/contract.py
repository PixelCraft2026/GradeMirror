"""Strict identities and implementation binding. No numeric imports."""
import hashlib, json, re
from pathlib import Path
from ..common import GMError, canonical_json, sha_file, strict_load, reject_reparse
from . import BACKEND_ID, CONTRACT_VERSION, MANIFEST_SCHEMA, LEASE_SCHEMA, VERSION_FIELDS

ROOT = Path(__file__).resolve().parents[3]
CORE_ROWS = 512
RUNTIME = dict(numpy='1.26.4', torch='2.5.1+cu124', threads=4, interop=1)


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def require(value, code='HYBRID_CONTRACT_INVALID'):
    if not value: raise GMError(code, code, 7)


def hash_value(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def execution_contract():
    from .runtime_identity import identity
    from ..light.portable import source_files, source_sha
    # Relative names make the identity portable; no generated digest source is
    # included. Every implementation change produces a distinct contract.
    files = source_files(ROOT/'src/grademirror/hybrid_replay', '*.py')
    files += source_files(ROOT/'src/grademirror/hybrid_fit', '*.py')
    files += source_files(ROOT/'src/grademirror/resource_execution', '*.py')
    files += sorted((ROOT/'schemas').glob('resource-execution-*-v1.json'))
    names = ['src/grademirror/'+n for n in ('__main__.py','gm004a_api.py','gm004a_operations.py',
             'gm003_supervisor.py','gm004a_resident.py','lease.py','gm004a_preview.py','cli.py','desktop/backend.py',
             'engine/cpu_postprocess/receiver.py','engine/cpu_postprocess/core.py',
             'engine/cpu_postprocess/executor.py','engine/cpu_postprocess/geometry.py',
             'engine/cpu_postprocess/contract.py','engine/cpu_postprocess/service.py',
             'engine/cpu_postprocess/transport.py','export_pixels.py','export_metadata.py','gm004a_export.py')]
    names += ['tools/gm003_session_host.py','tools/gm003_session_owner.py','tools/gm021_compute_worker.py']
    names += ['tools/gm023_compute_worker.py']
    names += ['tools/gm003_compute_worker.py']
    names += ['src/grademirror/engine/'+n for n in ('independent_r0/adapter.py','independent_r0/core.py','independent_r0/profile.py',
        'independent_r0_p3_lld10/area.py','independent_r0_p3_lld10/profile.py','independent_r0_p3_lld10/solver_process.py',
        'independent_r0_p3_lld10/p3_solver.py')]
    names += ['tools/gm008_solver_worker.py']
    files += [ROOT/n for n in names]
    implementation = {p.relative_to(ROOT).as_posix(): source_sha(p) for p in files}
    value = dict(version=CONTRACT_VERSION, backend_id=BACKEND_ID, implementation=implementation,
                 runtime=RUNTIME, primary_runtime=identity(), core_rows=512, tile_side=512, precision='original-f32-f64-order',
                 no_fallback=True, color='parent-fine-color-policy-unchanged')
    from .resource_journal import VERSION as journal_version
    value['resource_journal']=journal_version
    from .monitor_completion import VERSION as completion_version
    value['monitor_completion']=completion_version
    value['hybrid_fit_replay_cpu_tiers']=dict(default=512,explicit=[256,512],policy='guarded-v1',runtime_switch=False)
    return value, digest(value)


def read_execution_manifest(path):
    """Explicit type routing, retaining the old reader's exact field contract."""
    from ..hybrid_fit import MANIFEST_SCHEMA as new_schema
    if strict_load(Path(path)).get('schema_version')==new_schema:
        from ..hybrid_fit.contract import read_manifest as read_new
        return read_new(path)
    return read_manifest(path)


def versions(value):
    require(isinstance(value, dict) and VERSION_FIELDS <= value.keys(), 'HYBRID_VERSION_REQUIRED')
    require(value['lease_schema_version']==LEASE_SCHEMA and value['backend_id']==BACKEND_ID)
    require(hash_value(value['execution_manifest_sha256']) and
            value['execution_contract_sha256']==execution_contract()[1], 'HYBRID_IMPLEMENTATION_CHANGED')
    return {k:value[k] for k in sorted(VERSION_FIELDS)}


def checked_binding(value, base_fields):
    require(isinstance(value,dict) and set(value)==set(base_fields)|VERSION_FIELDS,
            'HYBRID_BINDING_FIELDS')
    versions(value)
    return dict(value)


def asset(root, relative):
    require(isinstance(relative,str) and relative in ('recipe.json','parameters.npz','p3.npz'),
            'HYBRID_ASSET_PATH')
    root=Path(root).resolve();p=root/relative;reject_reparse(root);reject_reparse(p)
    require(p.resolve().parent==root and p.is_file(), 'HYBRID_ASSET_MISSING')
    require(p.stat().st_size<=4*1024**2, 'HYBRID_ASSET_QUOTA')
    return p


def parent(directory):
    directory=Path(directory).resolve()
    paths={n:asset(directory,n) for n in ('recipe.json','parameters.npz','p3.npz')}
    require(sum(p.stat().st_size for p in paths.values())<=4*1024**2,'HYBRID_PACKAGE_QUOTA')
    m=strict_load(paths['recipe.json'])
    from ..fine_contract import digest as parent_digest
    require(m.get('recipe_schema_version')=='0.5.0' and
            m.get('profile_id')=='independent-r0-p3-lld10-v1' and
            m.get('recipe_sha256')==parent_digest(m), 'HYBRID_PARENT_RECIPE')
    from ..engine.profiles import get_profile
    profile=get_profile(m['profile_id'],m.get('profile_contract_sha256'))
    require(m.get('core_sha256')==profile['core_sha256'],'HYBRID_PARENT_CORE')
    embedded=m.get('r0_recipe',{})
    require(all(m.get(k)==embedded.get(k) for k in('source_identity','reference_identity','device','runtime','color_policy')),
            'HYBRID_PARENT_R0_BINDING')
    require(sha_file(paths['parameters.npz'])==m['archive_sha256'] and
            sha_file(paths['p3.npz'])==m['p3_archive_sha256'], 'HYBRID_PARAMETER_CHANGED')
    return m, {n:dict(path=n,bytes=p.stat().st_size,sha256=sha_file(p)) for n,p in paths.items()}


def read_manifest(path):
    path=Path(path).resolve();reject_reparse(path);m=strict_load(path)
    fields={'schema_version','backend_id','execution_contract_version','execution_contract_sha256',
            'parent_recipe_sha256','assets','source_identity','reference_identity','ranges',
            'device','runtime','R0_runtime','parent_bindings','cpu_bindings','color_policy','core_rows','results','quantization','scope','manifest_sha256'}
    require(set(m)==fields and m['schema_version']==MANIFEST_SCHEMA and
            m['backend_id']==BACKEND_ID and m['execution_contract_version']==CONTRACT_VERSION,
            'HYBRID_MANIFEST_SCHEMA')
    require(m['manifest_sha256']==digest({k:v for k,v in m.items() if k!='manifest_sha256'}),
            'HYBRID_MANIFEST_DIGEST')
    from ..light.math_bridge import compatible_hybrid_execution
    require(compatible_hybrid_execution(m['execution_contract_sha256']) and m['core_rows']==512 and
            m['runtime']==RUNTIME, 'HYBRID_IMPLEMENTATION_CHANGED')
    recipe,assets=parent(path.parent)
    require(assets==m['assets'] and recipe['recipe_sha256']==m['parent_recipe_sha256'],
            'HYBRID_PARENT_CHANGED')
    require(m['parent_bindings']==parent_bindings(recipe) and m['R0_runtime']==recipe['runtime'] and
            m['cpu_bindings']==cpu_bindings(),'HYBRID_ALGORITHM_BINDING')
    require(m['scope']==qualification_scope(),'HYBRID_QUALIFICATION_SCOPE')
    require(m['source_identity']==recipe['source_identity'] and
            m['reference_identity']==recipe['reference_identity'] and
            m['ranges']==recipe['postprocess']['ranges'] and m['device']==recipe['device'] and
            m['color_policy']==recipe['color_policy'], 'HYBRID_MANIFEST_PARENT_BINDING')
    require(set(m['results'])=={'O','OP','LO','LP','Y'} and
            all(hash_value(v) for v in m['results'].values()),'HYBRID_MANIFEST_RESULTS')
    require(set(m['quantization'])=={str(b)+':'+str(a) for b in (8,16) for a in (0.,.5,1.)} and
            all(hash_value(v) for v in m['quantization'].values()),'HYBRID_MANIFEST_QUANTIZATION')
    marker=strict_load(path.parent/'published.json')
    require(set(marker)=={'schema_version','manifest_sha256','manifest_file_sha256'} and
            marker['schema_version']==MANIFEST_SCHEMA and marker['manifest_sha256']==m['manifest_sha256'] and
            marker['manifest_file_sha256']==sha_file(path), 'HYBRID_PUBLICATION_MARKER')
    return m,recipe


def parent_bindings(recipe):
    return dict(profile_id=recipe['profile_id'],profile_contract_sha256=recipe['profile_contract_sha256'],
        core_sha256=recipe['core_sha256'],parameters_archive_sha256=recipe['archive_sha256'],
        p3_archive_sha256=recipe['p3_archive_sha256'],b_sha256=recipe['postprocess']['coefficient_sha256'],
        b_shape=recipe['postprocess']['coefficient_shape'])


def cpu_bindings():
    from ..engine.cpu_postprocess import EXECUTOR_ID
    from ..engine.cpu_postprocess.receiver import RECEIVER_ID
    # Importing receiver has no NumPy/Torch import or pixel allocation.
    math_files=('core.py','executor.py','geometry.py','contract.py','service.py','transport.py')
    from ..light.portable import source_sha
    directory=ROOT/'src/grademirror/engine/cpu_postprocess'
    return dict(executor_id=EXECUTOR_ID,receiver_id=RECEIVER_ID,
                mathematical_sources={n:source_sha(directory/n) for n in math_files})


def qualification_scope():
    return dict(experimental_manual=True,CPU_QUALIFIED=False,AUTO_SELECTION=False,
        PRODUCTION_ACTIVATION=False,native_FP64='NOT_MEASURED_NATIVE',visual='PENDING')
