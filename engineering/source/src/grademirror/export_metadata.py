"""source-exif-v1: verified source metadata in private staging, before publication.
No source pixels are decoded. Opaque TIFF private bytes retain their offsets.
"""
import hashlib,io,json,os,shutil,struct,subprocess,time,uuid,zlib
from pathlib import Path
from tempfile import TemporaryFile
from .exif_structure import Graph,LIMIT,STORAGE,need,digest
POLICY='source-exif-v1';RULE='main-representation-v1';ROOT=Path(__file__).resolve().parents[2]
def _hash(p):
 with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def _identity(p):
 s=Path(p).stat();return dict(bytes=s.st_size,sha256=_hash(p),device=s.st_dev,inode=s.st_ino,mtime_ns=s.st_mtime_ns)
def _copy(f,g,n,check=None):
 while n:
  if check:check()
  b=f.read(min(n,1024**2));need(bool(b),'TRUNCATED');g.write(b);n-=len(b)
def _container(p):
 """Return type, EXIF and non-EXIF spans; no compressed image is decoded."""
 p=Path(p);size=p.stat().st_size;spans=[];exifs=[]
 with p.open('rb') as f:
  h=f.read(12)
  if h[:2]==b'\xff\xd8':
   kind='jpeg';pos=2;spans=[(0,2)]
   while pos<size:
    f.seek(pos);v=f.read(2);need(len(v)==2 and v[0]==255,'JPEG_MARKER');tag=v[1]
    if tag in (0xda,0xd9):spans.append((pos,size-pos));break
    n=struct.unpack('>H',f.read(2))[0];need(n>=2 and pos+2+n<=size,'JPEG_SEGMENT');is_exif=tag==0xe1 and f.read(6)==b'Exif\0\0'
    if is_exif:
     need(n-8<=LIMIT,'LIMIT');f.seek(pos+10);exifs.append(f.read(n-8))
    else:spans.append((pos,n+2))
    pos+=n+2
   else:need(False,'JPEG_SCAN_MISSING')
  elif h[:8]==b'\x89PNG\r\n\x1a\n':
   kind='png';pos=8;spans=[(0,8)]
   while pos<size:
    f.seek(pos);head=f.read(8);need(len(head)==8,'PNG_TRUNCATED');n,tag=struct.unpack('>I4s',head);need(pos+12+n<=size,'PNG_BOUNDS')
    if tag==b'eXIf':
     need(n<=LIMIT,'LIMIT');b=f.read(n);need(zlib.crc32(tag+b)&0xffffffff==struct.unpack('>I',f.read(4))[0],'PNG_CRC');exifs.append(b)
    else:spans.append((pos,n+12))
    pos+=n+12
    if tag==b'IEND':need(n==0 and pos==size,'PNG_TRAILING');break
   else:need(False,'PNG_IEND')
  elif h[:4]==b'RIFF' and h[8:]==b'WEBP':
   kind='webp';need(struct.unpack('<I',h[4:8])[0]+8==size,'WEBP_SIZE');pos=12
   while pos<size:
    f.seek(pos);head=f.read(8);need(len(head)==8,'WEBP_TRUNCATED');tag,n=struct.unpack('<4sI',head);length=8+n+(n%2);need(pos+length<=size,'WEBP_BOUNDS')
    if tag==b'EXIF':
     need(n<=LIMIT,'LIMIT');b=f.read(n);exifs.append(b[6:] if b.startswith(b'Exif\0\0') else b)
    else:spans.append((pos,length))
    pos+=length
  elif h[:2] in (b'II',b'MM'):return 'tiff',None,[]
  elif h[:2]==b'BM':return 'bmp',None,[(0,size)]
  else:need(False,'CONTAINER_UNSUPPORTED')
 need(len(exifs)<=1,'DUPLICATE_BLOCK');return kind,exifs[0] if exifs else None,spans

