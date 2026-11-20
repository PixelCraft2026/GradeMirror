"""GM064 resident handoff using the existing client, host and lease protocol."""
import contextvars
import ctypes as ct
import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from ..common import atomic_json, strict_load
from ..cpu_replay.contract import ROOT, binding, need
from ..winproc import current_identity, identity_alive
from . import portable_contract as portable

SCHEMA = 'gm064-renewal-handoff-v1'
_client = contextvars.ContextVar('gm064_resident_client', default=None)


def first_failure(keeper_error, business_error):
    if keeper_error is None:
        return 'business' if business_error is not None else None
    if business_error is None or keeper_error['monotonic']<=business_error['monotonic']:
        return 'keeper'
    return 'business'


def event(directory, name, **facts):
    atomic_json(Path(directory)/(name+'.json'), dict(schema=SCHEMA, action=name,
        wall=time.time(), monotonic=time.monotonic(), identity=current_identity(), **facts))


class Pins:
    """Windows share modes prevent content writes and rename while held.

    Ancestors are opened without delete sharing before the file is opened.
    File identity is verified from the same pinned handle that blocks writes.
    """
    def __init__(self):
        self.handles = {}
        self.rows = {}
        self.hashed_bytes = 0
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.open = kernel.CreateFileW
        self.open.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ct.c_void_p,
                              wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        self.open.restype = wintypes.HANDLE
        self.close_handle = kernel.CloseHandle
        self.close_handle.argtypes = [wintypes.HANDLE]
        self.close_handle.restype = wintypes.BOOL

    def _open(self, path, access, share, flags):
        handle = self.open(str(path),access,share,None,3,flags,None)
        if handle == ct.c_void_p(-1).value:
            raise ct.WinError(ct.get_last_error())
        return handle

    def add(self, row):
        import msvcrt
        path=Path(row['path'])
        need(path.is_absolute(),'GM064_PIN_ABSOLUTE')
        if path in self.rows:
            need(self.rows[path]==row['sha256'],'GM064_PIN_HASH_CONFLICT')
            return
        for parent in reversed(path.parents):
            if parent == Path(path.anchor) or parent in self.handles:
                continue
            self.handles[parent]=self._open(parent,0x80,3,0x02000000|0x00200000)
            portable.ancestors(parent)
        handle=self._open(path,0x80000000,1,0x00200000)
        try:
            portable.ancestors(path)
            fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
        except BaseException:
            self.close_handle(handle)
            raise
        stream=os.fdopen(fd,'rb')
        self.handles[path]=stream
        size=os.fstat(fd).st_size
        need((row.get('bytes',size)==size and hashlib.file_digest(stream,'sha256').hexdigest()==row['sha256']),
             'GM064_PIN_BYTES_CHANGED',str(path))
        self.rows[path]=row['sha256']
        self.hashed_bytes+=size

    def valid(self):
        return bool(self.rows) and all(not self.handles[path].closed for path in self.rows)

    def close(self):
        for value in reversed(tuple(self.handles.values())):
            if isinstance(value,int):
                self.close_handle(value)
            else:
                value.close()
        self.handles.clear()
        self.rows.clear()


def _scope_rows(c):
    q=portable.read(c['qualification']['path'])
    manifest=binding(ROOT/'portable-manifest.json')
    inventory=portable.read(c['runtime']['inventory']['path'])
    need(inventory.get('package_manifest')==manifest,'GM064_RENEWAL_PACKAGE_CHANGED')
    rows=[manifest,binding(c['manifest_path']),
          c['qualification'],c['runtime']['inventory'],q['authorization'],q['taskbook'],
          q['fixed_inputs'],q['resource_model'],q['source'],q['reference'],
          binding(portable.e(c)/'allocations.json'),binding(portable.e(c)/'qualification-index.json'),
          binding(Path(c['manifest_path']).parent.parent/'context-index.json'),
          binding(portable.package_member('validation/capability.json')),*q['interpreters'].values()]
    from .t34r3_c1_route import selected,KEY
    route=selected(q,Path(c['workspace']),KEY)
    if route is not None:
        rows.extend((binding(route.qualification_index),route.authority,route.controller,
                     route.a['old_qualification'],route.a['old_context'],route.a['old_selection'],
                     route.a['old_request'],route.a['old_wait'],route.a['old_window']))
    if c.get('resource_selection'):
        rows.append(c['resource_selection'])
    if q['p0']:
        rows.append(q['p0'])
    if q['parent_package']:
        rows.extend(q['parent_package']['files'])
        rows.extend(q['parent_package']['producer'].values())
    rows.extend(dict(path=str(ROOT/row['path']),sha256=row['sha256'])
                for row in c['executor_build']['files'])
    return rows


