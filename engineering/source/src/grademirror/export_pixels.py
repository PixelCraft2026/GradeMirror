"""Exact 64-row export mixing into one final integer image."""
import hashlib,math
import numpy as np
from .engine import group15_codec as codec

def mix_quantize(source,result,alpha,*,bits=None,check=None,observe=None,stats=None,auxiliary_bits=None):
    if type(alpha)not in(float,int)or not math.isfinite(alpha)or not 0<=alpha<=1:raise ValueError('ALPHA_INVALID')
    if (not isinstance(source,np.ndarray)or source.dtype not in(np.uint8,np.uint16)or source.ndim!=3 or source.shape[-1]!=3 or min(source.shape)<1):raise ValueError('EXPORT_SOURCE_INVALID')
    if not isinstance(result,np.ndarray)or result.dtype!=np.float32 or result.shape!=source.shape:raise ValueError('EXPORT_RESULT_INVALID')
    bits=source.dtype.itemsize*8 if bits is None else bits
    if type(bits)is not int or bits not in(8,16):raise ValueError('EXPORT_BITS_INVALID')
    if auxiliary_bits is not None and (auxiliary_bits!=16 or bits!=8 or source.dtype!=np.uint16):raise ValueError('EXPORT_AUXILIARY_INVALID')
    q=np.empty(source.shape,dtype=np.uint8 if bits==8 else np.uint16);quantize=codec.quantize8 if bits==8 else codec.quantize16
    summary=stats if stats is not None else {};summary.update(block_rows=64,blocks=0,rows=0,target_bits=bits,full_target_arrays=1,full_float_blend_arrays=0,max_float_block_bytes=0,max_float_block_shape=None,quantize8_blocks=0,quantize16_blocks=0,auxiliary_sha256=None)
    auxiliary=hashlib.sha256()if auxiliary_bits is not None else None
    # Keep every operation in float32 and in the original order. Reuse two
    # bounded scratch buffers instead of allocating the expression temporaries
    # on every strip. Quantization still uses the unchanged codec function.
    shape=(min(64,len(source)),*source.shape[1:])
    source_scratch=np.empty(shape,dtype=np.float32)
    mixed_scratch=np.empty(shape,dtype=np.float32)
    denominator=np.float32(np.iinfo(source.dtype).max);strength=np.float32(alpha)
    quantize_counter='quantize'+str(bits)+'_blocks'
    summary.update(scratch_float32_arrays=2,scratch_float32_bytes=source_scratch.nbytes+mixed_scratch.nbytes)
    if observe:observe('Q',q)
    for row in range(0,len(source),64):
        if check:check()
        region=slice(row,row+64)
        rows=len(source[region]);s=source_scratch[:rows];mixed32=mixed_scratch[:rows]
        np.copyto(s,source[region],casting='unsafe')
        np.divide(s,denominator,out=s)
        np.subtract(result[region],s,out=mixed32)
        np.multiply(mixed32,strength,out=mixed32)
        np.add(s,mixed32,out=mixed32)
        if observe:observe('mixed_block',mixed32)
        q[region]=quantize(mixed32)
        summary[quantize_counter]+=1
        if auxiliary is not None:
            auxiliary.update(memoryview(codec.quantize16(mixed32)).cast('B'));summary['quantize16_blocks']+=1
        summary['blocks']+=1;summary['rows']+=len(mixed32)
        if mixed32.nbytes>summary['max_float_block_bytes']:summary.update(max_float_block_bytes=mixed32.nbytes,max_float_block_shape=list(mixed32.shape))
        del s,mixed32
    if check:check()
    if auxiliary is not None:summary['auxiliary_sha256']=auxiliary.hexdigest()
    return q