def inspect_source(path):
 p=Path(path);ident=_identity(p);kind,raw,_=_container(p)
 if kind=='tiff':
  with p.open('rb') as f:g=Graph(f,ident['bytes'])
  from .exif_structure import tiff_layout
  from .export_thumbnail import photo_rows
  layout=tiff_layout(g)
  rows=photo_rows(g,tiff=True);state='SOURCE_EXIF_PRESENT' if rows else 'SOURCE_EXIF_ABSENT';raw_hash=None
 else:
  g=Graph(io.BytesIO(raw),len(raw)) if raw is not None else None;rows=g.semantic() if g else [];state='SOURCE_EXIF_PRESENT' if raw is not None else 'SOURCE_EXIF_ABSENT';raw_hash=digest(raw) if raw is not None else None
  if raw is not None and kind=='png':
   for r in g.rows:
    if r['ifd']=='IFD0' and r['tag']==274:need(g.ints(r)==[1],'PNG_ORIENTATION_UNSUPPORTED')
 need(_identity(p)==ident,'SOURCE_CHANGED')
 return dict(metadata_policy_id=POLICY,normalization_rule=RULE,source_exif_state=state,source_exif_sha256=raw_hash,source_exif_semantic_sha256=digest(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()),source_file_sha256=ident['sha256'],container=kind,rows=rows,**(dict(tiff_layout={k:v for k,v in layout.items() if k!='icc_bytes'}) if kind=='tiff' else {}))

def component():
 from .light import portable
 from .light.native_policy import EXIF_COMPONENT, EXPECTED_ENTRY, EXPECTED_SCRIPT, fixed_component
 from .common import strict_load, reject_reparse
 p=ROOT/EXIF_COMPONENT
 if portable.enabled():p=fixed_component(EXIF_COMPONENT,"exif")
 v=strict_load(p)
 need(v['version']=='13.60' and v['schema']=='exiftool-component-v1','COMPONENT_VERSION')
 need(v['relative_root']=='13.60' and v['entry']==EXPECTED_ENTRY and v['script']==EXPECTED_SCRIPT,'COMPONENT_ROLE')
 base=p.parent/'13.60'
 seen=set()
 for x in v['files']:
  name=x['path'];parts=name.split('/')
  need(len(parts)>1 and parts[0]=='exiftool-13.60_64' and all(a not in ('','.','..') and not a.endswith(('.',' ')) for a in parts) and chr(92) not in name and ':' not in name and name.casefold() not in seen,'COMPONENT_MEMBER')
  seen.add(name.casefold())
  f=base/name;reject_reparse(f)
  need(f.is_file() and f.stat().st_size==x['bytes'] and _hash(f)==x['sha256'],'COMPONENT_CHANGED')
 need({v['entry'].casefold(),v['script'].casefold()}<=seen,'COMPONENT_ROLE')
 return v,base

def policy_for_export_key(source, source_sha256, *, prior=None, requested=None):
    """Recover the original policy or select the current default for a new key."""
    if prior is not None:
        saved = prior.get("metadata_policy_id")
        if requested is not None and requested != saved:
            raise ValueError("EXPORT_METADATA_POLICY_CHANGED")
        if saved is None:
            return {}
        requested = saved
    return policy_request(source, source_sha256, policy=requested)


def policy_request(source,source_sha256,*,policy=None):
 s=inspect_source(source);need(s['source_file_sha256']==source_sha256,'SOURCE_IDENTITY');v,_=component()
 result={k:s[k] for k in ('metadata_policy_id','normalization_rule','source_exif_state','source_exif_sha256','source_exif_semantic_sha256')}|dict(metadata_component_sha256=digest((ROOT/'tools/vendor/exiftool/component.json').read_bytes()))
 from .export_thumbnail import POLICY as NEW_POLICY,RULE as NEW_RULE,THUMBNAIL_POLICY
 from .export_tiff_compact import POLICY as COMPACT_POLICY,RULE as COMPACT_RULE
 policy=(COMPACT_POLICY if s['container']=='tiff' else NEW_POLICY) if policy is None else policy
 if policy==COMPACT_POLICY:
  need(s['container']=='tiff','POLICY_UNSUPPORTED')
  return dict(result,metadata_policy_id=COMPACT_POLICY,normalization_rule=COMPACT_RULE,thumbnail_policy_id=THUMBNAIL_POLICY)
 if policy==NEW_POLICY:result.update(metadata_policy_id=NEW_POLICY,normalization_rule=NEW_RULE,thumbnail_policy_id=THUMBNAIL_POLICY)
 else:
  need(policy==POLICY,'POLICY_UNSUPPORTED')
  need(not s.get('tiff_layout',{}).get('reduced'),'TIFF_REDUCED_REQUIRES_CURRENT_POLICY')
 return result