def _pin_runtime(c, pins):
    import sys
    from ..cpu_replay.runtime import loaded_binaries
    known={os.path.normcase(row['path']):row for row in portable.read(c['runtime']['inventory']['path'])['files']}
    product={Path(ROOT/row['path']):row['sha256'] for row in c['executor_build']['files']}
    paths={Path(row['path']).resolve() for row in loaded_binaries()}
    paths.update(Path(p).resolve() for p in sys.path if Path(p).is_file())
    paths.update(Path(p).resolve() for module in tuple(sys.modules.values())
                 if (p:=getattr(module,'__file__',None)) and Path(p).is_file())
    for path in paths:
        row=known.get(os.path.normcase(str(path)))
        if row is None and path in product:
            row=dict(path=str(path),sha256=product[path])
        need(row is not None,'GM064_RENEWAL_RUNTIME_UNKNOWN',str(path))
        pins.add(row)


_tcp = None


def prepare_peer_table():
    global _tcp
    if _tcp is None:
        # Prime delayed system dependencies before taking the runtime snapshot.
        ct.WinDLL('nsi.dll')
        ct.WinDLL('mswsock.dll')
        library=ct.WinDLL('iphlpapi.dll',use_last_error=True)
        _tcp=library.GetExtendedTcpTable
        _tcp.argtypes=[ct.c_void_p,ct.POINTER(wintypes.DWORD),wintypes.BOOL,
                       wintypes.ULONG,ct.c_int,wintypes.ULONG]
        _tcp.restype=wintypes.DWORD
        size=wintypes.DWORD()
        need(_tcp(None,ct.byref(size),False,2,5,0)==122 and 4<=size.value<=16*1024**2,
             'GM064_RENEWAL_TCP_TABLE_INIT')


def peer_pid(conn):
    import socket
    sock=socket.socket(fileno=conn.fileno())
    try:
        local=sock.getsockname()
        peer=sock.getpeername()
    finally:
        sock.detach()
    need(local[0]==peer[0]=='127.0.0.1' and _tcp is not None,'GM064_RENEWAL_PEER_SCOPE')
    size=wintypes.DWORD()
    _tcp(None,ct.byref(size),False,2,5,0)
    for _ in range(3):
        need(4<=size.value<=16*1024**2,'GM064_RENEWAL_TCP_TABLE_SIZE')
        data=ct.create_string_buffer(size.value)
        code=_tcp(data,ct.byref(size),False,2,5,0)
        if code==0:
            break
        need(code==122,'GM064_RENEWAL_TCP_TABLE',str(code))
    else:
        need(False,'GM064_RENEWAL_TCP_TABLE_CHANGED')
    import struct
    count=struct.unpack_from('<I',data)[0]
    need(4+24*count<=size.value,'GM064_RENEWAL_TCP_TABLE_TRUNCATED')
    match=[]
    for state,la,lp,ra,rp,pid in struct.iter_unpack('<6I',data.raw[4:4+24*count]):
        if state==5 and la==ra==0x0100007f and socket.ntohs(lp&65535)==peer[1] and socket.ntohs(rp&65535)==local[1]:
            match.append(pid)
    need(len(match)==1,'GM064_RENEWAL_PEER_UNKNOWN')
    return match[0]


def _live(who):
    need(identity_alive(who) is True,'GM064_RENEWAL_IDENTITY_NOT_LIVE')


