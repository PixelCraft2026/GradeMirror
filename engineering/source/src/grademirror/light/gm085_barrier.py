"""Explicit metadata-only probe for a packaged worker with a bound plan."""
import os
from pathlib import Path
import sys
import time
from ..common import atomic_json, sha_file, strict_load
from ..cpu_replay.contract import need
from ..winproc import current_identity

def preflight(role, plan, args, executable):
    from .runtime import SCOPES, data_root
    from . import portable
    # A production invocation has no sidecar. A probe uses the same argv and
    # stops after plan, interpreter, role imports and packaged entry verification.
    target = Path(args[-1]).resolve() if args else None
    if plan is None or target is None:
        return False
    marker = target.with_name(target.name + ".gm085-preflight.json")
    if not marker.is_file():
        return False
    token = strict_load(marker)
    allowed = set(SCOPES) if portable.enabled() else {"mixed", "owner", "host", "cpu_owner", "cpu_host", "gpu"}
    need(role in allowed and marker.is_relative_to(data_root() / "gm085-preflight")
         and token == dict(role=role,plan_id=plan["plan_id"], target_sha256=sha_file(target),
                           expires=token.get("expires"))
         and isinstance(token["expires"], (int,float)) and 0 <= token["expires"]-time.monotonic() <= 300
         and not marker.with_suffix(".cancel").exists(), "GM085_PREFLIGHT_TOKEN")
    torch = sys.modules.get("torch")
    need(torch is None or not torch.cuda.is_initialized(), "GM085_PREFLIGHT_CUDA")
    identity = current_identity()
    if portable.enabled():
        portable_member = portable.entry_identity(executable, identity)
        entry = dict(executable=identity["image_path"],
                     executable_sha256=portable_member["role_executable_sha256"],
                     portable_entry=portable_member)
    else:
        entry = dict(executable=str(executable), executable_sha256=sha_file(executable))
    atomic_json(marker.with_suffix(".ack.json"), dict(identity=identity, plan_id=plan["plan_id"],
        role=role, argv=list(args), **entry, selected_device=plan.get("device"),
        visible=os.environ.get("CUDA_VISIBLE_DEVICES"), temp=os.environ.get("TEMP"),
        model_load=0, cuda_context=0, photo_decode=0, at=time.monotonic()))
    return True
