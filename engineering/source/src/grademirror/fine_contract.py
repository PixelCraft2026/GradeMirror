"""Standard-library fine recipe and b64 archive verification; never computes pixels."""
import ast,hashlib,io,math,struct,zipfile
from pathlib import Path
from .common import GMError,canonical_json,sha_file
from .engine.independent_r0_p3_lld10 import profile as fine
from .engine.independent_r0 import profile as fast

def fail(message):raise GMError('FINE_RECIPE_INVALID',message,7)
def digest(metadata):return hashlib.sha256(canonical_json({k:v for k,v in metadata.items()if k not in {'recipe_sha256','archive_sha256'}})).hexdigest()
def coefficient_shape(reference_shape,*,limits=None):
    limits=fine.LIMITS if limits is None else limits
    h,w=reference_shape[:2];k=(math.ceil(w/8)+1)*(math.ceil(h/8)+1)
    if k>limits['K']:fail('P3 coefficient geometry exceeds limit')
    return [k,3]
def geometry_issues(source=None,reference=None,*,limits=None):
    """Explain known header limits, including while only one import has finished."""
    limits=fine.LIMITS if limits is None else limits
    issues=[]
    for label,info,pixel_limit in (('原图',source,limits['native_pixels']),
                                  ('参考图',reference,limits['reference_pixels'])):
        if info is None:continue
        w,h=info['width'],info['height']
        if any(type(n)is not int or n<1 for n in (w,h)):
            issues.append(f'{label}尺寸须为正整数像素');continue
        exceeded=[]
        if w*h>pixel_limit:exceeded.append(f'像素数上限 {pixel_limit:,}')
        if label=='参考图'and max(w,h)>limits['reference_max_axis']:
            exceeded.append(f'最长边上限 {limits["reference_max_axis"]:,} 像素')
        if exceeded:issues.append(f'{label} {w} × {h}（{w*h:,} 像素）超出精细模式的'+ '、'.join(exceeded))
    return issues

def validate_limits(source,reference,*,limits=None):
    issues=geometry_issues(source,reference,limits=limits)
    if issues:
        raise GMError('FINE_GEOMETRY_UNSUPPORTED','；'.join(issues)+'。请选择尺寸符合限制的图片。',3)
    rw,rh=reference['width'],reference['height']
    coefficient_shape([rh,rw,3],limits=limits)
def read_coefficients(path,shape,expected_archive):
    p=Path(path)
    if not isinstance(shape,list)or len(shape)!=2 or any(type(n)is not int for n in shape)or shape[1]!=3 or not 1<=shape[0]<=fine.LIMITS['K']:fail('P3 coefficient shape quota')
    raw_bytes=math.prod(shape)*8
    archive_cap=min(fine.LIMITS['archive_bytes'],raw_bytes+math.ceil(raw_bytes/1000)+65536)
    if p.stat().st_size>archive_cap or sha_file(p)!=expected_archive:fail('P3 archive quota or hash differs')
    try:
        with zipfile.ZipFile(p)as z:
            infos=z.infolist()
            if len(infos)!=1 or infos[0].filename!='b.npy'or infos[0].file_size>raw_bytes+2058 or infos[0].flag_bits&1:fail('P3 archive member set/quota differs')
            data=z.read(infos[0])
        if data[:8]!=b'\x93NUMPY\x01\x00'or len(data)<10:fail('P3 NPY version differs')
        length=struct.unpack('<H',data[8:10])[0]
        if not 0<length<=2048 or 10+length>len(data):fail('P3 NPY header quota')
        header=ast.literal_eval(data[10:10+length].decode('ascii').strip());payload=data[10+length:]
        if header!={'descr':'<f8','fortran_order':False,'shape':tuple(shape)}or len(payload)!=math.prod(shape)*8:fail('P3 dtype/shape differs')
        if any(not math.isfinite(v[0])for v in struct.iter_unpack('<d',payload)):fail('P3 nonfinite coefficients')
        return payload
    except (OSError,ValueError,SyntaxError,zipfile.BadZipFile,struct.error,UnicodeError)as exc:fail('Unreadable P3 archive: '+str(exc))
