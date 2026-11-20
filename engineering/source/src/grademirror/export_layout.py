"""Metadata-only export layout planning. Never decodes or re-encodes pixels.
Known self-contained metadata can move; opaque private data uses fixed offsets.
"""
import struct
from .exif_structure import LINKS,STORAGE,need

TIFF_SAFE={269,270,271,272,274,282,283,285,286,287,296,297,301,305,306,315,316,318,319,33432,700,33723,34665,34853}
EXIF_SAFE={33434,33437,34850,34852,34855,34856,34864,34865,34866,34867,34868,34869,36864,36867,36868,36880,36881,36882,37121,37122,37377,37378,37379,37380,37381,37382,37383,37384,37385,37386,37396,37510,37520,37521,37522,40960,40961,40962,40963,40965,41483,41484,41486,41487,41488,41492,41493,41495,41728,41729,41730,41985,41986,41987,41988,41989,41990,41991,41992,41993,41994,41995,41996,42016,42032,42033,42034,42035,42036,42037,42240}

def merge_ranges(ranges):
 out=[]
 for a,b in sorted((a,b) for a,b in ranges if b>a):
  if out and a<=out[-1][1]:out[-1]=(out[-1][0],max(b,out[-1][1]))
  else:out.append((a,b))
 return out

def subtract_ranges(ranges,protected):
 protected=merge_ranges(protected);out=[];j=0
 for a,b in merge_ranges(ranges):
  while j<len(protected) and protected[j][1]<=a:j+=1
  pos=a;k=j
  while k<len(protected) and protected[k][0]<b:
   x,y=protected[k]
   if pos<x:out.append((pos,min(x,b)))
   pos=max(pos,y)
   if pos>=b:break
   k+=1
  if pos<b:out.append((pos,b))
 return out

def metadata_ranges(g):
 out=[(0,8)]
 for off in g.visited:
  n=g.unpack('H',g.read(off,2));out.append((off,off+2+12*n+4))
 for r in g.rows:
  if 'value_offset' in r:out.append((r['value_offset'],r['value_offset']+len(r['_raw'])))
 return merge_ranges(out)

def image_ranges(g,paths):
 out=[]
 for path in paths:
  by={r['tag']:r for r in g.rows if r['ifd']==path}
  for offset_tag,count_tag in ((273,279),(324,325),(513,514)):
   if offset_tag not in by and count_tag not in by:continue
   need(offset_tag in by and count_tag in by,'IMAGE_OFFSET_PAIR')
   offsets=g.ints(by[offset_tag]);counts=g.ints(by[count_tag]);need(len(offsets)==len(counts),'IMAGE_OFFSET_COUNT')
   for off,count in zip(offsets,counts):
    need(0<=off<=g.size and 0<count<=g.size-off,'IMAGE_PAYLOAD_BOUNDS');out.append((off,off+count))
 return merge_ranges(out)

def photoshop_self_contained(raw):
 """Only resource types with self-contained IPTC/XMP/thumbnail/digest payloads."""
 pos=0;seen=0
 while pos<len(raw):
  if pos+12>len(raw) or raw[pos:pos+4]!=b'8BIM':return False
  ident=struct.unpack('>H',raw[pos+4:pos+6])[0]
  if ident not in (0x0404,0x0409,0x040c,0x0424,0x0425):return False
  name=1+raw[pos+6];name+=name%2;head=pos+6+name
  if head+4>len(raw):return False
  length=struct.unpack('>I',raw[head:head+4])[0];end=head+4+length
  if end>len(raw):return False
  if ident==0x0425 and length!=16:return False
  if ident in (0x0409,0x040c):
   if length<28:return False
   data=raw[head+4:end];fmt=struct.unpack('>I',data[:4])[0];packed=struct.unpack('>I',data[20:24])[0]
   if fmt!=1 or packed!=length-28:return False
  pos=end+(length%2);seen+=1
  if seen>4096:return False
 return pos==len(raw)

def relocatable(g, *, validated_exif=()):
 from .export_thumbnail import IMAGE_TAGS
 reasons=[]
 for r in g.rows:
  path=r['ifd'];tag=r['tag']
  if tag=='JPEGPreview':
   if path!='IFD0/Next':reasons.append([path,tag])
   continue
  if path=='IFD0/Next' and tag in IMAGE_TAGS:continue
  if r['type']==13 and tag not in LINKS:reasons.append([path,tag]);continue
  if tag in LINKS:
   if r['type'] not in (4,13) or r['occurrence']:reasons.append([path,tag])
   continue
  if path in ('IFD0','IFD0/Next'):
   safe=tag in STORAGE and path=='IFD0' or tag in TIFF_SAFE or tag==34377 and photoshop_self_contained(r['_raw'])
  elif path.endswith('/Exif0'):safe=(tag in EXIF_SAFE and tag!=41995) or (path,tag,r['occurrence']) in validated_exif
  elif path.endswith('/GPS0'):safe=type(tag)is int and 0<=tag<=31
  elif path.endswith('/Interop0'):safe=tag in (1,2,4096,4097,4098)
  else:safe=False
  if not safe:reasons.append([path,tag])
 return not reasons,reasons