_ACTIVE_HELPER=None
def helper_argv_allowed(executable,argv):
 """The exact in-flight non-shell helper only; UTF-8 args arrive on stdin."""
 active=_ACTIVE_HELPER
 return active is not None and str(executable)==active[0] and (argv==subprocess.list2cmdline(active[1]) if isinstance(argv,str) else list(argv)==active[1])

def _validate_retained_ack(ack,identity):
 from .winproc import identity_key
 need(isinstance(ack,dict) and ack.get('actual_handle_retained') is True and
      identity_key(ack.get('identity'))==identity_key(identity),'HELPER_RETAINED_ACK_INVALID')

def _retain_helper(process,root,token,started):
 """The stdin request is withheld until the existing observer retains a handle.
 A parent Popen handle is always retained; an outer observer is additional proof.
 """
 from .common import atomic_json,strict_load
 from .winproc import process_identity,identity_key
 identity=process_identity(process.pid)
 need(identity and not identity.get('unknown'),'HELPER_IDENTITY')
 proof=dict(source='PARENT_POPEN_HANDLE',identity=identity,parent_handle_retained=True)
 if root:
  directory=Path(root)/'metadata-helpers'/token
  atomic_json(directory/'entry.json',dict(identity=identity,stage='VALIDATION_PENDING',stdin_request_withheld=True))
  deadline=min(started+120,time.monotonic()+30)
  while not (directory/'ack.json').is_file():
   need(process.poll() is None,'HELPER_EXIT_BEFORE_RETAIN')
   need(time.monotonic()<deadline,'HELPER_RETAIN_TIMEOUT')
   time.sleep(.01)
  ack=strict_load(directory/'ack.json');_validate_retained_ack(ack,identity)
  need(process.poll() is None and identity_key(process_identity(process.pid))==identity_key(identity),'HELPER_RETAIN_IDENTITY_CHANGED')
  proof.update(source='PARENT_HANDLE_AND_FROZEN_OBSERVER_ACK',ack_path=str(directory/'ack.json'),ack_sha256=_hash(directory/'ack.json'),stdin_delivered_after_retention=True)
 return identity,proof

def _reader_values(record):
 """ExifTool CopyN is an output ordinal, not an EXIF tag identity.
 Retain all per-IFD/tag values and multiplicities; raw Graph preserves order.
 """
 values={}
 for key,value in record.items():
  if key=='SourceFile':continue
  parts=key.split(':');middle=[p for p in parts[1:-1] if not(p.startswith('Copy') and p[4:].isdigit())]
  logical=':'.join([parts[0],*middle,parts[-1]]) if len(parts)>1 else key
  values.setdefault(logical,[]).append(value)
 return {key:sorted(rows,key=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False)) for key,rows in values.items()}

def _default_main_subfile_type(key,before,after):
 """Only a single integer zero and absence denote the same main TIFF image.
 Never exempt nonzero flags, duplicate entries or photographic fields.
 """
 if key!='IFD0:SubfileType':return False
 def zero(value):return isinstance(value,list) and len(value)==1 and type(value[0]) is int and value[0]==0
 return before is None and zero(after) or after is None and zero(before)

