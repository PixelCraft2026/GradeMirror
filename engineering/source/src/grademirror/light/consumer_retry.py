"""Keep the current lease through a short, known pre-BLEND memory shortage."""
import time

from ..common import GMError,atomic_json


def initial_blend(send,path,session,request,*,deadline,cancelled,report,clock=time.monotonic,pause=time.sleep):
    retry_until=min(deadline,clock()+30.)
    attempts=0
    while True:
        if cancelled():raise GMError("CANCELLED","Cancelled while waiting to publish the resident result",8)
        attempts+=1
        try:
            result=send(session,request,deadline=deadline,cancelled=cancelled)
            report("publish")
            return result
        except GMError as exc:
            boundary=getattr(exc,"resource_boundary",None)
            if (exc.code!="PREVIEW_MEMORY_NOT_READY" or not isinstance(boundary,dict)
                    or boundary.get("stage")!="blend" or boundary.get("point")!="resident-blend"):
                raise
            available=boundary.get("available",{})
            required=boundary.get("required",{})
            fields=("ram","commit")
            if (not all(type(available.get(k)) is int and type(required.get(k)) is int for k in fields)
                    or not any(available[k]<required[k] for k in fields)):
                raise
            try:
                atomic_json(path/"pending-first-blend.json",dict(attempts=attempts,reason=exc.code,
                    resource_boundary=boundary,lease=request["handle_id"],deadline=retry_until,retry_scope="same_resident_lease"))
            except OSError as error:
                exc.add_note("BLEND retry receipt: "+str(error))
            left=retry_until-clock()
            if left<=0 or attempts>=7:raise
            report("gpu_wait")
            pause(min(left,5.))

