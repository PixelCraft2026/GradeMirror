"""Static fine-mode contract. No numeric imports."""
import hashlib,json
from ..independent_r0 import profile as fast
from ...image_limits import MAX_PIXELS
PROFILE_ID='independent-r0-p3-lld10-v1'
RECIPE_SCHEMA_VERSION='0.5.0'
FIT_CONTRACT='gm008-fit-v1'
P3=dict(h=8,ridge=.0001,smooth=.01,dtype='float64',cg_rtol=1e-8,cg_atol=1e-12,cg_maxiter=2000,preconditioner='Jacobi',start='zero',basis='full_last_cell')
LLF=dict(sigma=.1,J=8,delta=1/64,range_margin=2/255,anchor_cap_per_channel=257,dtype='float32',range_rule='pair-absolute-anchor-v1',P3_strip_rows=128,composition_rows=64)
SETTINGS=fast.SETTINGS
PARAMETER_SHAPES=fast.PARAMETER_SHAPES
LEGACY_LIMITS=dict(native_pixels=50118276,reference_pixels=2000000,reference_max_axis=2048,K=32768,archive_bytes=2*1024**2)
# With positive integer dimensions and W*H <= P, the largest h8 grid
# occurs at (P, 1). Keep an explicit bound even for extreme aspect ratios.
LIMITS=dict(native_pixels=MAX_PIXELS,reference_pixels=MAX_PIXELS,reference_max_axis=MAX_PIXELS,K=2*((MAX_PIXELS+7)//8+1),archive_bytes=1280*1024**2)
LEGACY_CONTRACT_SHA256='b0ba3fdcbf6f30a673a512a9585ca0cb1afa8f553aabac2dc49e42e9953addc1'
LEGACY_CORE_SHA256='b92fa1ea4cfc70365f4c38162435a93373bfd89e0a4af17293448681b5e32a4a'
HIGH_RES_CONTRACT_SHA256='39f4de52ee4c9aadcd365e49ba31f0b3e652677730955859fb5a11c1e110122e'
HIGH_RES_CORE_SHA256='68340b440cdc6e7c2b0649afe8c05fe8016c4396fa0c512821dd844dafdc8328'
COMPATIBLE_CONTRACTS={LEGACY_CONTRACT_SHA256:dict(core_sha256=LEGACY_CORE_SHA256,limits=LEGACY_LIMITS),HIGH_RES_CONTRACT_SHA256:dict(core_sha256=HIGH_RES_CORE_SHA256,limits=LIMITS)}
RUNTIME=dict(neural=fast.RUNTIME,solver=dict(numpy='2.5.3',scipy='1.18.1'))
MATH_SHA256={'p3_solver.py': '4e06ffa816de551907c102e3ae24634e6e83d2d313939421a408666b238af8a5', 'area.py': '31e67e6da4dd8e547fa776814331a504f7fc10da2a2ced0304f764a5f5a73ed3', 'render.py': '02524bd4a48de0110ddbbd9c6df360c4cc7cd2641ca3507c0e5f775ec201c0cb', 'llf_common.py': '40a4d2f7b6dd41a308c35412a697db2801d0cef7d6a13d7c7406a33ac8973458', 'llf.py': 'ba3e17a4ff8927ebcc1123f6bbabee76f117f0b4538e150f30442c89f059aaec', 'compose.py': '4669bf0c55239be154874b41f994cf8686a5ae7464115ec63ed1089b63c1c7ea'}
EXECUTION_SHA256={**MATH_SHA256, 'llf.py': '38ffa2a58187537dbd07a7c301ea121853874688c814b4ad4b87a08ce4a3baf2'}
CORE_SHA256=hashlib.sha256(json.dumps(MATH_SHA256,sort_keys=True,separators=(',',':')).encode()).hexdigest()
PROFILE_CONTRACT_SHA256=hashlib.sha256(json.dumps(dict(profile=PROFILE_ID,version=RECIPE_SCHEMA_VERSION,fit=FIT_CONTRACT,r0=fast.PROFILE_CONTRACT_SHA256,P3=P3,LLF=LLF,math=MATH_SHA256,runtime=RUNTIME,limits=LIMITS,composition='FP32(FP64(O)+(FP64(LP)-FP64(LO)))'),sort_keys=True,separators=(',',':')).encode()).hexdigest()