def _readback_changes(rows,*,thumbnail_removed=False,tiff_pair=False,compact=False):
 a,b=(_reader_values(row) for row in rows)
 changed=[]
 main={'IFD0:Orientation','ExifIFD:ExifImageWidth','ExifIFD:ExifImageHeight','IFD1:ThumbnailOffset','IFD0:ImageWidth','IFD0:ImageHeight','IFD0:BitsPerSample','IFD0:Compression','IFD0:PhotometricInterpretation','IFD0:FillOrder','IFD0:StripOffsets','IFD0:SamplesPerPixel','IFD0:RowsPerStrip','IFD0:StripByteCounts','IFD0:PlanarConfiguration','IFD0:Predictor','IFD0:SampleFormat','IFD0:TileWidth','IFD0:TileLength','IFD0:TileOffsets','IFD0:TileByteCounts'}
 for key in a.keys()|b.keys():
  if key=='SourceFile':continue
  if a.get(key)!=b.get(key):
   derived=thumbnail_removed and key.startswith('IFD1:') and key.split(':')[-1] in ('SubfileType','NewSubfileType','ImageWidth','ImageHeight','BitsPerSample','Compression','PhotometricInterpretation','FillOrder','StripOffsets','Orientation','SamplesPerPixel','RowsPerStrip','StripByteCounts','XResolution','YResolution','PlanarConfiguration','ResolutionUnit','Predictor','TileWidth','TileLength','TileOffsets','TileByteCounts','SampleFormat','ThumbnailOffset','ThumbnailLength','ThumbnailImage','ThumbnailTIFF','YCbCrCoefficients','YCbCrSubSampling','YCbCrPositioning','ReferenceBlackWhite')
   default_main=tiff_pair and _default_main_subfile_type(key,a.get(key),b.get(key))
   omitted=compact and key not in b
   need(key in main or derived or default_main or omitted,'SECOND_READER_VALUE_CHANGED:'+key);changed.append(dict(field=key,before=a.get(key),after=b.get(key),reason='main TIFF explicit SubfileType=0 equals absent default' if default_main else 'main representation or container offset; raw graph checked separately'))
 return changed

def _wait_for_metadata_helper(process, started, out, err, check):
    """Bound an actual child-handle wait with the existing caller and helper deadlines."""
    while True:
        if check:
            check()
        remaining = started + 120 - time.monotonic()
        need(remaining > 0, 'HELPER_TIMEOUT')
        need(out.stat().st_size <= LIMIT and err.stat().st_size <= 128*1024,
             'HELPER_OUTPUT_LIMIT')
        try:
            return process.wait(timeout=min(remaining, .05))
        except subprocess.TimeoutExpired:
            continue


