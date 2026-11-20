"""GM064 R3's explicitly adopted, metadata-only public recovery capability.

The old failed fit is immutable input. This capability never grants a fit,
render or lease; the normal replay/refine APIs grant their own operations.
"""
import ctypes
import hashlib
import os
import struct
import time
from contextlib import contextmanager
from pathlib import Path
from ctypes import wintypes as w
from ..common import atomic_json
from ..cpu_recipe.guard import metadata_api
from ..cpu_recipe.primitives import digest
from ..cpu_replay.contract import binding, verify, need, ROOT
from . import portable_contract as k

AUTHORITY='validation/t34r3-authority.json'


def authority():
    a=k.read(k.package_member(AUTHORITY))
    need(a['schema']=='gm064-t34r3-saved-fit-authority-v1' and a['scope']=='GM064-T34-R3', 'R3_AUTHORITY_SCHEMA')
    for row in a['adoption']['bindings']:
        member=k.package_member(a['adoption_members'][row['sha256']])
        actual=binding(member)
        need((actual['bytes'],actual['sha256'])==(row['bytes'],row['sha256']),'R3_ADOPTION_BYTES')
    need(a['adoption']['status']=='USER_ADOPTED_SCOPED_RECOVERY_CONTRACT' and
         a['adoption']['new_fit']==0 and a['adoption']['T3_recovery_R0']==1,'R3_ADOPTED_ONLY')
    return a


def validate_source(source, session_root, *, p0=None, cleanup=False, continuation=None):
    from .build import current_closure
    from ..cpu_recipe.recovered import source_structure
    source_structure(source);a=authority();root=k.session(session_root);mark=k.read(root/'session.json')
    r=mark['t34r3_recovery'];manifest=continuation.old_window['manifest'] if continuation is not None else binding(ROOT/'portable-manifest.json')
    need(r['schema']=='gm064-t34r3-session-v1' and r['authority']==binding(k.package_member(AUTHORITY)) and
         mark['package_manifest']==manifest and mark['package_root']==str(ROOT), 'R3_SESSION_BINDING')
    need(source['session']==root.name and source['nonce']==r['nonce'] and
         source['authority']==r['authority'] and source['original']==a['original'] and
         source['adoption']==a['adoption'], 'R3_SOURCE_AUTHORITY')
    executor=continuation.old_qualification['executor_build'] if continuation is not None else current_closure(native=True)
    need(source['new_executor']==executor and source['current_runtime']==dict(status='KNOWN_EXACT_RUNTIME',inventory=binding(root/'runtime-inventory.json')) and
         source['current_runtime']['status']=='KNOWN_EXACT_RUNTIME', 'R3_EXECUTOR_RUNTIME')
    verify(source['current_runtime']['inventory'])
    now=continuation.a['reservation']['wall'] if continuation is not None else time.time()
    window=k.read(r['window']['path']);verify(r['window'])
    need(window['session']==str(root) and window['nonce']==r['nonce'] and window['manifest']==manifest and
         window['authority']==r['authority'] and window['seconds']==28800 and
         window['deadline_at']==window['started_at']+28800 and window['started_at']<=now<=window['deadline_at']+(7200 if cleanup else 0) and
         1793788456.605<=now<=1793788456.605+168*3600,'R3_RECOVERY_CLOCK')
    proof=p0 or binding(root/'P0-PASS.json');verify(proof);v=k.read(proof['path'])
    need(proof==binding(root/'P0-PASS.json') and v['manifest']==manifest,'R3_P0_MANIFEST')
    need(v['status']=='GM064_ZERO_SCIENCE_PASS' and v['executor_build']==source['new_executor']['sha256'] and
         v['R3_groups']==['E1','E2','E3','E4','E5','E6'] and v['target_only_current_inventory'] is (not mark.get('local_only')),'R3_P0_REQUIRED')
    for key in ('checks','lifecycle','outer_wait','parent_delivery_checks'):verify(v[key])
    wait=k.read(v['outer_wait']['path'])
    need(all(wait[key] is True for key in ('actual_root_wait','actual_all_wait','exact','tree_empty')) and
         wait['root_exit']==0 and wait['error'] is None,'R3_P0_ACTUAL_WAIT')
    return a,root


