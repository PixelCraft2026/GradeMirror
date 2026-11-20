"""Exact original interpreter explicitly restored to the GM021 S4 contract."""
from pathlib import Path
from ..common import sha_file,GMError

PRIMARY=Path('__historical_runtime_not_shipped__')
ORIGINAL_SHA256='7a4102b91959bb234507850ea584e1d31d300a6831b171e4aadc59ff592f0234'
APPROVED_SHA256=ORIGINAL_SHA256
AMENDMENT='GM-021-CONTROL-RUNTIME-RESTORED-20261023-v2'

def identity():
    from ..light.runtime import application,bundle_exe
    from ..light import portable
    if application():
        exe=bundle_exe("control")
        amendment = "GM090-PORTABLE-EXE" if portable.enabled() else "GM066-LIGHT-PREVIEW"
        return dict(amendment=amendment,python="3.12.14",bytes=exe.stat().st_size,
                    sha256=sha_file(exe),original_sha256=ORIGINAL_SHA256,original_binding_retained=True)
    import os
    if os.environ.get('GM063_LAUNCH_CONFIG'):
        from ..adaptive_execution.calibration_launch import primary_runtime
        selected=primary_runtime()
        return dict(amendment='GM-063-FIXED24-CURRENT-RUNTIME-20261104-v1',python='3.12.14',bytes=selected['bytes'],sha256=selected['sha256'],original_sha256=ORIGINAL_SHA256,original_binding_retained=True)
    if PRIMARY.stat().st_size!=107312 or sha_file(PRIMARY)!=APPROVED_SHA256:
        raise GMError('HYBRID_PRIMARY_RUNTIME_CHANGED','Explicitly adopted primary runtime bytes differ',7)
    return dict(amendment=AMENDMENT,python='3.12.14',bytes=107312,sha256=APPROVED_SHA256,
        original_sha256=ORIGINAL_SHA256,original_binding_retained=True)

def effective_binding(original):
    if Path(original['path'])!=PRIMARY:return original
    import os
    if os.environ.get('GM063_LAUNCH_CONFIG'):
        from ..adaptive_execution.calibration_launch import primary_runtime
        selected=primary_runtime()
        if original['bytes']!=107312 or original['sha256'] not in (ORIGINAL_SHA256,selected['sha256']):raise GMError('HYBRID_ORIGINAL_RUNTIME_BINDING_CHANGED','Original or GM063 exact binding required',7)
        return dict(original,sha256=selected['sha256'])
    if original['bytes']!=107312 or original['sha256']!=ORIGINAL_SHA256:
        raise GMError('HYBRID_ORIGINAL_RUNTIME_BINDING_CHANGED','Original baseline differs',7)
    identity()
    return dict(original,sha256=APPROVED_SHA256)
