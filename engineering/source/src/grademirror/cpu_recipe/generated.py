"""Generated v2 recipes: bounded parameters, local provenance, atomic COMPLETE.

No model/array/image imports. Existing v1 packing and authority are unchanged.
"""
import time
import uuid
from pathlib import Path
from ..common import atomic_json
from ..store import locked
from .primitives import (root, read, read_bytes, fields, require, digest, inside,
                         file_record, verify_file, new_json, reject_reparse, RecipeError)
from .assets import parse_file, parse_npz
from .identity import math_closure, runtime_status
from .guard import metadata_api

MANIFEST_FIELDS = {'format', 'version', 'mode', 'mathematical', 'math_id', 'runtime_requirements',
                   'producer_build', 'qualification', 'assets', 'provenance', 'bundle_id'}


def structure(m):
    if type(m) is dict and type(m.get('version')) is int and m['version']==3 and m.get('mode')=='fast':
        from .recovered import structure as recovered_structure
        return recovered_structure(m)
    fields(m, MANIFEST_FIELDS)
    require(m['format'] == 'grademirror.cpu_recipe_bundle' and type(m['version']) is int and
            m['version'] == 2 and m['mode'] == 'fast', 'GENERATED_BUNDLE_VERSION')
    math = m['mathematical']
    fields(math, ('profile', 'settings', 'normalization', 'inputs', 'color_policy',
                  'color_declaration', 'r0_parameters', 'math_files'))
    fields(math['inputs'], ('source_identity', 'reference_identity'))
    fields(math['profile'], ('id','contract_sha256','core_sha256'))
    preview = m['provenance'].get('producer') == 'gm065-preview-original-cpu-fit'
    depths = {'uint8': 8, 'uint16': 16} if preview else {'float32': 32, 'uint8': 8}
    for identity in math['inputs'].values():
        fields(identity, ('shape','dtype','bit_depth','file_sha256','file_size_bytes','pixel_sha256'))
        require(type(identity['shape']) is list and len(identity['shape']) == 3 and identity['shape'][2] == 3 and
                all(type(v) is int and v>0 for v in identity['shape']) and identity['dtype'] in depths and
                identity['bit_depth'] == depths[identity['dtype']],'GENERATED_INPUT_IDENTITY')
    fields(math['r0_parameters'], ('members', 'value_sha256'))
    fields(m['producer_build'], ('files', 'sha256'))
    require(digest(m['producer_build']['files']) == m['producer_build']['sha256'], 'PRODUCER_BUILD_ID')
    fields(m['qualification'], ('status', 'execution_qualified', 'general_input_qualified', 'CPU_ENABLED',
                                'AUTO_ENABLED', 'PRODUCTION_ENABLED', 'D01_extended', 'user_visual'))
    require(m['qualification']['status'] == 'CPU_FIT_GENERATED_PENDING_SCOPED_QUALIFICATION' and
            m['qualification']['user_visual'] == 'PENDING' and all(m['qualification'][k] is False for k in
            ('execution_qualified', 'general_input_qualified', 'CPU_ENABLED', 'AUTO_ENABLED', 'PRODUCTION_ENABLED', 'D01_extended')),
            'GENERATED_QUALIFICATION_ESCALATION')
    fields(m['provenance'], ('fit_intent_sha256', 'job_id', 'attempt_id', 'producer', 'TEST_ONLY'))
    require((m['provenance']['producer'] == 'original_cpu_fit_loop' and type(m['provenance']['TEST_ONLY']) is bool)
            or (preview and m['provenance']['TEST_ONLY'] is False),
            'GENERATED_PRODUCER')
    require(digest(math) == m['math_id'] and digest({k:v for k,v in m.items() if k != 'bundle_id'}) == m['bundle_id'], 'GENERATED_SEMANTIC')
    require(type(m['assets']) is list and {x['path'] for x in m['assets']} ==
            {'r0/parameters.npz', 'provenance/fit-intent.json', 'provenance/fit-receipt.json'} and len(m['assets']) == 3,
            'GENERATED_ASSET_SET')
    for item in m['assets']:
        fields(item, ('path', 'bytes', 'sha256'))
        require(type(item['bytes']) is int and 0 < item['bytes'] <= 8*1024**2, 'GENERATED_ASSET_SIZE')
    return m