@contextmanager
def protected_parameter(path, expected):
    """Keep a deny-write/delete native handle from verification through publish."""
    need(os.name=='nt' and Path(path).is_absolute(),'R3_PARAMETER_PLATFORM')
    k.ancestors(path)
    need(os.path.normcase(os.path.abspath(path))==os.path.normcase(expected['path']),'R3_PARAMETER_SOURCE_PATH')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE];kernel.CreateFileW.restype=w.HANDLE
    kernel.ReadFile.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
    kernel.GetFileInformationByHandle.argtypes=[w.HANDLE,ctypes.c_void_p]
    kernel.GetFinalPathNameByHandleW.argtypes=[w.HANDLE,w.LPWSTR,w.DWORD,w.DWORD]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    h=kernel.CreateFileW(str(path),0x80000000,1,None,3,0,None)
    need(h not in (None,ctypes.c_void_p(-1).value),'R3_PARAMETER_PROTECT_OPEN')
    def identity():
        raw=ctypes.create_string_buffer(52)
        need(kernel.GetFileInformationByHandle(h,raw),'R3_PARAMETER_HANDLE_ID')
        # File access time can change on read; it is not part of the stable identity.
        return raw.raw[:12]+raw.raw[20:]
    try:
        before=identity();n=expected['bytes'];need(n==49768,'R3_PARAMETER_BOUND')
        if 'identity' in expected:
            raw=ctypes.create_string_buffer(52);need(kernel.GetFileInformationByHandle(h,raw),'R3_PARAMETER_IDENTITY')
            x=struct.unpack('<13I',raw.raw)
            observed=dict(attributes=x[0],creation_time=x[1]|x[2]<<32,write_time=x[5]|x[6]<<32,
                volume_serial=x[7],bytes=x[8]<<32|x[9],links=x[10],file_id=x[11]<<32|x[12])
            need(observed==expected['identity'],'R3_PARAMETER_FROZEN_IDENTITY')
        final=ctypes.create_unicode_buffer(32768);gotpath=kernel.GetFinalPathNameByHandleW(h,final,len(final),0)
        need(0<gotpath<len(final) and os.path.normcase(final.value.removeprefix('\\\\?\\'))==os.path.normcase(str(Path(path).absolute())) and
             ('final_path' not in expected or final.value==expected['final_path']),
             'R3_PARAMETER_FINAL_PATH')
        buf=ctypes.create_string_buffer(n+1);got=w.DWORD()
        need(kernel.ReadFile(h,buf,n+1,ctypes.byref(got),None) and got.value==n,'R3_PARAMETER_EXACT_LENGTH')
        data=buf.raw[:n]
        need(hashlib.sha256(data).hexdigest()==expected['sha256'],'R3_PARAMETER_HASH')
        yield data
        need(identity()==before,'R3_PARAMETER_ID_CHANGED')
    finally:kernel.CloseHandle(h)


@metadata_api
def recover_saved_fit(directory, parameter_file, source, *, session_root, request_key, p0=None):
    a,root=validate_source(source,session_root,p0=p0)
    target=Path(directory).absolute();need(target==k.workspace(root)/'T3/recovered-fast','R3_RECOVERY_DESTINATION')
    expected=a['original']['bindings']['parameters']
    with protected_parameter(parameter_file,expected) as data:
        from ..cpu_recipe.recovered import publish
        result=publish(target,data,source,request_key)
    receipt=root/'science/recovered-source.json'
    record=dict(schema='gm064-t34r3-source-accepted-v1',source_id=source['source_id'],
        bundle={key:result[key] for key in ('bundle_id','math_id','version','mode')},directory=str(target),
        original_fit_exit=7,original_T3_exit=1,original_runtime='UNKNOWN_RETAINED',
        current_execution='NOT_STARTED',new_fit=0,source=binding(target/'provenance/recovery-source.json'))
    if receipt.exists():need(k.read(receipt)==record,'R3_SOURCE_RECEIPT_CONFLICT')
    else:atomic_json(receipt,record)
    return result


def validate_parent(pkg,q,m,*,cleanup=False,continuation=None):
    """A new recovery proof cannot enter the old successful-fit branch."""
    proof=pkg['producer'];need(set(proof)==({'recovery_source','accepted','release'} if q['request']['operation']=='refine' else
                                         {'recovery_source','accepted'}),'R3_PARENT_PROOF_FIELDS')
    for b in (proof['recovery_source'],proof['accepted']):verify(b)
    source=k.read(proof['recovery_source']['path']);a,root=validate_source(
        source,k.session(q['manifest_path']),cleanup=cleanup,continuation=continuation)
    need(m['version']==3 and m['mode']=='fast' and m['recovery']==source and
         Path(proof['recovery_source']['path'])==Path(pkg['directory'])/'provenance/recovery-source.json' and
         Path(proof['accepted']['path'])==root/'science/recovered-source.json','R3_PARENT_SOURCE')
    accepted=k.read(proof['accepted']['path'])
    need(accepted['source_id']==source['source_id'] and accepted['bundle']['bundle_id']==m['bundle_id'] and
         accepted['bundle']['math_id']==m['math_id'],'R3_PARENT_ACCEPTED')
    if q['request']['operation']=='refine':
        need(proof['release'] is not None,'R3_RELEASE_REQUIRED_BEFORE_REFINE');verify(proof['release'])
        release=k.read(proof['release']['path'])
        for row in release['files']:verify(row)
        need(Path(proof['release']['path'])==root/'science/recovered-release.json' and
             release['status']=='R3_RECOVERED_FAST_PUBLIC_RELEASE_PASS' and
             release['actual_wait'] is True and release['tree_empty'] is True and release['bundle_id']==m['bundle_id'],
             'R3_RECOVERED_RELEASE')
    else:need(q['request']['operation']=='replay' and q['request']['quality']=='fast','R3_PARENT_OPERATION')
    return True
