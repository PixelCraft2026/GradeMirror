"""Offline resumption at the archive/launcher packaging boundary."""
import base64
import collections
import base64
import csv
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile,ZIP_DEFLATED,ZipInfo

root=Path(__file__).resolve().parent
cache=Path(sys.argv[1]).resolve(strict=True)
windows=Path(sys.argv[2]).resolve(strict=True)
tool=Path(sys.executable).resolve(strict=True).parent
out=root/"output"
allowed=(root,cache,tool,windows/"System32",windows/"WinSxS")
writable=(root/"output",root/"tmp",root/"launcher-04",root/"roles")
hits=collections.Counter()
denied=[]
def scope(file,roots):
    if isinstance(file,int):
        return "inherited_descriptor"
    p=Path(os.path.abspath(os.fsdecode(file)))
    return next((str(base) for base in roots if p.is_relative_to(base)),None)
def hook(event,args):
    if event.startswith("socket."):
        denied.append([event,"offline"])
        raise PermissionError("Offline socket disallowed")
    if event=="subprocess.Popen":
        image=args[0]
        argv=str(args[1])
        expected=str(tool/"python.exe")
        explicit=str(image).casefold()==expected.casefold()
        implicit=image is None and any(argv.casefold().startswith(prefix.casefold()+" ") for prefix in (expected,'"'+expected+'"'))
        child=str(root/"roles/builder/PyInstaller/isolated/_child.py").casefold()
        if not ((explicit or implicit) and child in argv.casefold()):
            denied.append([event,str(image),argv])
            raise PermissionError(f"Executable outside offline build graph: {image}")
        hits["isolated-pyinstaller-python:"+expected]+=1
    if event in ("open","os.listdir","os.scandir"):
        flag=args[2] if event=="open" else 0
        write=isinstance(flag,int) and flag&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        base=scope(args[0] or ".",writable if write else allowed)
        if not base:
            denied.append([event,str(args[0])])
            raise PermissionError(f"Path outside clean build: {args[0]}")
        hits[("write:" if write else "read:")+base]+=1
sys.path.insert(0,str(root))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
os.chdir(root)
sys.addaudithook(hook)
from build_support import bootstrap,canon,code_archive

def ident(p):
    with p.open("rb") as f:
        return dict(bytes=p.stat().st_size,sha256=hashlib.file_digest(f,"sha256").hexdigest())
def digest(obj):
    return hashlib.sha256(canon(obj)).hexdigest()
def write_json(p,value):
    with p.open("xb") as f:
        f.write(canon(value)+b"\n")
def link(a,b):
    b.parent.mkdir(parents=True,exist_ok=True)
    os.link(a,b)
def zip_member(z,name,payload):
    info=ZipInfo(name)
    info.compress_type=ZIP_DEFLATED
    z.writestr(info,payload,compresslevel=6)

def safe(name):
    return all(part and part not in (".","..") and not part.endswith((" ",".")) for part in name.split("/")) and not any(c in name for c in "\\:")

