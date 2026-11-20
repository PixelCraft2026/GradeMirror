"""Fine envelopes and explicit phase memory estimates, standard library only."""
import math
from .image_limits import photo_budget,require_memory,GIB,MIB
from .fine_contract import validate_limits,coefficient_shape
from .common import GMError
from .resource_policy import VERSION,operation_name

def solver_memory_bytes(native_wh,reference_wh):
    w,h=native_wh;rw,rh=reference_wh;r=rw*rh
    k=coefficient_shape([rh,rw,3])[0]
    # Include Python axis/edge construction as well as sparse payloads.
    return 512*MIB+384*r+48*k+256*(w+h+rw+rh)

def solver_job_bytes(native_wh,reference_wh):
    return max(3*GIB,math.ceil((solver_memory_bytes(native_wh,reference_wh)+256*MIB)/(256*MIB))*256*MIB)

def solver_seconds(reference_pixels):
    return min(7200,max(1000,math.ceil(reference_pixels/2000000*1000)))

def gpu_plan(pixels,*,native_wh=None):
    if type(pixels)is not int or not 0<pixels<=210000000:raise ValueError('GPU_PIXELS_INVALID')
    reserved=max(3*GIB,math.ceil((64*pixels+512*MIB)/(256*MIB))*256*MIB)
    if native_wh is not None:
        if (not isinstance(native_wh,list)or len(native_wh)!=2 or any(type(n)is not int or n<1 for n in native_wh)or native_wh[0]*native_wh[1]!=pixels):raise ValueError('GPU_GEOMETRY_INVALID')
        if min(native_wh)>=512 and max(native_wh)<=16384 and pixels<=50118276:
            reserved=max(2*GIB,math.ceil((48*pixels+256*MIB)/(256*MIB))*256*MIB)
    return dict(reserved_bytes=reserved,required_free_bytes=reserved+512*MIB,
                rgb_seconds=max(900,math.ceil(pixels/50118276*900)))

def _legacy_memory_plan(source,reference,*,operation='fit'):
    operation_name(operation)
    p=source['width']*source['height'];r=reference['width']*reference['height'];k=coefficient_shape([reference['height'],reference['width'],3])[0]
    integer_inputs=p*3*source['bits']//8+r*3*reference['bits']//8
    reference_integer=r*3*reference['bits']//8
    # Conservative working allowances, explicitly separate from hard Job caps.
    # Solver sparse construction <=9 A entries/reference pixel plus temporary
    # COO/CSR, Q/D/vectors; no dense N*K or full-native basis.
    solver=solver_memory_bytes((source['width'],source['height']),(reference['width'],reference['height']))
    parent_solver=3*GIB+reference_integer+12*p+48*r
    post=3*GIB+reference_integer+36*p
    # Three main RGB buffers. Scalar input/D2H, finite masks and the original
    # FP64 expression are separate transient capacities. On extreme wide
    # shapes a 64-row block can exceed the fixed allocator allowance: retain
    # the old envelope and add an explicit capacity floor, never undercount it.
    transient=max(12*p,3*p,96*source['width']*min(source['height'],64))
    known_post=reference_integer+36*p+transient+24*k
    if known_post>post:post=max(3*GIB+integer_inputs+48*p,3*GIB+known_post)
    host=512*MIB+40*p+3*r*3*reference['bits']//8
    control=128*MIB
    stages={'R0_fit_render':3*GIB+integer_inputs+24*p,'P3_solve_parent_and_child':parent_solver+solver,'LLF_composition':post}
    if operation=='replay':
        source_integer=p*3*source['bits']//8
        stages={'R0_replay_render':stages['R0_fit_render']+24*k,
                'recipe_load':3*GIB+source_integer+96*k+4*MIB,
                'LLF_composition':post}
        solver=0
    peak=max(stages.values())+host+control
    return dict(operation=operation,resource_policy_version=VERSION,numeric_stages_bytes=stages,solver_child_working_bytes=solver,host_active_estimate_bytes=host,control_active_estimate_bytes=control,simultaneous_peak_bytes=peak,required_physical_bytes=peak+GIB,physical_margin_bytes=GIB,postprocess_known_capacity_floor_bytes=known_post,postprocess_transient_capacity_bytes=transient,assumption='Runtime/allocator allowance3GiB; three main RGB buffers with explicit extreme-shape transient floor; bounded sparse COO/CSR and axis construction; size-derived Jobs are ceilings, not physical preallocations')

def memory_plan(source,reference,*,operation='fit'):
    plan=_legacy_memory_plan(source,reference,operation=operation)
    sw,sh=source['width'],source['height'];rw,rh=reference['width'],reference['height']
    calibrated=(min(sw,sh)>=512 and max(sw,sh)<=16384 and sw*sh<=33554432
                and min(rw,rh)>=512 and max(rw,rh)<=4096 and rw*rh<=1572864
                and source['bits']in(8,16)and reference['bits']in(8,16))
    plan['ram_policy_version']='fine-ram-calibrated-v1'if calibrated else'fine-ram-legacy-v1'
    plan['calibrated_geometry']=calibrated
    if not calibrated:return plan
    stages={name:value-1536*MIB for name,value in plan['numeric_stages_bytes'].items()}
    stages['LLF_composition']=max(stages['LLF_composition'],plan['postprocess_known_capacity_floor_bytes'])
    peak=max(stages.values())+plan['host_active_estimate_bytes']+plan['control_active_estimate_bytes']
    required=math.ceil((peak+GIB)/(256*MIB))*256*MIB
    plan.update(numeric_stages_bytes=stages,simultaneous_peak_bytes=peak,required_physical_bytes=required,
                physical_rounding_bytes=required-peak-GIB,runtime_allocator_allowance_bytes=1536*MIB,
                assumption='Calibrated domain only: runtime/allocator allowance1.5GiB; distinct fit/replay stages; original host/control and1GiB physical margin;256MiB rounding; legacy hard Jobs retained')
    return plan

def budget(source,reference,*,admit=False,operation='fit'):
    operation_name(operation)
    validate_limits(source,reference);out=photo_budget(source,reference);plan=memory_plan(source,reference,operation=operation)
    job_plan=_legacy_memory_plan(source,reference,operation='fit')
    reference_bytes=reference['width']*reference['height']*3*reference['bits']//8
    host_needed=512*MIB+64*source['width']*source['height']+3*reference_bytes
    out['host']=max(out['host'],math.ceil(host_needed/(256*MIB))*256*MIB)
    out['compute']=max(8*GIB,math.ceil((max(job_plan['numeric_stages_bytes'].values())+512*MIB)/(256*MIB))*256*MIB)
    out['pool']=sum(out[k]for k in ('compute','host','control_ipc'))
    if out['pool']>256*GIB:raise GMError('FINE_RAM_ENVELOPE_UNSUPPORTED','此尺寸精细处理的内存预算超过 256 GiB 上限',5)
    if admit:require_memory(plan['required_physical_bytes'])
    return out

def operation_seconds(pixels,reference_pixels=0):
    return min(21600,max(1200,math.ceil(pixels/(4160*6240)*1200))+math.ceil(max(0,reference_pixels-2000000)/2000000*60))
