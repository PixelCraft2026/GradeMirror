"""Bounded PNG round trip through the normal portable export metadata consumer."""
import struct
from ..common import reject_reparse, sha_file, strict_load
from ..cpu_replay.contract import need
from .. import export_metadata
from . import portable

def run(root):
    need(portable.state().role == "io", "METADATA_PROBE_ROLE")
    request = root/"metadata-probe.json"
    reject_reparse(request)
    need(request.stat().st_size < 4096, "METADATA_PROBE_SIZE")
    expected = strict_load(request)
    need(set(expected)=={"source","encoded"}, "METADATA_PROBE_BINDINGS")
    src=root/"source.png"
    dst=root/"encoded.png"
    size=[]
    for key,path in (("source",src),("encoded",dst)):
        reject_reparse(path)
        need(path.stat().st_size <= 1024**2 and sha_file(path)==expected[key], "METADATA_PROBE_INPUT")
        with path.open("rb") as stream:
            head=stream.read(24)
        need(head[:8]==b"\x89PNG\r\n\x1a\n" and head[12:16]==b"IHDR", "METADATA_PROBE_PNG")
        width,height=struct.unpack(">II",head[16:])
        need(1<=width<=512 and 1<=height<=512,"METADATA_PROBE_DIMENSIONS")
        size.append((width,height))
    policy=export_metadata.policy_request(src,expected["source"])
    proof=export_metadata.apply(src,dst,policy,*size[1])
    need(proof["image_stream_before"]==proof["image_stream_after"],"METADATA_PROBE_PIXELS")
    return proof
