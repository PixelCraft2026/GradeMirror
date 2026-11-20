"""Fixed GM063 qualification hashes using the original mix_quantize primitive."""
import hashlib,time
from pathlib import Path
from ..common import atomic_json
from ..cpu_replay.contract import need
from .calibration_launch import config


def respond(host,conn,request):
    from ..gm003_ipc import send_json
    c=config();need(c is not None and c['backend']=='G','GM063_QUALIFICATION_SCOPE')
    need(host.spec['source_sha256']==c['source']['sha256'] and list(host.source.shape)==[6000,4000,3],'GM063_QUALIFICATION_INPUT')
    host._require_lease(request);out=host.session_dir/'gm063-quantization.json';need(not out.exists(),'GM063_QUANTIZATION_REUSED')
    from ..export_pixels import mix_quantize
    fine=host.spec['profile_id']=='independent-r0-p3-lld10-v1';bits_set=(8,16)if fine else(8,)
    atomic_json(host.session_dir/'gm063-quantization-reserve.json',dict(unit=c['unit'],count=len(bits_set)*3,before_call=True))
    started=time.perf_counter();quant={}
    for bits in bits_set:
        for alpha in(0.,.5,1.):
            h=hashlib.sha256()
            for a in range(0,len(host.y),64):
                value=mix_quantize(host.source[a:a+64],host.y[a:a+64],alpha,bits=bits);h.update(memoryview(value).cast('B'));del value
            quant[str(bits)+':'+str(alpha)]=h.hexdigest()
    result=dict(quantization=quant,seconds=time.perf_counter()-started,fit_count=host.fit_count,render_count=host.render_count)
    atomic_json(out,result);send_json(conn,dict(ok=True,**result));return True
