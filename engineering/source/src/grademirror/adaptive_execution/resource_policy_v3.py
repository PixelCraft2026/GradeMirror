"""GM062 allocation accounting, not a new numerical execution credential.

The conservative candidate is explicitly unqualified. In particular, a host
with more RAM does not turn an unresolved backend workspace into a certificate.
Old resource_policy_v2 and its callers retain their original defaults.
"""
import math
import time
from .intent import normalize, digest, need
from .resource_policy_v2 import checked

G = 1024**3
M = 1024**2
TARGET = 2_500_000_000
POLICY = 'resource-policy-v3'


def margin(peak):
    need(type(peak) is int and peak >= 0, 'RESOURCE_V3_PEAK_UNKNOWN')
    return max(G, (peak + 4)//5)


def allocation_model(request):
    """Byte arithmetic only. No model, codec, NumPy or tensor imports.

    Allowances are declared engineering envelopes, not measured upper bounds.
    Backend/allocator envelopes have not been certified: this is why execution
    remains disabled even if a larger machine satisfies the advisory estimate.
    """
    r = normalize(request)
    meta = r['source']['metadata']; w, h = meta['width'], meta['height']
    pixels = w*h; s = pixels*3*(meta['bits']//8); o = pixels*3*4
    tile = min(w, 514)*min(h, 514)
    # Frozen recipe: RGB32, XY32, context64; extra RGB/XY depth2.
    # During decode_features/sin: color32+position32+hidden64+context64
    # plus two simultaneous64-channel expression temporaries (320 channels).
    field = tile*320*4
    rgb_xy_lab = tile*3*4*16 + tile*2*4
    stages = []

    def add(name, allocations, commit_extra=0, status='MODELED_UNCERTIFIED'):
        physical = sum(x['physical_bytes'] for x in allocations)
        commit = sum(x['commit_bytes'] for x in allocations) + commit_extra
        stages.append(dict(stage=name, allocations=allocations,
            incremental_physical_bytes=physical, incremental_commit_bytes=commit,
            required_available_physical_bytes=physical+margin(physical),
            required_available_commit_bytes=commit+margin(commit),
            physical_margin_bytes=margin(physical), commit_margin_bytes=margin(commit),
            status=status))

    def a(name, physical, commit=None, basis='source allocation'):
        return dict(name=name, physical_bytes=physical,
                    commit_bytes=physical if commit is None else commit, basis=basis)

    controls = a('future owner/control processes', 64*M, 128*M, 'declared envelope; caller excluded')
    host = a('host interpreter, metadata and codec overhead', 256*M, 384*M, 'declared envelope; excludes explicitly listed image buffers')
    maps = a('unique S and O pages (fast O aliases Y)', s+o, basis='Host.allocate; counted once across all views')
    add('import_decode', [controls, host, maps,
        a('Pillow decoded RGB storage', pixels*4),
        a('source uint array and conversion copy', 2*s)], 64*M)
    add('R0', [controls, host, maps,
        a('numeric runtime/import envelope', 512*M, G, 'import-only calibration plus explicit reserve, not certified upper bound'),
        a('live frozen Field expressions', field),
        a('tile RGB/XY/Lab temporaries', rgb_xy_lab),
        a('allocator retention and backend workspace envelope', 576*M, 768*M,
          'retained allocations/workspace not bounded by Python lifetimes; not a numerical buffer rewrite')])
    if r['quality'] == 'fine':
        from ..engine.cpu_postprocess.geometry import plan, workspace_bound
        stripes=plan(h,w,256)
        workspace=max(workspace_bound(x)['total_bytes'] for x in stripes)
        source_scalar=max(x.base_rows*w*4 for x in stripes)
        add('CPU256_receive', [controls, host, maps,
            a('receiver final Y while O remains live', o),
            a('numeric runtime/import envelope', 512*M, G, 'declared envelope'),
            a('CPU256 workspace including four proof fields', workspace,
              basis='unchanged geometry.workspace_bound over all global stripes;512MiB hard cap remains'),
            a('provider scalar source copy', source_scalar),
            a('CPU allocator retained blocks outside live workspace', 512*M, 768*M,
              'unqualified allowance distinct from live512MiB workspace; not a larger workspace grant'),
            a('receiver single borrowed transport field', min(h,256)*w*3*4)])
    add('resident', [controls, host, maps])
    add('preview', [controls, host, maps, a('bounded preview/codec envelope', 64*M)])
    add('export_encode', [controls, host, maps,
        a('integer quantized RGB8', pixels*3), a('Pillow RGB encoder storage', pixels*4),
        a('metadata/JPEG buffer envelope', 128*M)])
    add('export_readback', [controls, host, maps,
        a('Pillow decoded RGB storage', pixels*4), a('readback tobytes', pixels*3),
        a('metadata/JPEG buffer envelope', 128*M)])
    # Original exporter deletes quantized before full readback: no invented
    # full-float blend or simultaneous quantized/readback allocation.
    peak = max(x['incremental_physical_bytes'] for x in stages)
    commit = max(x['incremental_commit_bytes'] for x in stages)
    result = dict(schema='cpu-allocation-model-v3', policy=POLICY,
        shape=[h,w,3], source_bits=meta['bits'], quality=r['quality'], operation=r['operation'],
        tile_side=512, core_rows=256, threads=4, interop_threads=1,
        source_bytes=s, float_output_bytes=o, live_field_expression_bytes=field,
        stages=stages, incremental_physical_bytes=peak, incremental_commit_bytes=commit,
        required_available_physical_bytes=peak+margin(peak),
        required_available_commit_bytes=commit+margin(commit),
        target_available_physical_bytes=TARGET,
        target_incremental_physical_budget_bytes=TARGET-G,
        target_physical_gap_bytes=max(0,peak+margin(peak)-TARGET),
        target_commit_ceiling_bytes=8*G, target_fits=peak+margin(peak)<=TARGET and commit+margin(commit)<=8*G,
        certified=False, unresolved=['NATIVE_ALLOCATOR_RETENTION_UPPER_BOUND', 'BACKEND_WORKSPACE_UPPER_BOUND'],
        shared_pages_counted_once=True, caller_already_in_os_available=True,
        raw_tree_rss_is_not_unique_physical=True,
        execution_qualified=False, estimates_only=True)
    result['model_id']=digest(result)
    return result


def evaluate(request, snapshot, stage='plan', *, now=None, model=None):
    r=normalize(request); s=checked(snapshot); expected=allocation_model(r)
    need(model is None or model==expected, 'RESOURCE_V3_MODEL_CHANGED')
    m=expected
    if stage in ('plan','submit','frame_start','native_start','worker_start','before_control_worker','before_host'):
        p,c=m['incremental_physical_bytes'],m['incremental_commit_bytes']
    else:
        rows=[x for x in m['stages'] if x['stage']==stage]
        need(len(rows)==1,'RESOURCE_V3_STAGE_UNKNOWN',stage)
        p,c=rows[0]['incremental_physical_bytes'],rows[0]['incremental_commit_bytes']
    reasons=[];unknown=[]
    need(type(s['complete']) is bool,'RESOURCE_V3_SNAPSHOT_COMPLETE_INVALID')
    if not s['complete']:unknown.append('SNAPSHOT_INCOMPLETE')
    if not -1 <= (time.time() if now is None else now)-s['captured_at'] <=60:unknown.append('SNAPSHOT_STALE')
    for name,available,required in (
        ('PHYSICAL',s['physical']['available_bytes'],p+margin(p)),
        ('SYSTEM_COMMIT',s['commit']['headroom_bytes'],c+margin(c)),
        ('LEGACY_PROCESS_COMMIT',s['legacy_process_commit_available'],c+margin(c))):
        if available is None:unknown.append(name+'_UNKNOWN')
        elif available<required:reasons.append(name+'_BELOW_PREDICTED_DEMAND')
    if s['pool']['state']=='UNKNOWN' or s['pool']['exited'] is None:unknown.append('POOL_USAGE_UNKNOWN')
    elif s['pool']['state']!='FREE' and r['operation'] not in ('resident_preview','export'):reasons.append('POOL_NOT_FREE')
    if s['product'].get('coverage')!='caller_and_all_known_workspace_processes':unknown.append('TOTAL_PRODUCT_COVERAGE_UNKNOWN')
    for limit,caller,pool,future in (('ram_bytes','rss_bytes','resident_rss_bytes',p),('commit_bytes','private_bytes','resident_commit_bytes',c)):
        cap=r['limits'][limit];a=s['product'][caller];b=s['pool'][pool]
        if cap is not None:
            if a is None or b is None:unknown.append('EXISTING_'+limit.upper()+'_UNKNOWN')
            elif a+b+future>cap:reasons.append('USER_'+limit.upper()+'_TOO_SMALL')
    if r['limits']['vram_bytes'] is not None and s['gpu']['status'] not in ('ABSENT','KNOWN_CPU_NO_GPU_ALLOCATION'):unknown.append('EXISTING_VRAM_USAGE_UNKNOWN')
    feasible=False if reasons else None if unknown else True
    return dict(policy=POLICY,stage=stage,model_id=m['model_id'],resource_feasible=feasible,
        resource_reasons=reasons+unknown,minimum_available_ram=p+margin(p),
        minimum_available_commit=c+margin(c),additional_peak_bytes=p,
        additional_commit_bytes=c,physical_margin_bytes=margin(p),commit_margin_bytes=margin(c),
        target_fits=m['target_fits'],model_certified=m['certified'],execution_authorized=False,
        allocation_basis='cold scenario envelope; no live-allocation credits certified',
        message='低内存模型尚未获得执行资格；'+(';'.join(reasons+unknown) or '当前读数满足估算，不代表实测资格'),
        snapshot=s,total_limit_includes_existing=True)