def wheel_members(row):
    archive=cache/row["filename"]
    expected={k:row[k] for k in ("bytes","sha256")}
    if ident(archive)!=expected:
        raise ValueError(f"Offline wheel lock mismatch {archive.name}")
    pool=root/"roles/_wheel-content"/row["sha256"][:12]
    files={}
    withheld=[]
    with ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel=info.filename
            if not safe(rel):
                raise ValueError("Invalid archive member "+rel)
            first,_,rest=rel.partition("/")
            if first.endswith(".data"):
                place,_,name=rest.partition("/")
                if place not in ("purelib","platlib"):
                    withheld.append(rel)
                    continue
                rel=name
            if row["name"] == "opencv-python-headless" and rel == "cv2/opencv_videoio_ffmpeg4100_64.dll":
                withheld.append(rel)
                continue
            if all(role in runtime_selection for role in row["roles"]) and not any(
                rel in runtime_selection[role] for role in row["roles"]
            ):
                withheld.append(rel)
                continue
            if row["name"] == "pyside6-essentials" and rel in qt_prune:
                with z.open(info) as excluded_stream:
                    excluded = dict(bytes=info.file_size,sha256=hashlib.file_digest(excluded_stream,"sha256").hexdigest())
                if excluded != qt_prune[rel]:
                    raise ValueError("Qt omitted member identity mismatch: "+rel)
                qt_withheld.add(rel)
                withheld.append(rel)
                continue
            if rel.endswith(("/RECORD","/direct_url.json")):
                continue
            folded=rel.casefold()
            if folded in files:
                raise ValueError("Duplicate wheel member "+rel)
            target=pool/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_name(target.name+".gm093-stage")
            with z.open(info) as src,tmp.open("xb") as dst:
                completed=0
                sync_interval=32*1024**2
                while block:=src.read(2*1024**2):
                    if shutil.disk_usage(root).free < 4563402752:
                        raise OSError("DISK_RESERVE_MEMBER_WRITE")
                    dst.write(block)
                    completed+=len(block)
                    if completed%sync_interval==0:
                        dst.flush()
                        os.fsync(dst.fileno())
                if completed>=sync_interval:
                    dst.flush()
                    os.fsync(dst.fileno())
            tmp.rename(target)
            files[folded]=[rel,target,ident(target)]
    record_index=[entry[0].removesuffix("METADATA")+"RECORD" for entry in files.values()
                  if entry[0].count("/")==1 and entry[0].endswith(".dist-info/METADATA")]
    if len(record_index)!=1:
        raise ValueError("Invalid wheel metadata count "+archive.name)
    record=record_index[0]
    buf=io.StringIO(newline="")
    csv.writer(buf,lineterminator="\n").writerows(
        [(rel,"sha256="+base64.urlsafe_b64encode(bytes.fromhex(binding["sha256"])).decode().rstrip("="),binding["bytes"])
         for rel,_,binding in sorted(files.values())]+[(record,"","")])
    dest=pool/record
    dest.write_bytes(buf.getvalue().encode("utf-8"))
    files[record.casefold()]=[record,dest,ident(dest)]
    return files,withheld


policy=json.loads((root/"layout-policy.json").read_bytes())
source_manifest=json.loads((root/"source-inputs.json").read_bytes())
if {p.relative_to(root/"source").as_posix():ident(p) for p in (root/"source").rglob("*") if p.is_file()}!=source_manifest:
    raise ValueError("Source inputs differ")
assert len(source_manifest)==354
res={}
out.mkdir()
if "--verify-inputs" in sys.argv:
    locks=json.loads((root/"dependency-lock.json").read_bytes())["inputs"]
    for item in locks:
        if not safe(item["filename"]) or ident(cache/item["filename"])!={k:item[k] for k in ("bytes","sha256")}:
            raise ValueError("Wheel lock hash mismatch")
    exif=json.loads((root/"exiftool-lock.json").read_bytes())["archive"]
    assert ident(cache/"exiftool-13.60_64.zip")=={k:exif[k] for k in ("bytes","sha256")}
    for rel,row in json.loads((root/"runtime-data.json").read_bytes()).items():
        assert safe(rel) and ident(root/"runtime-data"/rel)==row
    compiled,index,archive=code_archive(out)
    write_json(out/"verified-inputs.json",dict(source=compiled["sha256"],code_archive=archive,
        modules=len(index["members"]),dependency_archives=len(locks)+1,read_audit=hits,denied=denied))
    print("Source and vendor inputs checked. No runtime wheel expansion in verify-inputs mode.")
    raise SystemExit(0)