def independent_readback(source,target,check=None,*,thumbnail_removed=False,compact=False):
 global _ACTIVE_HELPER
 v,base=component();argv=[str(base/v['entry']),str(base/v['script']),'-config','','-@','-']
 filenames=[str(Path(source).resolve()),str(Path(target).resolve())];need(not any('\n' in x or '\r' in x for x in filenames),'FILENAME_CONTROL')
 input_bytes=('\n'.join(['-q','-charset','filename=UTF8','-j','-a','-G1:4','-n','-u','-U','-EXIF:all','--',*filenames])+'\n').encode('utf8')
 # Helpers must be members of the host's existing Job; no independent monitor.
 if os.name=='nt':
  import ctypes
  from ctypes import wintypes as wt
  k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=wt.HANDLE;k.IsProcessInJob.argtypes=[wt.HANDLE,wt.HANDLE,ctypes.POINTER(wt.BOOL)];inside=wt.BOOL();need(k.IsProcessInJob(k.GetCurrentProcess(),None,ctypes.byref(inside)) and inside.value,'HELPER_JOB_REQUIRED')
 token=uuid.uuid4().hex;root=os.environ.get('GM052_OBSERVER_ROOT');plan=None
 if root:
  from .common import atomic_json
  from .cpu_replay.runtime import approve_plan
  plan=Path(root)/'plans'/('exiftool-'+token+'.json');atomic_json(plan,dict(argv=argv,cwd=str(ROOT),before_CreateProcess=True,role='source-exif-readback',stdin_utf8_sha256=digest(input_bytes),stdin_utf8_bytes=len(input_bytes)));approve_plan(plan)
 # Keep diagnostics inside the owning Job/session, never beside the user's image.
 from .common import writable_path
 from .preview.contract import selected as preview_context
 preview=preview_context()
 from .light.runtime import application, data_root
 helper_root=(Path(preview['workspace'])/'metadata-helpers' if preview else
              Path(root)/'metadata-helpers' if root else
              (data_root() if application() else ROOT)/'jobs/export-metadata')
 directory=writable_path(helper_root/token)
 directory.mkdir(parents=True,exist_ok=False)
 out=directory/'stdout';err=directory/'stderr';receipt=directory/'wait.json';p=None;identity=None;retention=None;started=time.monotonic()
 failure=None
 # The ordinary Job path supplies a finite argument file on stdin. The
 # observer-controlled path keeps stdin gated until its actual handle ACK.
 transport='observer-gated-pipe' if root else 'bounded-argument-file' 
 try:
  with out.open('xb') as so,err.open('xb') as se,TemporaryFile(mode='w+b',dir=directory) as request:
   if not root:
    request.write(input_bytes);request.flush();request.seek(0)
   if check:check()
   env=dict(os.environ);[env.pop(k,None) for k in ('PERL5LIB','PERLLIB','PERL5OPT','EXIFTOOL_HOME')]
   env.update(LC_ALL='C',LC_CTYPE='C',LANG='C');env['TEMP']=str(directory);env['TMP']=str(directory)
   _ACTIVE_HELPER=(argv[0],argv)
   try:p=subprocess.Popen(argv,executable=argv[0],cwd=ROOT,env=env,stdin=subprocess.PIPE if root else request,stdout=so,stderr=se,close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   finally:_ACTIVE_HELPER=None
   identity,retention=_retain_helper(p,root,token,started)
   if check:check()
   if root:
    p.stdin.write(input_bytes);p.stdin.close()
   _wait_for_metadata_helper(p,started,out,err,check)
  need(p.returncode==0,'HELPER_EXIT');need(out.stat().st_size<=LIMIT,'HELPER_OUTPUT_LIMIT');need(err.stat().st_size==0,'HELPER_STDERR');rows=json.loads(out.read_text('utf8'));need(len(rows)==2,'HELPER_READBACK')
  need(not any(any('Error' in k or 'Warning' in k for k in x) for x in rows),'HELPER_REPORTED_ERROR')
  tiff_pair=_container(source)[0]=='tiff' and _container(target)[0]=='tiff'
  changed=_readback_changes(rows,thumbnail_removed=thumbnail_removed,tiff_pair=tiff_pair,compact=compact)
  result=dict(diagnostic_receipt=str(receipt),secondary_allowed_changes=changed,component_version=v['version'],component_sha256=digest((ROOT/'tools/vendor/exiftool/component.json').read_bytes()),records=rows,elapsed_seconds=time.monotonic()-started,identity=identity,retention=retention,actual_handle_wait=True,exit_code=p.returncode,request_transport=transport)
  from .common import atomic_json
  from .light.native_policy import CONFIG, MODULE
  module=base/MODULE
  marker=directory/'gm094-module-loaded.txt'
  result.update(library_build=CONFIG["mode"],module_sha256=_hash(module),
                user_module_loaded=marker.is_file() and marker.read_bytes()==b'GM094-EXIFTOOL-MODULE')
  atomic_json(receipt,result)
  return result
 except Exception as error:
  failure=dict(type=type(error).__name__,code=str(error)[:160])
  raise
 finally:
  if p is not None:
   if p.stdin is not None and not p.stdin.closed:p.stdin.close()
   if p.poll() is None:p.terminate()
   p.wait(timeout=30)
  if p is not None and not receipt.exists():
   from .common import atomic_json
   atomic_json(receipt,dict(identity=identity,retention=retention,actual_handle_wait=True,exit_code=p.returncode,failed=True,failure=failure,request_transport=transport,elapsed_seconds=time.monotonic()-started,stdout=out.read_text('utf8',errors='replace') if out.exists() and out.stat().st_size<=LIMIT else 'OUTPUT_LIMIT_OR_ABSENT',stderr=err.read_text('utf8',errors='replace') if err.exists() and err.stat().st_size<=128*1024 else 'OUTPUT_LIMIT_OR_ABSENT'))
  for f in (out,err):
   try:f.unlink()
   except FileNotFoundError:pass

def image_stream_proof(path):
 """All JPEG/PNG non-EXIF bytes; WebP exact image/ICC chunks. TIFF raw strips."""
 kind,raw,spans=_container(path);h=hashlib.sha256();icc=hashlib.sha256()
 with open(path,'rb') as f:
  if kind=='tiff':
   import tifffile
   with tifffile.TiffFile(path) as t:
    p=t.pages[0];shape=p.shape;dtype=str(p.dtype);offsets=list(p.dataoffsets);counts=list(p.databytecounts);profile=p.tags.get(34675);profile=profile.value if profile else None
   for off,n in zip(offsets,counts):
    f.seek(off)
    while n:b=f.read(min(n,1024**2));need(bool(b),'TIFF_STRIP_BOUNDS');h.update(b);n-=len(b)
   return dict(container=kind,image_sha256=h.hexdigest(),shape=list(shape),dtype=dtype,icc_sha256=digest(profile) if profile else None)
  for off,n in spans:
   f.seek(off)
   if kind=='webp':
    tag=f.read(4);f.seek(off)
    if tag==b'VP8X':continue
   while n:b=f.read(min(n,1024**2));need(bool(b),'IMAGE_BOUNDS');h.update(b);n-=len(b)
 return dict(container=kind,image_sha256=h.hexdigest())

def _block_write(target,kind,raw,width,height):
 _,old,spans=_container(target);need(old is None,'OUTPUT_ALREADY_HAS_EXIF')
 need(len(raw)<=LIMIT,'LIMIT')
 if kind=='jpeg':need(len(raw)+8<=65535,'JPEG_BLOCK_TOO_LARGE')
 side=Path(str(target)+'.metadata-stage')
 try:
  with open(target,'rb') as f,side.open('xb') as g:
   if kind=='jpeg':
    need(len(raw)+8<=65535,'JPEG_BLOCK_TOO_LARGE');g.write(b'\xff\xd8\xff\xe1'+struct.pack('>H',len(raw)+8)+b'Exif\0\0'+raw);f.seek(2);_copy(f,g,target.stat().st_size-2)
   elif kind=='png':
    for off,n in spans:
     f.seek(off)
     if off==33:g.write(struct.pack('>I',len(raw))+b'eXIf'+raw+struct.pack('>I',zlib.crc32(b'eXIf'+raw)&0xffffffff))
     _copy(f,g,n)
   elif kind=='webp':
    g.write(b'RIFF\0\0\0\0WEBP');has_vp8x=False
    for off,n in spans:
     f.seek(off);head=f.read(8);tag,length=struct.unpack('<4sI',head)
     if tag==b'VP8X':
      need(length==10,'WEBP_VP8X');b=bytearray(f.read(10));b[0]|=8;g.write(head+b);has_vp8x=True
     else:
      if not has_vp8x:
       g.write(b'VP8X'+struct.pack('<I',10)+bytes([8,0,0,0])+(width-1).to_bytes(3,'little')+(height-1).to_bytes(3,'little'));has_vp8x=True
      f.seek(off);_copy(f,g,n)
    g.write(b'EXIF'+struct.pack('<I',len(raw))+raw+(b'\0' if len(raw)%2 else b''));end=g.tell();g.seek(4);g.write(struct.pack('<I',end-8))
   else:need(False,'OUTPUT_FORMAT')
   g.flush();os.fsync(g.fileno())
  os.replace(side,target)
 finally:
  if side.exists():side.unlink()

def _tiff_splice_plan(sg,tg,source_size,target_size):
 from .export_layout import tiff_plan
 need(source_size==sg.size and target_size==tg.size,'TIFF_INPUT_SIZE_CHANGED')
 return tiff_plan(sg,tg)

def _tiff_preserve(source,target,check=None,*,compact=False,encoded=None):
 """Compact standard metadata; retain the complete source layout for opaque data.
 Opaque bytes stay fixed; new processed pixels and directories only append.
 """
 from .exif_structure import tiff_layout
 from .export_thumbnail import photo_rows
 encoded=Path(encoded) if encoded is not None else target
 with open(source,'rb') as sf,open(encoded,'rb') as tf:
  sg=Graph(sf,Path(source).stat().st_size);tg=Graph(tf,Path(encoded).stat().st_size)
  source_layout=tiff_layout(sg);tiff_layout(tg,single_output=True)
  if compact:
   from .export_tiff_compact import compact_plan
   retained,plan=compact_plan(sg,tg)
  else:retained,plan=sg,_tiff_splice_plan(sg,tg,sg.size,tg.size)
 side=Path(str(target)+'.metadata-stage');owned_side=False;verified=0
 from .storage_guard import reserve,write_chunks
 reserve(side,plan['final_bytes'])
 def before_chunk():
  if check:check()
  reserve(side,1024**2)
 try:
  with side.open('xb+') as out:
   owned_side=True
   with open(source,'rb') as s,open(encoded,'rb') as t:
    for a,b in plan['source_copies']:
     s.seek(a);out.seek(a);_copy(s,out,b-a,before_chunk)
    for off,blob in plan['blobs']:
     if check:check()
     out.seek(off);write_chunks(side,out,blob)
    for old,count,new in plan['transfers']:
     t.seek(old);out.seek(new);_copy(t,out,count,before_chunk)
   out.seek(0);write_chunks(side,out,plan['header']);reserve(side,max(0,plan['final_bytes']-out.seek(0,os.SEEK_END)));out.truncate(plan['final_bytes']);out.flush();os.fsync(out.fileno())
  need(side.stat().st_size==plan['final_bytes'],'TIFF_FINAL_SIZE')
  # Every byte retained for opaque private offsets is checked in-place.
  with open(source,'rb') as a,side.open('rb') as b:
   for start,end in plan['source_copies']:
    pieces=[(start,min(end,4)),(max(start,8),end)] if start<8 and end>4 else [(start,end)]
    for low,high in pieces:
     if high<=low:continue
     a.seek(low);b.seek(low);remaining=high-low
     while remaining:
      if check:check()
      n=min(remaining,1024**2);need(a.read(n)==b.read(n),'TIFF_PRIVATE_BYTES_CHANGED');remaining-=n;verified+=n
  with side.open('rb') as f:out_graph=Graph(f,side.stat().st_size)
  tiff_layout(out_graph,single_output=True)
  expected_rows=photo_rows(retained,tiff=True)
  actual_rows=photo_rows(out_graph,tiff=True)
  if compact:
   order=lambda row:(row['ifd'],row['tag'],row['occurrence'])
   expected_rows=sorted(expected_rows,key=order);actual_rows=sorted(actual_rows,key=order)
  need(expected_rows==actual_rows,'TIFF_METADATA_CHANGED')
  if check:check()
  if encoded == target:os.replace(side,target)
  else:os.link(side,target)
 finally:
  if owned_side and side.exists():side.unlink()
 if compact:
  return {key:plan[key] for key in ('mode','final_bytes','source_image_bytes','source_image_bytes_omitted',
          'source_copied_bytes','omitted_metadata','validated_standard_extensions','processed_stream_bytes','header_and_metadata_bytes','padding_bytes')}
 return dict(method='compact-tiff-metadata-v1' if plan['mode']=='relocated-standard-metadata-v1' else 'private-tiff-conservative-preservation-v2',mode=plan['mode'],final_bytes=plan['final_bytes'],source_image_bytes=plan['source_image_bytes'],source_image_bytes_omitted=plan['source_image_bytes_omitted'],source_copied_bytes=plan['source_copied_bytes'],source_preserved_bytes=plan['source_preserved_bytes'],source_preserved_ranges=plan['source_preserved_ranges'],allowed_source_header_update=plan['allowed_source_header_update'],conservative_reason=plan['conservative_reason'],appended_bytes=plan['appended_bytes'],fixed_private_verified_bytes=verified,opaque_private_relocations=0,standard_metadata_relocated=plan['mode']=='relocated-standard-metadata-v1',private_relocation_blockers=plan['private_relocation_blockers'],fixed_offset_extent=plan['fixed_offset_extent'],source_reduced_ifd=source_layout['reduced'],classic_offset_limit=0xffffffff)


def apply(source,target,expected,width,height,check=None,progress=None):
 source=Path(source);target=Path(target);before=_identity(source);s=inspect_source(source)
 need(policy_request(source,before['sha256'],policy=expected.get('metadata_policy_id'))==expected,'POLICY_CHANGED')
 if progress:progress('保留原图 EXIF')
 if check:check()
 from .export_thumbnail import POLICY as NEW_POLICY,THUMBNAIL_POLICY,remove_source_thumbnail
 from .export_tiff_compact import POLICY as COMPACT_POLICY
 compact=expected['metadata_policy_id']==COMPACT_POLICY
 new_policy=expected['metadata_policy_id'] in (NEW_POLICY,COMPACT_POLICY)
 thumbnail=dict(policy_id=THUMBNAIL_POLICY,status='SINGLE_PRIMARY_CONTAINER',shell_source='FINAL_PRIMARY_IMAGE',removed=[],photographic_entries_preserved=True)
 pixel_before=image_stream_proof(target);kind,raw,_=_container(source);out_kind,_,_=_container(target);changes=[];private=None
 if kind=='tiff':
  need(out_kind=='tiff','TIFF_CROSS_FORMAT_UNPROVEN');private=_tiff_preserve(source,target,check,compact=compact)
  if s['tiff_layout']['reduced']:thumbnail.update(status='SOURCE_THUMBNAIL_UNLINKED',source_preview_bytes_not_exposed=True,private_payload_relocations=0)
 elif raw is not None:
  need(out_kind in ('jpeg','png','webp'),'CROSS_FORMAT_EXIF_UNSUPPORTED');g=Graph(io.BytesIO(raw),len(raw));normalized,changes=g.normalized(raw,width,height)
  if new_policy:normalized,thumbnail=remove_source_thumbnail(normalized)
  _block_write(target,out_kind,normalized,width,height)
 if progress:progress('校验导出文件')
 if check:check()
 after=image_stream_proof(target);need(pixel_before==after,'IMAGE_OR_ICC_CHANGED');out=inspect_source(target)
 if kind=='tiff':
  if not compact:need(s['rows']==out['rows'],'TIFF_METADATA_CHANGED')
 elif raw is not None:
  _,result_raw,_=_container(target);need(result_raw==normalized,'RAW_BLOCK_CHANGED')
 else:need(out['source_exif_state']=='SOURCE_EXIF_ABSENT','UNEXPECTED_EXIF')
 independent=independent_readback(source,target,check,thumbnail_removed=new_policy,compact=compact);need(_identity(source)==before,'SOURCE_CHANGED')
 result=dict(**expected,status='SOURCE_EXIF_PRESERVED' if s['source_exif_state']=='SOURCE_EXIF_PRESENT' else 'SOURCE_EXIF_ABSENT',source_file_sha256=before['sha256'],output_exif_sha256=out['source_exif_sha256'],output_semantic_sha256=out['source_exif_semantic_sha256'],allowed_changes=changes,private_data_proof=private,image_stream_before=pixel_before,image_stream_after=after,independent_readback=independent)
 if new_policy:result['thumbnail']=thumbnail
 return result
