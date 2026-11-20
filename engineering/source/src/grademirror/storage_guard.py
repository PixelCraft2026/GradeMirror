"""Bound disk writes used by the portable resident export transaction."""
import shutil
from pathlib import Path

from .common import GMError

HARD_FREE = 4 * 1024**3
CHUNK = 1024**2

def reserve(path, estimated_write, *, reserve_bytes=HARD_FREE, usage=shutil.disk_usage):
    directory = Path(path)
    while not directory.is_dir():
        if directory == directory.parent:
            raise GMError("PORTABLE_STORAGE_SCOPE","Cannot identify output volume",5)
        directory = directory.parent
    available = usage(directory).free
    if estimated_write < 0 or available < reserve_bytes + estimated_write:
        raise GMError("PORTABLE_STORAGE_RESERVE",
                      f"Free {available} B, expected write {estimated_write} B, reserve {reserve_bytes} B.",5)
    return available

def tiff_writer_upper(width, height, bits, source_bytes):
    if width<=0 or height<=0 or bits not in (8,16) or source_bytes<0:
        raise GMError("PORTABLE_STORAGE_GEOMETRY","Invalid TIFF reservation geometry",5)
    raw=width*height*3*(bits//8)
    # Allow deflate overhead, per-strip storage and alignment, IFD and ICC.
    encoded=raw+raw//16+32*1024**2
    rewritten=encoded+source_bytes+16*1024**2
    return encoded,rewritten

def write_chunks(path, file, data):
    view=memoryview(data)
    for offset in range(0,len(view),CHUNK):
        block=view[offset:offset+CHUNK]
        reserve(path,len(block))
        if file.write(block)!=len(block):
            raise OSError("Short staged write")
