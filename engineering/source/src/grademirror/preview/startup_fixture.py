"""Authority for GM-080's two deterministic, synthetic PNGs and zero-model barrier."""
import hashlib
import os
from pathlib import Path
import struct
import time
import zlib

from ..common import GMError, atomic_json, strict_load
from ..cpu_replay.contract import need


def image(seed):
    def chunk(name, data):
        return (struct.pack(">I", len(data)) + name + data +
                struct.pack(">I", zlib.crc32(name + data)))
    rows = b"".join(bytes([0] + [(x * 13 + y * 7 + seed) % 256 for x in range(96)])
                    for y in range(32))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">2I5B", 32, 32, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(rows, 0)) + chunk(b"IEND", b""))


def enabled():
    """The packaged release requires the ordinary source geometry contract."""
    return False


def allowed(header):
    if not enabled():
        return False
    if (header.get("width"), header.get("height"), header.get("bits"),
            str(header.get("format")).lower()) != (32, 32, 8, "png"):
        return False
    return any(header.get("sha256") == hashlib.sha256(image(seed)).hexdigest() for seed in (29, 113))


def active():
    from ..light.runtime import operation
    plan = operation()
    if not plan or plan.get("engineering_startup_chain") != "gm080-zero-science-v1":
        return False
    need(plan.get("validation_evidence") is True and
         (plan["selection"]["backend"], plan["quality"], plan["operation"]) == ("cpu", "fast", "fit") and
         allowed(plan["source"]) and allowed(plan["reference"]), "GM080_FIXTURE_AUTHORITY")
    return True


def model_barrier(root, spec, payload):
    import numpy as np
    from ..cpu_replay.numeric import attach
    from ..winproc import current_identity

    owned = []
    source = output = None
    counts = dict(render=0, model_construct=0, state_load=0, forward_batches=0, completed_tiles=0)
    try:
        sm, source = attach(payload["S"])
        owned.append(sm)
        om, output = attach(payload["O"])
        owned.append(om)
        need(source.dtype == np.uint8 and output.dtype == np.float32 and
             source.shape == output.shape == (32, 32, 3) and not source.flags.writeable and
             output.flags.writeable and not np.shares_memory(source, output), "GM080_FIXTURE_MAPPINGS")
        ack = strict_load(root / "actual-retained.json")
        atomic_json(root / "gm080-model-barrier.json",
                    dict(role="r0", stage="after_ack_and_payload_before_bundle_parameters",
                         identity=current_identity(), ack=ack, counts=counts,
                         attempt=spec["attempt_id"], maps=[sm.name, om.name]))
        end = time.monotonic() + 60
        while not (Path(payload["cancel"]).is_file() or (root.parent / "stop.signal").is_file()):
            need(time.monotonic() < end, "GM080_BARRIER_CANCEL_TIMEOUT")
            time.sleep(.025)
        raise GMError("CANCELLED", "GM-080 模型前工程取消", 8)
    finally:
        del source, output
        for mapping in reversed(owned):
            mapping.close()
        atomic_json(root / "gm080-barrier-tail.json", dict(counts=counts, mappings_closed=len(owned)))
