"""Bounded, read-only classic TIFF/EXIF graph inspection. Never relocates private data."""
import hashlib,struct
LIMIT=16*1024**2
SIZES={1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8,13:4}
LINKS={34665:'Exif',34853:'GPS',40965:'Interop',330:'SubIFD'}
# Main-image representation only. Never applied recursively to preview/private IFDs.
STORAGE={254,256,257,258,259,262,266,273,274,277,278,279,284,317,322,323,324,325,338,339,34675}
def need(ok,code):
 if not ok:raise ValueError('SOURCE_EXIF_'+code)
def digest(b):return hashlib.sha256(b).hexdigest()
class Graph:
 def __init__(self,stream,size):
  self.f=stream;self.size=size;self.used=0;self.rows=[];self.visited=set();self.ranges=[]
  h=self.read(0,8);need(h[:2] in (b'II',b'MM'),'BYTE_ORDER');self.end='<' if h[:2]==b'II' else '>';need(self.unpack('H',h[2:4])==42,'CLASSIC_TIFF_REQUIRED');self.walk(self.unpack('I',h[4:8]),'IFD0',0)
 def unpack(self,fmt,b):return struct.unpack(self.end+fmt,b)[0]
 def read(self,off,n):
  need(type(off)is int and 0<=off<=self.size and 0<=n<=self.size-off,'BOUNDS');self.used+=n;need(self.used<=LIMIT,'LIMIT');self.f.seek(off);b=self.f.read(n);need(len(b)==n,'TRUNCATED');return b
 def ints(self,row):
  need(row['type'] in (3,4,13),'POINTER_TYPE');fmt='H' if row['type']==3 else 'I';return list(struct.unpack(self.end+str(row['count'])+fmt,row['_raw']))
 def walk(self,off,path,depth):
  need(off not in self.visited and depth<=12 and len(self.visited)<128,'CYCLIC_OR_DEEP');self.visited.add(off)
  n=self.unpack('H',self.read(off,2));need(n<=4096,'TAG_COUNT');table=self.read(off+2,n*12+4);local=[];seen={}
  for i in range(n):
   e=table[i*12:i*12+12];tag,typ,count=struct.unpack(self.end+'HHI',e[:8]);need(typ in SIZES,'UNKNOWN_TYPE');length=count*SIZES[typ];need(length<=LIMIT,'VALUE_LIMIT');v=off+2+i*12+8 if length<=4 else self.unpack('I',e[8:]);raw=self.read(v,length);occ=seen.get(tag,0);seen[tag]=occ+1
   row=dict(ifd=path,tag=tag,occurrence=occ,type=typ,count=count,sha256=digest(raw),value_offset=v,entry_offset=off+2+i*12,_raw=raw,_entry=e);local.append(row);self.rows.append(row)
  for row in local:
   if row['tag'] in LINKS:
    for k,v in enumerate(self.ints(row)):self.walk(v,path+'/'+LINKS[row['tag']]+str(k),depth+1)
  by={r['tag']:r for r in local}
  if 513 in by or 514 in by:
   need(513 in by and 514 in by,'THUMBNAIL_PAIR');a,b=self.ints(by[513]),self.ints(by[514]);need(len(a)==len(b)==1,'THUMBNAIL_SCHEMA');blob=self.read(a[0],b[0]);self.rows.append(dict(ifd=path,tag='JPEGPreview',occurrence=0,type=7,count=len(blob),sha256=digest(blob),_raw=blob))
  nxt=self.unpack('I',table[-4:]);
  if nxt:self.walk(nxt,path+'/Next',depth+1)
 def semantic(self,tiff=False):
  out=[]
  for r in self.rows:
   tag=r['tag'];path=r['ifd']
   if tiff and path=='IFD0' and tag in STORAGE:continue
   d={k:r[k] for k in ('ifd','tag','occurrence','type','count','sha256')}
   if tag in LINKS or tag==513:d['sha256']='STRUCTURAL_OFFSET'
   out.append(d)
  return out
 def normalized(self,raw,width,height):
  b=bytearray(raw);changes=[]
  for r in self.rows:
   tag,path=r['tag'],r['ifd'];value=1 if path=='IFD0' and tag==274 else width if (path=='IFD0' and tag==256) or (path=='IFD0/Exif0' and tag==40962) else height if (path=='IFD0' and tag==257) or (path=='IFD0/Exif0' and tag==40963) else None
   if value is None:continue
   need(r['count']==1 and r['type'] in (3,4),'REPRESENTATION_TYPE');old=self.ints(r)[0];fmt='H' if r['type']==3 else 'I';packed=struct.pack(self.end+fmt,value);a=r['value_offset'];b[a:a+len(packed)]=packed
   if old!=value:changes.append(dict(ifd=path,tag=tag,before=old,after=value,reason='actual normalized main image representation'))
  return bytes(b),changes