def validate_recipe(metadata,coefficient_path=None):
    r0=metadata.get('r0_recipe');post=metadata.get('postprocess')
    if not isinstance(r0,dict)or not isinstance(post,dict):fail('Missing R0/P3/LLF provenance')
    added={'r0_recipe','postprocess','p3_archive_sha256','archive_sha256'}
    if set(metadata)!=set(r0)|added:fail('Fine recipe fields differ')
    from .engine.profiles import recipe_profile
    bound=recipe_profile(metadata)
    if bound['id']!=fine.PROFILE_ID:fail('Fine profile identity differs')
    if (r0.get('profile_id')!=fast.PROFILE_ID or r0.get('recipe_schema_version')!='0.4.0'or r0.get('profile_contract_sha256')!=fast.PROFILE_CONTRACT_SHA256 or r0.get('core_sha256')!=fast.CORE_SHA256 or digest(r0)!=r0.get('recipe_sha256')):fail('Embedded R0 contract differs')
    replaced={'profile_id','recipe_schema_version','core_sha256','profile_contract_sha256','recipe_sha256'}
    if any(metadata.get(k)!=v for k,v in r0.items()if k not in replaced):fail('Fine and embedded R0 input/parameter binding differs')
    if metadata.get('recipe_kind')!='development_image'or metadata.get('production_qualified')is not False:fail('Fine recipe is not a product image recipe')
    expected={'version','P3','LLF','solver_runtime','ranges','hashes','witnesses','coefficient_shape','coefficient_sha256','reference_normalization','solver'}
    if set(post)!=expected or post['version']!='gm008-post-v1'or post['P3']!=fine.P3 or post['LLF']!=fine.LLF or post['solver_runtime']!=fine.RUNTIME['solver']:fail('Fine stage contract differs')
    sh=metadata['source_identity']['shape'];rh=metadata['reference_identity']['shape']
    limits=fine.LEGACY_LIMITS if bound['contract_sha256']==fine.LEGACY_CONTRACT_SHA256 else fine.LIMITS
    validate_limits(dict(width=sh[1],height=sh[0]),dict(width=rh[1],height=rh[0]),limits=limits)
    shape=coefficient_shape(rh,limits=limits)
    if post['coefficient_shape']!=shape:fail('P3 coefficient shape differs')
    denom={'uint8':255,'uint16':65535}.get(metadata['reference_identity']['dtype'])
    if denom is None or post['reference_normalization']!={'dtype':'float64','denominator':denom,'grid':rh,'intermediate_float32':False}:fail('P3 reference precision differs')
    ranges=post['ranges']
    if not isinstance(ranges,list)or len(ranges)!=3:fail('Missing pair ranges')
    for row in ranges:
        if set(row)!={'kmin','kmax','count','min','max','delta','sigma','J'}:fail('Range fields differ')
        if any(type(row[k])is not int for k in ('kmin','kmax','count','J'))or any(type(row[k])not in (int,float)or not math.isfinite(row[k])for k in ('min','max','delta','sigma')):fail('Invalid pair ranges')
        if not(row['min']<=row['max']and row['kmin']==math.floor((row['min']-2/255)*64)-1 and row['kmax']==math.ceil((row['max']+2/255)*64)+1 and 2<=row['count']==row['kmax']-row['kmin']+1<=257 and row['delta']==1/64 and row['sigma']==.1 and row['J']==8):fail('Pair absolute range differs')
    hashes=post['hashes']
    if not isinstance(hashes,dict)or set(hashes)!={'O','OP','LO','LP','Y'}:fail('Fine native proof set differs')
    witnesses=post['witnesses']
    xy=[[int((i+.5)*sh[1]/8),int((j+.5)*sh[0]/8)]for j in range(8)for i in range(8)]
    if not isinstance(witnesses,dict)or set(witnesses)!=set(hashes):fail('Fine witness set differs')
    for row in witnesses.values():
        if not isinstance(row,dict)or set(row)!={'xy','values'}or row['xy']!=xy:fail('Fine witness coordinates differ')
        values=row['values']
        if not isinstance(values,list)or len(values)!=64 or any(not isinstance(v,list)or len(v)!=3 or any(type(x)not in (int,float)or not math.isfinite(x)for x in v)for v in values):fail('Fine witness values differ')
    for value in [*hashes.values(),post['coefficient_sha256'],metadata['p3_archive_sha256'],metadata['archive_sha256']]:
        if not isinstance(value,str)or len(value)!=64 or any(c not in '0123456789abcdef'for c in value):fail('Invalid fine digest')
    if digest(metadata)!=metadata.get('recipe_sha256'):fail('Fine recipe digest differs')
    if coefficient_path is not None:
        payload=read_coefficients(coefficient_path,shape,metadata['p3_archive_sha256'])
        if hashlib.sha256(payload).hexdigest()!=post['coefficient_sha256']:fail('P3 values digest differs')
    return metadata

def validate_compute(compute,hashes,*,replay=False,ranges=None):
    c=compute.get('postprocess_counts',{})
    from .engine.profiles import get_profile
    bound=get_profile(compute.get('profile_id'),compute.get('profile_contract_sha256'))
    if bound['id']!=fine.PROFILE_ID or compute.get('profile_contract_sha256')!=bound['contract_sha256']:fail('Fine compute profile differs')
    if compute.get('postprocess_hashes')!=hashes or compute.get('float32_sha256')!=hashes['Y']:fail('Fine compute stage proof differs')
    if any(c.get(k)!=v for k,v in {'P3_native_fields':1,'native_RGB_LLF':2,'native_scalar_LLF':6,'final_compositions':1}.items()):fail('Fine compute stage counts differ')
    if compute.get('scalar_CG')not in ({0}if replay else{3,6}):fail('Fine CG count differs')
    if ranges is not None:
        remaps=2*sum(row['count']for row in ranges)
        if c.get('native_remap_pyramids')!=remaps or c.get('D_calls')!=(remaps+6)*7 or not 0<c.get('U_calls',0)<=remaps*14+42:fail('Fine LLF operator counts differ')