class Allocator:
 def __init__(self,free=None,reserved_end=8):
  self.linear=free is None;self.free=list(free or []);self.end=reserved_end;self.allocated_end=8
 def take(self,n):
  need(type(n)is int and n>0,'ALLOCATION_SIZE')
  if not self.linear:
   for a,b in self.free:
    pos=a+(a%2)
    if pos+n<=b:
     need(pos+n<=0xffffffff,'TIFF_CLASSIC_OFFSET_LIMIT')
     self.free=subtract_ranges(self.free,[(pos,pos+n)]);self.allocated_end=max(self.allocated_end,pos+n);return pos
  pos=max(self.end,self.allocated_end);pos+=pos%2;need(pos+n<=0xffffffff,'TIFF_CLASSIC_OFFSET_LIMIT');self.allocated_end=pos+n
  if self.linear:self.end=pos+n
  else:self.free=subtract_ranges(self.free,[(pos,pos+n)])
  return pos

def tiff_plan(sg,tg,*,validated_exif=()):
 from .export_thumbnail import IMAGE_TAGS
 from .image_limits import MAX_IMAGE_BYTES
 source_main=[r for r in sg.rows if r['ifd']=='IFD0'];target_main=[r for r in tg.rows if r['ifd']=='IFD0'];sb={r['tag']:r for r in source_main};tb={r['tag']:r for r in target_main}
 for tag in (256,257,258,277):need(tag in sb and tag in tb and sg.ints(sb[tag])==tg.ints(tb[tag]),'TIFF_MAIN_SCHEMA_CHANGED')
 need(sg.end==tg.end,'TIFF_ENDIAN_ENCODING_UNSUPPORTED');need(sb.get(34675,{}).get('_raw')==tb.get(34675,{}).get('_raw'),'TIFF_ICC_CHANGED')
 offset_tag=273 if 273 in tb else 324;count_tag=279 if offset_tag==273 else 325;offsets=tg.ints(tb[offset_tag]);counts=tg.ints(tb[count_tag]);need(len(offsets)==len(counts)>0,'TIFF_STORAGE_COUNT')
 safe,reasons=relocatable(sg,validated_exif=validated_exif);pixels=image_ranges(sg,('IFD0','IFD0/Next'));free=subtract_ranges(pixels,metadata_ranges(sg)) if safe else [];copies=[] if safe else [(0,sg.size)];reserved_end=max((b for a,b in copies),default=8);alloc=Allocator(None if safe else free,reserved_end)
 groups={}
 for r in sg.rows:
  if r['tag']!='JPEGPreview':groups.setdefault(r['ifd'],[]).append(dict(r,target=False))
 groups['IFD0']=[dict(r,target=False) for r in source_main if r['tag'] not in STORAGE]+[dict(r,target=True) for r in target_main if r['tag'] in STORAGE]
 groups['IFD0'].sort(key=lambda r:r['tag'])
 if 'IFD0/Next' in groups:
  groups['IFD0/Next']=[r for r in groups['IFD0/Next'] if r['tag'] not in IMAGE_TAGS]
  if not groups['IFD0/Next']:del groups['IFD0/Next']
 if not safe:groups={p:rows for p,rows in groups.items() if p in ('IFD0','IFD0/Next')}
 else:
  for path,rows in list(groups.items()):
   for r in rows:
    if r['tag'] in LINKS:
     for k,off in enumerate(sg.ints(r)):groups.setdefault(path+'/'+LINKS[r['tag']]+str(k),[])
 destinations=[alloc.take(n) for n in counts] if not safe else []
 dir_offsets={path:alloc.take(2+12*len(rows)+4) for path,rows in groups.items()};entries={};blobs={};pixel_entry=None
 for path,rows in groups.items():
  encoded=[]
  for r in rows:
   if not safe and not r['target']:
    encoded.append(bytearray(r['_entry']));continue
   e=bytearray(r['_entry']);raw=r['_raw']
   if r['target'] and r['tag']==offset_tag:
    raw=b'\0'*(4*len(counts));e[2:4]=struct.pack(sg.end+'H',4)
   elif r['tag'] in LINKS:
    raw=struct.pack(sg.end+str(r['count'])+'I',*[dir_offsets[path+'/'+LINKS[r['tag']]+str(k)] for k in range(r['count'])])
   slot=None
   if len(raw)<=4:e[8:12]=raw.ljust(4,b'\0')
   else:
    slot=alloc.take(len(raw));e[8:12]=struct.pack(sg.end+'I',slot);blobs[slot]=raw
   encoded.append(e)
   if r['target'] and r['tag']==offset_tag:pixel_entry=(e,slot)
  entries[path]=encoded
 if safe:destinations=[alloc.take(n) for n in counts]
 raw=struct.pack(sg.end+str(len(counts))+'I',*destinations);e,slot=pixel_entry
 if slot is None:e[8:12]=raw.ljust(4,b'\0')
 else:blobs[slot]=raw
 for path,rows in groups.items():
  nxt=dir_offsets.get(path+'/Next',0);blobs[dir_offsets[path]]=struct.pack(sg.end+'H',len(rows))+b''.join(entries[path])+struct.pack(sg.end+'I',nxt)
 final_size=max(reserved_end,alloc.allocated_end);need(final_size<=MAX_IMAGE_BYTES,'TIFF_FINAL_OUTPUT_BUDGET' if safe else 'TIFF_OPAQUE_FULL_LAYOUT_OUTPUT_BUDGET');need(final_size<=0xffffffff,'TIFF_CLASSIC_OFFSET_LIMIT')
 header=(b'II' if sg.end=='<' else b'MM')+struct.pack(sg.end+'HI',42,dir_offsets['IFD0'])
 return dict(mode='relocated-standard-metadata-v1' if safe else 'preserved-opaque-source-layout-v2',private_relocation_blockers=reasons,source_copies=copies,blobs=sorted(blobs.items()),transfers=list(zip(offsets,counts,destinations)),header=header,final_bytes=final_size,source_image_bytes=sum(b-a for a,b in pixels),source_image_bytes_omitted=sum(b-a for a,b in free),source_copied_bytes=sum(b-a for a,b in copies),source_preserved_bytes=sg.size-4 if not safe else 0,source_preserved_ranges=[[0,4],[8,sg.size]] if not safe else [],allowed_source_header_update=[4,8] if not safe else None,conservative_reason='OPAQUE_REFERENCE_CLOSURE_NOT_PROVEN_KEEP_FULL_SOURCE_LAYOUT' if not safe else None,appended_bytes=final_size-sg.size if not safe else 0,metadata_value_bytes=sum(len(v) for v in blobs.values()),fixed_offset_extent=reserved_end if not safe else 0)

