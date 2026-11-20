"""Private bounded float64 frames for the short-lived P3 solver only."""
import math
from .gm003_ipc import MAX_FRAME,send_json,recv_json
from .image_limits import MAX_PIXELS
MAX_ARRAY=MAX_PIXELS*3*8
MAX_TOTAL=2*MAX_ARRAY

def _shape(shape,max_bytes):
    if not isinstance(shape,list)or len(shape)not in (2,3)or any(type(n)is not int or n<1 for n in shape)or shape[-1]!=3:
        raise ValueError('P3_IPC_SHAPE')
    size=math.prod(shape)*8
    if size>min(max_bytes,MAX_ARRAY):raise ValueError('P3_IPC_QUOTA')
    return size

def send_f64(conn,array,*,max_bytes=MAX_TOTAL):
    import numpy as np
    if not isinstance(array,np.ndarray)or array.dtype.str!='<f8'or not array.flags.c_contiguous:raise ValueError('P3_IPC_DTYPE')
    n=_shape(list(array.shape),max_bytes);send_json(conn,dict(kind='p3-f64',shape=list(array.shape),nbytes=n))
    view=memoryview(array).cast('B')
    for offset in range(0,n,MAX_FRAME):conn.send_bytes(view[offset:min(n,offset+MAX_FRAME)])
    send_json(conn,dict(end=True))

def recv_f64(conn,shape,*,max_bytes=MAX_TOTAL):
    import numpy as np
    n=_shape(list(shape),max_bytes);header=recv_json(conn)
    if header!={'kind':'p3-f64','shape':list(shape),'nbytes':n}:raise ValueError('P3_IPC_IDENTITY')
    out=np.empty(shape,dtype='<f8');view=memoryview(out).cast('B')
    for offset in range(0,n,MAX_FRAME):
        size=min(MAX_FRAME,n-offset);data=conn.recv_bytes(maxlength=MAX_FRAME)
        if len(data)!=size:raise ValueError('P3_IPC_FRAME')
        view[offset:offset+size]=data
    if recv_json(conn)!={'end':True}or not np.isfinite(out).all():raise ValueError('P3_IPC_FINITE_END')
    return out