class Client:
    def __init__(self, out, key):
        self.out=Path(out)
        self.key=key
        self.pins=Pins()
        self.c=None
        self.operation=None
        self.receipts=[]

    def prepare(self,c):
        need(self.c is None and portable.enabled(c),'GM064_RENEWAL_CLIENT_SCOPE')
        from .context import load
        need(load(c['manifest_path'])==c,'GM064_RENEWAL_CONTEXT_CHANGED')
        started=time.monotonic()
        prepare_peer_table()
        for row in _scope_rows(c):
            self.pins.add(row)
        _pin_runtime(c,self.pins)
        self.who=current_identity()
        self.directory=Path(c['output_root'])
        self.ticket=dict(schema=SCHEMA,caller=self.who,context=binding(c['manifest_path']),
                         manifest=binding(ROOT/'portable-manifest.json'),request_key=self.key,
                         nonce=uuid.uuid4().hex)
        ticket=self.directory/'renewal-client.json'
        atomic_json(ticket,self.ticket)
        self.pins.add(binding(ticket))
        event(self.out,'renewal-ready',caller=self.who,protected_count=len(self.pins.rows),
              context=self.ticket['context'],manifest=self.ticket['manifest'],
              preparation_seconds=time.monotonic()-started)
        self.c=c

    def renew(self,path,spec,lease):
        from ..cpu_replay import api
        need(self.c is not None and self.pins.rows and spec['request_key']==self.key
             and spec['semantics']['instance_context']==self.ticket['context'],
             'GM064_RENEWAL_OPERATION_SCOPE')
        path=Path(path)
        need(path.is_relative_to(Path(self.c['workspace'])/'store'),'GM064_RENEWAL_STORE_SCOPE')
        session=path/'session'
        if self.operation is None:
            self.operation=(path,lease)
        need(self.operation==(path,lease),'GM064_RENEWAL_OPERATION_CHANGED')
        current=strict_load(session/'lease.json')
        need(current['state']=='RESIDENT' and current['binding']==lease and time.time()<current['expires_at'],
             'RESULT_NOT_RESIDENT',exit_code=4)
        for who in (lease['owner'],lease['host']):
            _live(who)
        request_id=uuid.uuid4().hex
        event(self.out,'renewal-send-'+request_id,request_id=request_id,old_expiry=current['expires_at'])
        result=api.call(path,'KEEP_ALIVE',lease,lease_policy=self.c['lease_policy'],
                        context=self.ticket['context'],handoff=dict(caller=self.who,
                        nonce=self.ticket['nonce'],request_id=request_id))
        need(result.get('host_confirmed') is True and result.get('binding')==lease
             and result.get('request_id')==request_id and result['confirmed_at']<result['old_expires_at'],
             'GM064_RENEWAL_CONFIRMATION')
        event(self.out,'renewal-confirm-'+request_id,request_id=request_id,old_expiry=result['old_expires_at'],
              new_expiry=result['expires_at'],host_confirmed=True)
        return result


@contextmanager
def use_client(client):
    token=_client.set(client)
    try:
        yield
    finally:
        _client.reset(token)


def prepare_client(c):
    client=_client.get()
    if client is not None and client.c is None:
        client.prepare(c)


def prepare_host(host):
    c=host.context
    if not portable.enabled(c):
        return None
    ticket_path=Path(c['output_root'])/'renewal-client.json'
    if not ticket_path.is_file():
        return None
    started=time.monotonic()
    prepare_peer_table()
    pins=Pins()
    try:
        for row in _scope_rows(c):
            pins.add(row)
        _pin_runtime(c,pins)
        pins.add(binding(ticket_path))
        pins.add(binding(host.path/'spec.json'))
        ticket=strict_load(ticket_path)
        spec=host.spec
        observer=Path(os.environ['GM052_OBSERVER_ROOT'])
        observed=strict_load(observer/'entry.json')['identity']
        need(set(ticket)=={'schema','caller','context','manifest','request_key','nonce'}
             and ticket['schema']==SCHEMA and ticket['caller']==observed
             and ticket['context']==spec['semantics']['instance_context']
             and ticket['manifest']==binding(ROOT/'portable-manifest.json')
             and len(ticket['nonce'])==32,'GM064_RENEWAL_HOST_TICKET')
        need(strict_load(host.path/'spec.json')['request_key']==ticket['request_key'],
             'GM064_RENEWAL_HOST_REQUEST')
        _live(ticket['caller'])
        event(host.session/'host','renewal-ready',caller=ticket['caller'],protected_count=len(pins.rows),
              context=ticket['context'],manifest=ticket['manifest'],
              preparation_seconds=time.monotonic()-started)
        return pins,ticket
    except BaseException:
        pins.close()
        raise


def authorized_host(host, request, peer_pid):
    proof=getattr(host,'renewal_proof',None)
    need(proof is not None,'GM064_RENEWAL_HOST_NOT_READY')
    pins,ticket=proof
    handoff=request['handoff']
    committed=strict_load(host.path/'commit.json')
    need(committed.get('result_lease')==host.binding
         and all(committed.get(key)==host.spec[key] for key in ('job_id','attempt_id')),
         'GM064_RENEWAL_PUBLICATION_CHANGED')
    need(pins.rows and type(handoff) is dict and set(handoff)=={'caller','nonce','request_id'}
         and handoff['caller']==ticket['caller'] and handoff['nonce']==ticket['nonce']
         and peer_pid==ticket['caller']['pid']
         and type(handoff['request_id']) is str and len(handoff['request_id'])==32
         and all(ch in '0123456789abcdef' for ch in handoff['request_id'])
         and request['context']==ticket['context'],'GM064_RENEWAL_HOST_CALLER')
    _live(ticket['caller'])
    event(portable.renewal_directory(host.session)/'r3','recv-'+handoff['request_id'],
          request_id=handoff['request_id'],caller=ticket['caller'])
    return host.context,ticket['context'],handoff['request_id']
