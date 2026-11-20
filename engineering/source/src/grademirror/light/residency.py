"""GM066 preview lease authority. Existing result and release identities remain immutable."""
import time
from pathlib import Path

from ..adaptive_execution.residency import lock
from ..common import atomic_json, strict_load
from ..cpu_replay.contract import need
from ..cpu_replay.pool import PortableAdmission
from ..winproc import identity_alive, process_identity


def renew(host, request, peer):
    c=host.context
    need(c and c.get("light_plan") and c.get("TEST_ONLY") is False and
         set(request)=={"command","binding"} and request["command"]=="KEEP_ALIVE",
         "LIGHT_RENEW_SCOPE")
    client=c["client"]
    need(peer==client["pid"] and process_identity(peer)==client,"LIGHT_RENEW_CLIENT")
    with lock(host.session):
        current=strict_load(host.session/"lease.json")
        now=time.time()
        need(current["state"]=="RESIDENT" and current["binding"]==request["binding"]==host.binding,
             "RESULT_NOT_RESIDENT",exit_code=4)
        need(now<current["expires_at"] and not (host.session/"stop.signal").exists()
             and not (host.path/"cancel.signal").exists(),"RESULT_LEASE_EXPIRED",exit_code=4)
        need(all(identity_alive(host.binding[k]) is True for k in ("owner","host"))
             and identity_alive(client) is True,"RESULT_OWNER_UNKNOWN")
        expires=max(current["expires_at"],now+c["lease_seconds"])
        if expires>current["expires_at"]:
            updated=dict(current,expires_at=expires)
            try:
                PortableAdmission(Path(c["pool_root"])).renew_idle(host.spec,current,updated,verified=c)
                atomic_json(host.session/"lease.json",updated)
            except BaseException:
                atomic_json(host.session/"lease.json",dict(current,state="RELEASE_PENDING"))
                atomic_json(host.session/"stop.signal",dict(reason="LIGHT_RENEW_COMMIT_FAILED"))
                raise
        return dict(resident=True,binding=host.binding,old_expires_at=current["expires_at"],
                    expires_at=expires,confirmed_at=now,host_confirmed=True)
