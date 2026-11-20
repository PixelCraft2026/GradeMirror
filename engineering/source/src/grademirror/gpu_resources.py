"""Deterministic VRAM envelopes from actual compute geometry, no numeric imports."""
from .resource_policy import VERSION,GPU_MARGIN_BYTES,operation_name
from .image_limits import check_geometry,MIB

def rounded(value,step):return ((value+step-1)//step)*step

def render_span(length):
    # An interior 512-pixel core has one halo pixel on either side.
    return length if length<=512 else min(length,513)if length<=1024 else 514

def fast_plan(width,height,*,operation='fit'):
    check_geometry(width,height);operation_name(operation)
    h=max(1,round(height*512/max(width,height)));w=max(1,round(width*512/max(width,height)))
    rh,rw=render_span(height),render_span(width)
    render=rounded(256*MIB+1536*rh*rw,128*MIB)
    training=rounded(896*MIB+1536*h*w,128*MIB)if operation=='fit'else 0
    reserved=max(render,training)
    return dict(reserved_bytes=reserved,required_free_bytes=reserved+GPU_MARGIN_BYTES,
        render_working_bytes=render,training_working_bytes=training,
        training_hw=[h,w]if operation=='fit'else None,maximum_render_hw=[rh,rw],
        resource_policy_version=VERSION)

def resource_geometry(source,*,operation='fit',fine=False):
    operation_name(operation);w,h=source['width'],source['height'];check_geometry(w,h)
    if type(fine)is not bool:raise ValueError('GPU_PROFILE_INVALID')
    if fine:
        from .fine_resources import gpu_plan
        plan=gpu_plan(w*h,native_wh=[w,h])
    else:plan=fast_plan(w,h,operation=operation)
    from .light.resources import scoped_gpu_plan
    plan=scoped_gpu_plan(source,plan,fine=fine,operation=operation)
    return dict(kind='fine'if fine else'fast',operation=operation,source_hw=[h,w],plan=plan)

def validate_resource(value,*,fine=None,operation=None):
    if not isinstance(value,dict)or set(value)!={'kind','operation','source_hw','plan'}:raise ValueError('GPU_RESOURCE_PLAN_INVALID')
    hw=value['source_hw']
    if value['kind']not in('fine','fast')or not isinstance(hw,list)or len(hw)!=2:raise ValueError('GPU_RESOURCE_PLAN_INVALID')
    actual_fine=value['kind']=='fine'
    if fine is not None and actual_fine!=fine:raise ValueError('GPU_RESOURCE_PROFILE_MISMATCH')
    if operation is not None and value['operation']!=operation:raise ValueError('GPU_RESOURCE_OPERATION_MISMATCH')
    expected=resource_geometry(dict(height=hw[0],width=hw[1]),operation=value['operation'],fine=actual_fine)
    if value!=expected:raise ValueError('GPU_RESOURCE_PLAN_MISMATCH')
    return expected['plan']