def tiff_layout(graph, *, allow_rgb8=True, single_output=False):
 """One RGB main image, optionally one reduced RGB8 image or metadata-only IFD.
 Shared by source preflight, codec headers and EXIF migration; reads no pixels.
 """
 from .export_thumbnail import IMAGE_TAGS
 from .image_limits import check_geometry
 need(not any(r['ifd'].startswith('IFD0/Next/Next') for r in graph.rows),'TIFF_UNDECLARED_PAGES')
 need(not any(r['tag']==330 for r in graph.rows),'TIFF_UNDECLARED_SUBIFDS')
 headers=[];profile=None;metadata_tail=False
 for index,path in enumerate(('IFD0','IFD0/Next')):
  rows=[r for r in graph.rows if r['ifd']==path]
  if not rows:
   need(index!=0,'TIFF_EMPTY_MAIN');continue
  by={r['tag']:r for r in rows}
  for tag in IMAGE_TAGS:need(sum(r['tag']==tag for r in rows)<=1,'TIFF_DUPLICATE_IMAGE_TAG')
  if index and not any(r['tag'] in IMAGE_TAGS or r['tag']=='JPEGPreview' for r in rows):
   metadata_tail=True;continue
  def ints(tag,default=None):
   if tag not in by:return default
   need(by[tag]['type'] in (3,4),'TIFF_INTEGER_SCHEMA');return graph.ints(by[tag])
  def one(tag,default=None):
   values=ints(tag,[default]);need(len(values)==1 and type(values[0]) is int,'TIFF_SCALAR_SCHEMA');return values[0]
  bits=ints(258,[]);w=one(256);h=one(257);compression=one(259,1)
  need(compression>0,'TIFF_COMPRESSION');check_geometry(w,h)
  photo=one(262);samples=one(277,1);planar=one(284,1);orientation=one(274,1);subtype=one(254,0);sample=ints(339,[1])
  need(photo==2 and samples==3 and planar==1 and orientation==1 and all(x==1 for x in sample) and len(sample) in (1,3) and not ints(338,[]),'TIFF_RGB_SCHEMA')
  icc=by.get(34675,{}).get('_raw')
  if not index:
   need(bits==[16]*3 or allow_rgb8 and bits==[8]*3,'TIFF_MAIN_SCHEMA');need(subtype==0,'TIFF_MAIN_SCHEMA');profile=icc
  else:
   need(not single_output,'TIFF_OUTPUT_MULTIPLE_PAGES')
   need(subtype==1 and bits==[8]*3 and w<headers[0]['width'] and h<headers[0]['height'],'TIFF_UNDECLARED_MAIN_IMAGE')
   need(icc is None or icc==profile,'COLOR_THUMBNAIL_ICC_CONFLICT')
  strips=273 in by or 279 in by;tiles=324 in by or 325 in by
  need(strips!=tiles,'TIFF_STORAGE_SCHEMA')
  if strips:
   offsets=ints(273,[]);counts=ints(279,[]);rps=one(278,h);need(rps>0,'TIFF_ROWS_PER_STRIP');expected=(h+rps-1)//rps
  else:
   offsets=ints(324,[]);counts=ints(325,[]);tw=one(322);th=one(323);need(tw>0 and th>0,'TIFF_TILE_GEOMETRY');expected=((w+tw-1)//tw)*((h+th-1)//th)
  need(len(offsets)==len(counts)==expected and expected>0,'TIFF_STORAGE_COUNT')
  need(all(0<o<=graph.size and 0<n<=graph.size-o for o,n in zip(offsets,counts)),'TIFF_STORAGE_BOUNDS')
  headers.append(dict(ifd=index,width=w,height=h,bits_per_sample=bits,compression=compression,photometric=photo,samples_per_pixel=samples,planar_configuration=planar,orientation=orientation,new_subfile_type=subtype,sample_format=1,extra_samples=[],icc_tag_present=icc is not None))
 return dict(headers=headers,icc_bytes=profile,reduced=len(headers)==2,metadata_tail=metadata_tail,metadata_bytes_read=graph.used)
