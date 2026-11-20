"""Finite manual capabilities and byte-bound policy; no new qualification."""
from pathlib import Path
from ..common import sha_file
from ..engine import profiles
from ..engine.independent_r0 import profile as fast
from ..engine.independent_r0_p3_lld10 import profile as fine
from ..resource_policy import VERSION, GPU_MARGIN_BYTES, ALLOCATOR_FRACTION
from .contract import digest

POLICY = 'gm022-advisory-v1'
ROOT = Path(__file__).resolve().parents[3]

def registry():
    rows=[]
    for mode,module in (('fast',fast),('fine',fine)):
        rows.append(dict(id='cuda-'+mode,status='EXISTING_MANUAL_BASELINE',automatic_eligible=False,
            operations=['fit','replay'],profile_id=module.PROFILE_ID,profile_contract_sha256=module.PROFILE_CONTRACT_SHA256,
            core_sha256=module.CORE_SHA256,runtime=module.RUNTIME,geometry_bits='Existing RGB8/16 native bounds; fine calibrated and legacy envelopes distinct',
            tile_side=512,halo='one pixel for interior R0 render',threads=4,cancellation='Existing worker/tile boundaries',
            estimated_seconds=None,time_contract='Existing operation deadline, not ETA',production_qualified=False,
            evidence=['docs/results/GM-014-product-review-20261022-v1.md']))
    rows += [dict(id='resident-export',status='EXISTING_RESIDENT_EXPORT',automatic_eligible=False,
        operations=['export'],runtime='existing source-format host',geometry_bits='Existing RGB8/16 source format',
        tile_side=None,threads=4,cancellation='Existing host export boundaries',estimated_seconds=None,
        time_contract='Existing export deadline, not ETA',production_qualified=False,evidence=['docs/results/GM-021-product-review-20261023-v1.md'])]
    for name,status,gap in (('cuda-r0-cpu-lld10-v1','SCOPED_ENGINEERING_ONLY','Only fixed manual saved-recipe replay; no first fit chain or general CPU qualification'),
                            ('cpu-r0','UNAVAILABLE_OR_UNQUALIFIED','No qualified CPU R0 fit/render'),
                            ('gpu-stripes','UNAVAILABLE_OR_UNQUALIFIED','No qualified alternate blocks or threads')):
        rows.append(dict(id=name,status=status,automatic_eligible=False,operations=['replay']if name=='cuda-r0-cpu-lld10-v1'else[],
            core_rows=512 if name=='cuda-r0-cpu-lld10-v1'else None,tile_side=512,threads=4,
            runtime=dict(numpy='1.26.4',torch='2.5.1+cu124',interop=1),geometry_bits='Fixed GM020/021 RGB8 single-image evidence only',
            cancellation='Existing bounded block and exact owned exit',estimated_seconds=None,time_contract='Frozen historical deadlines, not ETA',
            gap=gap,production_qualified=False,evidence=['docs/results/GM-020-product-review-20261023-v1.md','docs/results/GM-021-R3-product-review-20261023-v1.md']))
    from ..hybrid_replay.contract import execution_contract, cpu_bindings
    for row in rows:
        row['policy_version']=POLICY
        if row['id']=='cuda-fine':row['compatible_contracts']=fine.COMPATIBLE_CONTRACTS
        from ..light.portable import source_sha
        row['implementation_binding']=row.get('core_sha256') or digest({n:source_sha(ROOT/'src/grademirror'/n) for n in ('gm004a_export.py','export_pixels.py')})
        if row['id']=='cuda-r0-cpu-lld10-v1':
            row['implementation_binding']=execution_contract()[1];row['cpu_bindings']=cpu_bindings()
    for row in rows:
        if row['id']=='cuda-r0-cpu-lld10-v1':
            row.update(status='SCOPED_FIT_AND_REPLAY_EVIDENCE',operations=['fit','replay'],
                gap='Fixed GM-027/028 photo pair, device, runtime and CPU512 only; no general CPU or new-policy execution qualification',
                geometry_bits='Exact GM-027/028 RGB8 photo identities and geometry; current core_rows=512',
                evidence=['docs/results/GM-027-product-review-20261026-v1.md',
                          'docs/results/GM-028-product-review-20261026-v1.md'])
    return rows

def registry_v2():
    from .qualification import registration
    rows=registry()
    for row in rows:
        if row['id']=='cuda-r0-cpu-lld10-v1':row['qualification_scopes']=registration()
    rows.append(dict(id='cpu256',status='HISTORICAL_NUMERICAL_EVIDENCE_ONLY_CURRENT_WIRING_FIXED_512',
        operations=[],core_rows=256,advisory_eligible=False,automatic_eligible=False,
        production_qualified=False,evidence=['docs/results/GM-020-product-review-20261023-v1.md']))
    return rows

def registry_v3():
    from .qualification import registration_v3
    rows=registry_v2()
    for row in rows:
        if row['id']=='cuda-r0-cpu-lld10-v1':
            row.update(status='FIXED_PAIR_NATIVE512_REPLAY_ACCEPTED',
                qualification_scopes=registration_v3(),
                evidence=row['evidence']+['docs/results/GM-041-product-review-20261029-v1.md'],
                gap='C14 exact replay scope only; current fit, other pairs, CPU256 and new-policy execution unqualified')
    return rows

def binding():
    from ..light.portable import source_files, source_sha
    files=source_files(Path(__file__).parent, '*.py')
    files += [ROOT/'src/grademirror'/name for name in ('resource_policy.py','gpu_resources.py','fine_resources.py','image_limits.py','fine_contract.py','engine/profiles.py','engine/independent_r0/profile.py','engine/independent_r0_p3_lld10/profile.py')]
    implementation={p.relative_to(ROOT).as_posix():source_sha(p) for p in sorted(files)}
    policy=dict(version=POLICY,existing_resource_policy=VERSION,gpu_margin_bytes=GPU_MARGIN_BYTES,allocator_fraction=ALLOCATOR_FRACTION,
                fine_physical_margin_bytes=1024**3,freshness_seconds=5,future_tolerance_seconds=1,
                production_qualified=False,automatic_selection_enabled=False,implementation=implementation)
    return dict(policy=policy,policy_digest=digest(policy),implementation_digest=digest(implementation),registry_digest=digest(registry()))