def compact_thumbnail(raw,g,keep):
 """Reclaim derived pixels only for proven self-contained standard metadata.
 Opaque references keep every original payload byte and its fixed position;
 only the old visible thumbnail-directory link changes, and new dirs append.
 """
 from .exif_structure import LIMIT
 safe,reasons=relocatable(g);skipped=None
 if safe:
  try:free=subtract_ranges(image_ranges(g,('IFD0/Next',)),metadata_ranges(g))
  except ValueError as exc:free=[];skipped=str(exc)
 else:free=[]
 copies=subtract_ranges([(0,len(raw))],free);reserved=max((b for a,b in copies),default=8);alloc=Allocator(free,reserved);offset=alloc.take(2+12*len(keep)+4) if keep else 0;size=max(reserved,alloc.allocated_end)
 need(size<=LIMIT,'LIMIT' if safe else 'OPAQUE_PRESERVATION_BLOCK_LIMIT')
 out=bytearray(size)
 for a,b in copies:out[a:b]=raw[a:b]
 if keep:out[offset:offset+2+12*len(keep)+4]=struct.pack(g.end+'H',len(keep))+b''.join(r['_entry'] for r in keep)+b'\0'*4
 first=struct.unpack(g.end+'I',raw[4:8])[0];n=struct.unpack(g.end+'H',raw[first:first+2])[0];link=first+2+12*n;out[link:link+4]=struct.pack(g.end+'I',offset)
 if not safe:need(out[:link]==raw[:link] and out[link+4:len(raw)]==raw[link+4:],'OPAQUE_LAYOUT_CHANGED')
 return bytes(out),dict(method='standard-thumbnail-compaction-v1' if safe else 'preserved-opaque-exif-layout-v2',source_bytes=len(raw),output_bytes=size,obsolete_preview_bytes_omitted=sum(b-a for a,b in free),skipped_payload_compaction=skipped,conservative_reason='OPAQUE_REFERENCE_CLOSURE_NOT_PROVEN_KEEP_FULL_EXIF_LAYOUT' if not safe else None,private_relocation_blockers=reasons,original_layout_bytes=len(raw) if not safe else 0,preserved_source_ranges=[[0,link],[link+4,len(raw)]] if not safe else [],allowed_directory_link_update=[link,link+4],appended_bytes=size-len(raw) if not safe else 0)