def complete(directory):
    if read(root(directory)/'COMPLETE.json').get('format')=='cpu-recipe-complete-recovered-fast-v3':
        from .recovered import complete as recovered_complete
        return recovered_complete(directory)
    return _complete(directory)

def validate_pending(directory,mark):
    """Run the full existing saved-data checks before publishing a missing mark."""
    return _complete(directory,_pending_mark=mark)

def _complete(directory,*,_pending_mark=None):
    p = root(directory)
    mark = read(p / 'COMPLETE.json') if _pending_mark is None else _pending_mark
    fields(mark, ('format', 'bundle_id', 'math_id', 'manifest', 'operation_id', 'attempt_id'))
    require(mark['format'] == 'cpu-recipe-complete-v2' and mark['manifest']['path'] == 'manifest.json', 'GENERATED_COMPLETE_VERSION')
    verify_file(p / 'manifest.json', mark['manifest'])
    m = structure(read(p / 'manifest.json'))
    require((mark['bundle_id'], mark['math_id']) == (m['bundle_id'], m['math_id']), 'GENERATED_COMPLETE_IDENTITY')
    for item in m['assets']:
        verify_file(inside(p, item['path']), item)
    values = parse_file(p / 'r0/parameters.npz', 'r0')
    require(values['expanded_bytes'] <= 4*1024**2 and
            {k:v for k,v in values.items() if k != 'expanded_bytes'} == m['mathematical']['r0_parameters'], 'GENERATED_PARAMETERS')
    intent = read(p / 'provenance/fit-intent.json')
    receipt = read(p / 'provenance/fit-receipt.json')
    require(digest(intent) == m['provenance']['fit_intent_sha256'] and receipt['status'] == 'COMPLETE' and
            receipt['parameter_sha256'] == values['value_sha256'] and receipt['producer_build'] == m['producer_build']['sha256'] and
            all(receipt[k] == m['provenance'][k] for k in ('job_id', 'attempt_id')), 'GENERATED_FIT_PROVENANCE')
    total = 0
    for f in p.rglob('*'):
        reject_reparse(f)
        if f.is_file():
            require(f.stat().st_size <= 8*1024**2, 'GENERATED_MEMBER_LIMIT')
            total += f.stat().st_size
    require(total <= 16*1024**2, 'GENERATED_BUNDLE_LIMIT')
    return m, mark


@metadata_api
def inspect(directory, *, runtime_inventory=None):
    from .validation import _locator
    p = root(directory)
    m, mark = complete(p)
    return dict(format=m['format'], version=2, mode='fast', bundle_id=m['bundle_id'], math_id=m['math_id'],
                execution_build_id=m['producer_build']['sha256'], persistent_complete=True, assets_verified=True,
                state='PERSISTENT_COMPLETE', source_status=_locator(p, 'source'), reference_status=_locator(p, 'reference'),
                runtime_compatibility=runtime_status(m['runtime_requirements'], runtime_inventory),
                execution_qualification=m['qualification'], resident=False, availability='REPLAY_REQUIRED',
                operation_id=mark['operation_id'], attempt_id=mark['attempt_id'])


