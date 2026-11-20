"""Obtain exact release dependencies from their locked upstream archives."""
import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen

parser=argparse.ArgumentParser()
parser.add_argument("lock",type=Path)
parser.add_argument("cache",type=Path)
parser.add_argument("--verify-only",action="store_true")
args=parser.parse_args()
doc=json.loads(args.lock.read_bytes())
records=doc.get("inputs") or [dict(doc["archive"],filename="exiftool-13.60_64.zip")]
hosts={"files.pythonhosted.org","download.pytorch.org","download-r2.pytorch.org",
       "downloads.sourceforge.net","github.com"}
redirects=hosts|{"release-assets.githubusercontent.com","objects.githubusercontent.com","cdn.download.pytorch.org"}
disk_hard=4*1024**3
disk_margin=2*1024**3


def checked_path(path):
    for item in (path,*path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(),"st_file_attributes",0)&0x400):
            raise ValueError(f"Reparse point in dependency staging: {item}")


def allow_response(url):
    parts=urlsplit(url)
    host=parts.hostname or ""
    return parts.scheme=="https" and (host in redirects or host.endswith(".dl.sourceforge.net"))


checked_path(args.cache)
if not args.verify_only:
    args.cache.mkdir(parents=True,exist_ok=True)

def ident(path):
    with path.open("rb") as f:
        return path.stat().st_size,hashlib.file_digest(f,"sha256").hexdigest()
for row in records:
    url=row["url"]
    filename=row["filename"]
    endpoint=urlsplit(url)
    if endpoint.scheme!="https" or endpoint.hostname not in hosts or Path(filename).name!=filename or ":" in filename:
        raise ValueError(f"Unexpected archive destination: {filename}")
    path=args.cache/filename
    expected=(row["bytes"],row["sha256"])
    checked_path(path)
    if path.exists():
        if not path.is_file() or ident(path)!=expected:
            raise ValueError(f"Cache digest mismatch: {filename}")
    elif args.verify_only:
        raise FileNotFoundError(f"Required offline archive missing: {filename}")
    else:
        partial=path.with_suffix(path.suffix+".partial")
        checked_path(partial)
        offset=partial.stat().st_size if partial.is_file() else 0
        if not 0<=offset<=expected[0]:
            raise ValueError(f"Invalid partial: {filename}")
        if offset<expected[0]:
            if shutil.disk_usage(args.cache).free < disk_hard+disk_margin+expected[0]-offset:
                raise OSError(f"Download disk admission failed: {filename}")
            headers={"User-Agent":"GradeMirror upstream dependency acquisition"}
            if offset:
                headers["Range"]=f"bytes={offset}-"
            with urlopen(Request(url,headers=headers),timeout=45) as src:
                if not allow_response(src.geturl()):
                    raise ValueError(f"Unexpected upstream redirect: {filename}")
                if offset and (src.status!=206 or not src.headers.get("Content-Range","").startswith(f"bytes {offset}-")):
                    raise ValueError(f"Server refused exact-range resume: {filename}")
                with partial.open("ab" if offset else "xb") as dst:
                    while block:=src.read(1024**2):
                        offset+=len(block)
                        if offset>expected[0]:
                            raise ValueError(f"Input exceeds locked length: {filename}")
                        if shutil.disk_usage(args.cache).free < disk_hard+len(block):
                            raise OSError(f"Locked download stopped at disk reserve: {filename}")
                        dst.write(block)
        if ident(partial)!=expected:
            raise ValueError(f"Downloaded digest mismatch: {filename}")
        partial.rename(path)
    print(filename,expected[0],expected[1],flush=True)