lock=json.loads((root/"dependency-lock.json").read_bytes())
runtime_selection={k:set(v) for k,v in json.loads((root/"numeric-runtime-paths.json").read_bytes()).items()}
qt_prune=json.loads((root/"qt-prune.json").read_bytes())
qt_withheld=set()
roles=set()
omitted={}
inventory=collections.defaultdict(dict)
result={"wheel_roles":{},"editions":{},"launchers":{}}
try:
    for row in lock["inputs"]:
        if shutil.disk_usage(root).free < 4563402752:
            raise OSError("DISK_RESERVE_PREFLIGHT")
        entries,nonruntime=wheel_members(row)
        omitted[row["filename"]]=nonruntime
        for role in row["roles"]:
            roles.add(role)
            for fold,(name,source,binding) in entries.items():
                if fold in inventory[role]:
                    if inventory[role][fold][1]!=binding:
                        raise ValueError(f"Conflicting wheels in {role}: {name}")
                    continue
                link(source,root/"roles"/role/name)
                inventory[role][fold]=(name,binding)
        print(row["filename"],len(entries),flush=True)
    if qt_withheld != qt_prune.keys():
        raise ValueError("Qt omissions did not match the frozen list")
    result["wheel_roles"]={r:len(v) for r,v in inventory.items()}
    result["omitted_wheel_non_site_members"]=omitted
    res["verified_payloads"]={r:dict(v.values()) for r,v in inventory.items()}
    for edition in ("cpu","gpu"):
        target=out/edition
        target.mkdir()
        product_roles=("desktop","io","control","cpu","solver")+(("cu124",) if edition=="gpu" else ())
        for role in product_roles:
            (target/"runtime"/role/"Lib/site-packages").mkdir(parents=True)
            for name,_ in inventory[role].values():
                link(root/"roles"/role/name,target/"runtime"/role/"Lib/site-packages"/name)
        result["editions"][edition]={"roles":{k:len(inventory[k]) for k in product_roles}}
    built,index,archive=code_archive(out/"cpu")
    link(out/"cpu/runtime/grademirror-code.zip",out/"gpu/runtime/grademirror-code.zip")
    res.update(source_build=built,code_archive=archive,modules=len(index["members"]))
    old=out/"cpu/runtime/cpython/python312.zip"
    old.parent.mkdir(parents=True)
    with ZipFile(old,"x",ZIP_DEFLATED) as z:
        for p in sorted((tool/"Lib").rglob("*.py")):
            rel=p.relative_to(tool/"Lib").as_posix()
            if not rel.startswith(("site-packages/","test/","idlelib/","tkinter/test/","turtledemo/","ensurepip/")):
                zip_member(z,rel,p.read_bytes())
    with ZipFile(old) as z:
        assert z.testzip() is None
        res["stdlib_members"]=len(z.infolist())
    link(old,out/"gpu/runtime/cpython/python312.zip")
    res["stdlib"]=ident(old)
    for ed in ("cpu","gpu"):
        dst=out/ed
        dlls=dst/"runtime/cpython/DLLs"
        dlls.mkdir()
        for p in (tool/"DLLs").iterdir():
            if p.is_file() and p.suffix in (".dll",".pyd"):
                d=dlls/p.name
                if ed=="cpu":
                    tmp=d.with_suffix(d.suffix+".stage")
                    shutil.copyfile(p,tmp)
                    tmp.rename(d)
                else:
                    link(out/"cpu/runtime/cpython/DLLs"/p.name,d)
        for p in sorted((root/"source").rglob("*")):
            if p.is_file() and (p.suffix not in (".py",".md") or p.name=="README-离线.md"):
                d=dst/p.relative_to(root/"source")
                d.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(p,d)
        lic=dst/"licenses/Python-3.12.14.txt"
        lic.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(tool/"LICENSE.txt",lic)
    exif=json.loads((root/"exiftool-lock.json").read_bytes())
    exifzip=cache/"exiftool-13.60_64.zip"
    assert ident(exifzip)=={k:exif["archive"][k] for k in ("bytes","sha256")}
    relroot="tools/vendor/exiftool"
    with ZipFile(exifzip) as z:
        one=out/"cpu"/relroot/"13.60"
        two=out/"gpu"/relroot/"13.60"
        for entry in exif["files"]:
            name=entry["path"]
            data=z.read(name)
            assert len(data)==entry["bytes"] and hashlib.sha256(data).hexdigest()==entry["sha256"]
            d=one/name
            d.parent.mkdir(parents=True,exist_ok=True)
            tmp=d.with_name(d.name+".stage")
            with tmp.open("xb") as f:
                f.write(data)
            tmp.rename(d)
            link(d,two/name)
        component=dict(schema="exiftool-component-v1",version="13.60",relative_root="13.60",
                       download=dict(exif["archive"],installer=False,official_page="https://exiftool.org/"),
                       files=exif["files"],entry=exif["entry"],script=exif["script"])
        write_json(one.parent/"component.json",component)
        link(one.parent/"component.json",two.parent/"component.json")
    res["exiftool_count"]=len(exif["files"])
    launcher=root/"launcher-04"
    launcher.mkdir()
    (launcher/"tmp").mkdir()
    generated=bootstrap(launcher,built,archive,res["stdlib"]["sha256"])
    res["launcher_status"]="BUILT"
    res["launchers"]={p.name:ident(p) for p in generated.iterdir() if p.is_file()}
    res["editions"]={}
    additions=json.loads((root/"runtime-data.json").read_bytes())
    for edition in ("cpu","gpu"):
        for name,expected in additions.items():
            if not safe(name):
                raise ValueError("Frozen runtime-data path")
            source=root/"runtime-data"/name
            if ident(source)!=expected:
                raise ValueError("Frozen runtime-data digest: "+name)
            link(source,out/edition/name)

    for ed in ("cpu","gpu"):
        dst=out/ed
        for p in generated.rglob("*"):
            if p.is_file():
                link(p,dst/p.relative_to(generated))
        for name in ("python3.dll","vcruntime140_1.dll"):
            dest=dst/"_internal"/name
            if not dest.exists():
                with (tool/name).open("rb") as src, dest.with_suffix(".stage").open("xb") as target:
                    shutil.copyfileobj(src,target)
                dest.with_suffix(".stage").rename(dest)
        profiles=("control","io","cpu","solver")+(("cu124",) if ed=="gpu" else ())
        for role in profiles:
            work=dst/"workers"/role
            link(dst/"GradeMirror-CLI.exe",work/f"GradeMirror-{role}.exe")
            for p in (dst/"_internal").rglob("*"):
                if p.is_file():
                    link(p,work/"_internal"/p.relative_to(dst/"_internal"))
        rows={p.relative_to(dst).as_posix():ident(p) for p in dst.rglob("*") if p.is_file()}
        selection=json.loads((root/"expected-numeric-runtime.json").read_bytes())
        numeric={n:v for n,v in rows.items() if n.startswith(("runtime/cpu/","runtime/cu124/","runtime/solver/","runtime/io/","runtime/cpython/"))}
        assert len(numeric)==selection[ed]["count"] and digest(numeric)==selection[ed]["sha256"], "D1_EXACT_BUILD_RUNTIME"
        if ed=="cpu":
            from ast import literal_eval
            scope_file=root/"source/src/grademirror/light/fixed_c_bridge.py"
            definitions={line.split(" = ",1)[0]:line.split(" = ",1)[1] for line in scope_file.read_text(encoding="utf8").splitlines()
                         if line.startswith("SCOPE = ")}
            fixed_c_scope=literal_eval(definitions["SCOPE"])
            name="compatibility/cpu-fast-png-target.json"
            target=dst/name
            target.parent.mkdir(parents=True)
            bound=("GradeMirror-CLI.exe","GradeMirror.exe","workers/cpu/GradeMirror-cpu.exe",
                   "workers/io/GradeMirror-io.exe","workers/control/GradeMirror-control.exe")
            write_json(target,dict(schema="gm095-c-codec-target-v1",build=built["sha256"],scope=digest(fixed_c_scope),
                                   numerical_inventory=digest(numeric),launchers={n:rows[n] for n in bound}))
            rows[name]=ident(target)
        roles={}
        fixed=policy[ed]
        for role,info in fixed["bundled_roles"].items():
            exe=f"workers/{role}/GradeMirror-{role}.exe"
            members={k:v for k,v in rows.items() if k.startswith(f"runtime/{role}/") or k==exe}
            identity=[n for n in members if n.endswith(("/METADATA","/WHEEL","/RECORD"))]+[exe]
            roles[role]=dict(info,exe=exe,files=sorted(members),identity=sorted(identity),inventory_sha256=digest(members))
        manifest=dict(schema=fixed["schema"],version="0.1.0-preview.50",edition=ed,files=rows,
            distribution="gm103-offline-review-only",execution_layout="PORTABLE_PYINSTALLER_ONEDIR_V1",
            eta_compatibility="UNQUALIFIED",runtime_parent_sha256=fixed["runtime_sha256"],
            runtime_sha256=digest(roles),build=built,production_flags=fixed["production_flags"],
            prior_production_flags=fixed["production_flags"],bundled_roles=roles,windows_components=fixed["windows_components"],
            third_party_licenses=sorted(k for k in rows if "/licenses/" in k.casefold() or k.startswith("licenses/")))
        write_json(dst/"light-manifest.json",manifest)
        res["editions"][ed]=dict(files=len(rows)+1,manifest=ident(dst/"light-manifest.json"),
                                 source_build=manifest["build"]["sha256"],runtime_sha256=manifest["runtime_sha256"])
    res["status"]="CPU_GPU_ONEDIR_ASSEMBLED_UNQUALIFIED"

except (Exception,SystemExit) as error:
    res["status"]="INCOMPLETE"
    res["failure"]=repr(error)
    raise
finally:
    write_json(out/"result.json",res)
    write_json(out/"access.json",dict(hits=hits,denied=denied))