@metadata_api
def publish(directory, parameter_file, intent, receipt, context, *, checkpoint=lambda name: None):
    """Use the original target lock, bounded NPZ parser and atomic copy primitive."""
    from .transaction import _copy
    from ..common import canonical_json
    target = root(directory, write=True)
    data = read_bytes(parameter_file, 4*1024**2)
    parsed = parse_npz(data, 'r0')
    require(receipt['parameter_sha256'] == parsed['value_sha256'] and receipt['status'] == 'COMPLETE', 'FIT_RECEIPT_PARAMETERS')
    target.mkdir(parents=True, exist_ok=True)
    control = target / '.pack'
    control.mkdir(exist_ok=True)
    record = dict(operation_id=receipt['job_id'], attempt_id=receipt['attempt_id'])
    payloads = {'r0/parameters.npz': data, 'provenance/fit-intent.json': canonical_json(intent)+b'\n',
                'provenance/fit-receipt.json': canonical_json(receipt)+b'\n'}
    assets = [dict(path=name, bytes=len(value), sha256=__import__('hashlib').sha256(value).hexdigest()) for name,value in sorted(payloads.items())]
    from ..preview.contract import enabled as preview_enabled
    preview = preview_enabled(context)
    math = dict(profile=context['profile'], settings=context['settings'],
                normalization={name:dict(dtype=context[name+'_identity']['dtype'],
                     denominator=None if context['TEST_ONLY'] else (2**context[name+'_identity']['bit_depth']-1 if preview else 255),
                     dtype_after='float32', resize='INTER_AREA', quantization='none') for name in ('source','reference')},
                inputs={name+'_identity': context[name+'_identity'] for name in ('source','reference')},
                color_policy=context['color_policy'], color_declaration='gamma-encoded-sRGB-float32',
                r0_parameters={k:v for k,v in parsed.items() if k != 'expanded_bytes'}, math_files=math_closure('fast'))
    manifest = dict(format='grademirror.cpu_recipe_bundle', version=2, mode='fast', mathematical=math, math_id=digest(math),
                    runtime_requirements=context['runtime'], producer_build=context['executor_build'],
                    qualification=dict(status='CPU_FIT_GENERATED_PENDING_SCOPED_QUALIFICATION', execution_qualified=False,
                       general_input_qualified=False, CPU_ENABLED=False, AUTO_ENABLED=False, PRODUCTION_ENABLED=False,
                       D01_extended=False, user_visual='PENDING'), assets=assets,
                    provenance=dict(fit_intent_sha256=digest(intent), job_id=receipt['job_id'], attempt_id=receipt['attempt_id'],
                                    producer='gm065-preview-original-cpu-fit' if preview else 'original_cpu_fit_loop',
                                    TEST_ONLY=context['TEST_ONLY']))
    manifest['bundle_id'] = digest(manifest)
    with locked(control / 'target.lock', timeout=60):
        if (target / 'COMPLETE.json').is_file():
            old, _ = complete(target)
            require(old == manifest, 'GENERATED_TARGET_CONFLICT')
            atomic_json(control / 'record.json', dict(record, state='SUCCEEDED', recovered_from_complete=True))
            return inspect(target)
        require(not any(f.name not in ('.pack', 'r0', 'provenance', 'manifest.json') for f in target.iterdir()), 'GENERATED_UNOWNED_TARGET')
        rp = control / 'record.json'
        if rp.is_file():
            prior = read(rp)
            require(prior['intent'] == digest(intent), 'GENERATED_INTENT_CONFLICT')
        atomic_json(rp, dict(record, state='RUNNING', intent=digest(intent)))
        stage = control / 'staging' / str(uuid.uuid4())
        stage.mkdir(parents=True)
        try:
            for item in assets:
                dest = inside(target, item['path'])
                if dest.exists():
                    verify_file(dest, item)
                    continue
                staged = inside(stage, item['path'])
                _copy(payloads[item['path']], staged, record, control)
                verify_file(staged, item)
                dest.parent.mkdir(parents=True, exist_ok=True)
                __import__('os').rename(staged, dest)
            if (target / 'manifest.json').exists():
                require(read(target / 'manifest.json') == manifest, 'GENERATED_MANIFEST_CONFLICT')
            else:
                new_json(target / 'manifest.json', manifest)
            checkpoint('bundle_before_complete')
            atomic_json(target / 'COMPLETE.json', dict(format='cpu-recipe-complete-v2', bundle_id=manifest['bundle_id'],
                math_id=manifest['math_id'], manifest=file_record(target / 'manifest.json', name='manifest.json'),
                operation_id=receipt['job_id'], attempt_id=receipt['attempt_id']))
            checkpoint('bundle_after_complete')
            complete(target)
            atomic_json(rp, dict(record, state='SUCCEEDED', intent=digest(intent)))
            return inspect(target)
        except BaseException as error:
            failure = control / 'failures' / (str(uuid.uuid4())+'.json')
            atomic_json(failure, dict(record, state='FAILED', error=repr(error), complete_exists=(target/'COMPLETE.json').exists()))
            raise
