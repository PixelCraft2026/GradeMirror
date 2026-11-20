"""Strict NPZ/NPY byte validation, with the frozen R0 parameter shapes."""
import ast,hashlib,io,math,struct,zipfile
from .primitives import require,RecipeError,read_bytes,EXPANDED_LIMIT,BUNDLE_LIMIT
PARAMETER_SHAPES={'color_features.weight':(32,3,1,1),'color_features.bias':(32,),'position_features.weight':(32,2,1,1),'position_features.bias':(32,),'mix_features.weight':(64,64,1,1),'mix_features.bias':(64,),'neighborhood.weight':(64,1,3,3),'decode_features.weight':(64,64,1,1),'decode_features.bias':(64,),'edit_head.weight':(3,64,1,1),'edit_head.bias':(3,),'rgb_extra.0.weight':(32,32,1,1),'rgb_extra.0.bias':(32,),'xy_extra.0.weight':(32,32,1,1),'xy_extra.0.bias':(32,)}
def npy(data,dtype,shape):
    require(data[:6]==b'\x93NUMPY' and data[6:8]in (b'\x01\x00',b'\x02\x00',b'\x03\x00'),'NPY_MAGIC_VERSION')
    width=2 if data[6]==1 else 4;require(len(data)>=8+width,'NPY_HEADER');n=int.from_bytes(data[8:8+width],'little');require(1<=n<=4096 and len(data)>=8+width+n,'NPY_HEADER_SIZE')
    try:h=ast.literal_eval(data[8+width:8+width+n].decode('utf8'if data[6]==3 else'latin1').strip())
    except (ValueError,SyntaxError,UnicodeError)as e:raise RecipeError('NPY_HEADER',str(e))from e
    require(type(h)is dict and set(h)=={'descr','fortran_order','shape'} and h['descr']==dtype and h['fortran_order']is False and type(h['shape'])is tuple and h['shape']==tuple(shape) and all(type(x)is int and x>0 for x in h['shape']),'NPY_LAYOUT')
    body=data[8+width+n:];require(len(body)==math.prod(shape)*(4 if dtype=='<f4'else 8),'NPY_LENGTH');fmt='<f'if dtype=='<f4'else'<d'
    require(all(math.isfinite(v[0])for v in struct.iter_unpack(fmt,body)),'NONFINITE_ARRAY')
    return body,dict(shape=list(shape),dtype=dtype,bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
def parse_npz(data,kind):
    require(type(data)is bytes and len(data)<=BUNDLE_LIMIT,'NPZ_SIZE');shapes=PARAMETER_SHAPES if kind=='r0'else{'b':(24897,3)};dtype='<f4'if kind=='r0'else'<f8';arrays={};expanded=0
    try:
        with zipfile.ZipFile(io.BytesIO(data))as z:
            entries=z.infolist();names=[e.filename for e in entries];require(len(names)==len(set(names)) and set(names)=={k+'.npy'for k in shapes},'NPZ_MEMBERS')
            require(sum(e.file_size for e in entries)<=EXPANDED_LIMIT,'NPZ_EXPANDED_LIMIT')
            for e in entries:
                require(not e.flag_bits&1 and e.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED) and not e.is_dir() and e.file_size>0 and e.compress_size>0 and e.file_size<=max(4096,e.compress_size*200),'NPZ_COMPRESSION')
                raw=z.read(e);require(len(raw)==e.file_size,'NPZ_LENGTH');expanded+=len(raw);key=e.filename[:-4];body,info=npy(raw,dtype,shapes[key]);arrays[key]=(body,info)
    except RecipeError:raise
    except (OSError,ValueError,zipfile.BadZipFile,RuntimeError)as e:raise RecipeError('NPZ_INVALID',str(e))from e
    h=hashlib.sha256()
    for key in sorted(arrays):h.update(key.encode()+b'\0');h.update(arrays[key][0])
    return dict(members={k:v[1]for k,v in arrays.items()},value_sha256=h.hexdigest()if kind=='r0'else arrays['b'][1]['sha256'],expanded_bytes=expanded)
def parse_file(path,kind):return parse_npz(read_bytes(path,BUNDLE_LIMIT),kind)
