"""Read-only bridge for the fixed CPU fast PNG recipe."""
from ..common import reject_reparse, sha_file, strict_load
from ..cpu_replay.contract import digest, need
from ..cpu_recipe.identity import math_closure
from . import portable, runtime
from .system import manifest, packaged

SCOPE = {'schema': 'gm095-fixed-c-png-scope-v1', 'bundle_id': '979f434c35f82d7c85b9ce4e373fcfa23919e78c96a299a469d3c5b319d372a1', 'math_id': 'bb6522ef3000fdded43942f847caa99b3e905ce0ec703ceee43fdad55da9bd3d', 'producer': 'gm065-preview-original-cpu-fit', 'old_math_files': [{'path': 'grademirror/engine/group15_codec.py', 'sha256': '404fefce654cce0e246c5db232df020f35c63ec91a82e11e76bf474fc36d13c8'}, {'path': 'grademirror/engine/independent_r0/_initialization.py', 'sha256': 'fffe8d342399a27465d5e7061bdb30fa90d04be1aef618aa0ba83ec11525e718'}, {'path': 'grademirror/engine/independent_r0/_sampling.py', 'sha256': 'a6add364a4dfdae19f4a63ced9ebc358846a03b8004eca48ace6fd791cc1295d'}, {'path': 'grademirror/engine/independent_r0/color_lab.py', 'sha256': 'd0cb49a28b9f61de3a6a42b33736445054af667519db7abe7046e3be60a91916'}, {'path': 'grademirror/engine/independent_r0/core.py', 'sha256': '5be350e3e8b62822cafefc9d622a448bb6be702a30f13852dd0754364955d0a6'}, {'path': 'grademirror/engine/independent_r0/m05_lab.py', 'sha256': '2d339fc48ff647ebc0aecec7155d1cb3f7a0455cdfdf24f3f8c07b7130402c04'}, {'path': 'grademirror/engine/independent_r0/mapping_penalty.py', 'sha256': '02bd03442f69f8f7f99b44024bc8d62eb39b9ee35eda262fdd210d451490c55b'}, {'path': 'grademirror/engine/independent_r0/profile.py', 'sha256': '80363a885bff5346a46aee4639e611fe096e93cca29b84b98351fba6b2816197'}, {'path': 'grademirror/engine/independent_r0/tensor_ops.py', 'sha256': '7dcb5a4d2bc7330b998444e5ed559dfdd3396224e51d34a4c36305fb19ac9237'}, {'path': 'grademirror/export_pixels.py', 'sha256': '16892dc34aa6af4ae46ce4744fb422e725216b5979b9e1fb24d5424f7c1a6a84'}], 'new_math_files': [{'path': 'grademirror/engine/group15_codec.py', 'sha256': 'c826a1dadf2183b58aafff086c2c5905d2cb51704652ce80af470bd8ad03d6af'}, {'path': 'grademirror/engine/independent_r0/_initialization.py', 'sha256': 'fffe8d342399a27465d5e7061bdb30fa90d04be1aef618aa0ba83ec11525e718'}, {'path': 'grademirror/engine/independent_r0/_sampling.py', 'sha256': 'a6add364a4dfdae19f4a63ced9ebc358846a03b8004eca48ace6fd791cc1295d'}, {'path': 'grademirror/engine/independent_r0/color_lab.py', 'sha256': 'd0cb49a28b9f61de3a6a42b33736445054af667519db7abe7046e3be60a91916'}, {'path': 'grademirror/engine/independent_r0/core.py', 'sha256': '5be350e3e8b62822cafefc9d622a448bb6be702a30f13852dd0754364955d0a6'}, {'path': 'grademirror/engine/independent_r0/m05_lab.py', 'sha256': '2d339fc48ff647ebc0aecec7155d1cb3f7a0455cdfdf24f3f8c07b7130402c04'}, {'path': 'grademirror/engine/independent_r0/mapping_penalty.py', 'sha256': '02bd03442f69f8f7f99b44024bc8d62eb39b9ee35eda262fdd210d451490c55b'}, {'path': 'grademirror/engine/independent_r0/profile.py', 'sha256': '80363a885bff5346a46aee4639e611fe096e93cca29b84b98351fba6b2816197'}, {'path': 'grademirror/engine/independent_r0/tensor_ops.py', 'sha256': '7dcb5a4d2bc7330b998444e5ed559dfdd3396224e51d34a4c36305fb19ac9237'}, {'path': 'grademirror/export_pixels.py', 'sha256': '16892dc34aa6af4ae46ce4744fb422e725216b5979b9e1fb24d5424f7c1a6a84'}], 'recipe_assets': {'COMPLETE.json': {'bytes': 424, 'sha256': '1f5e9985401c467b20b47134e9103be0d9cbba0518813ee845630542ec2e3536'}, 'manifest.json': {'bytes': 52147, 'sha256': '509f32127ee8f23e7b8f92d815bbbe95042e3a57a005b9b62d67eee000d2301e'}, 'provenance/fit-intent.json': {'bytes': 331, 'sha256': '416cd3c9ee745479fe920e65bfd228956439650606d68afa70b0701fe3933ca8'}, 'provenance/fit-receipt.json': {'bytes': 107180, 'sha256': '7cd3d40d75db68884ee655e87c964e31681a4cd82a20e4f10b0c5b67d5911c5e'}, 'r0/parameters.npz': {'bytes': 49768, 'sha256': '077df359c44a6af4067eea62c76925cc65ca87dd5c9f25e0bedf08d5e1b253fb'}}, 'project': {'bytes': 1705, 'sha256': 'db6e8ba90ea4f39321ecdb26e6d5aec61a5988fb31862cba0384eebc9a3196ee'}, 'source': {'bytes': 3176654, 'sha256': '6b0877bef8356b9215d0a635f067cb032ae272ce38794f0379d844bb38abeb17', 'format': 'PNG', 'width': 1024, 'height': 1536, 'channels': 3, 'bits': 8, 'orientation': 1, 'color': {'color_policy_id': 'untagged-assume-srgb-user-20261017', 'working_domain': 'gamma-encoded sRGB', 'icc_present': True, 'srgb_present': False, 'png_srgb': None, 'png_gamma': None, 'png_chromaticity': None, 'icc_sha256': '2b3aa1645779a9e634744faf9b01e9102b0c9b88fd6deced7934df86b949af7e', 'icc_size': 3144, 'icc_description': 'sRGB IEC61966-2.1', 'icc_color_space': 'RGB ', 'interpretation': 'embedded_sRGB_ICC'}, 'decoder_id': 'imagecodecs-2026.8.16:PNG-RGB', 'training_grid': [512, 341]}, 'reference': {'bytes': 3401036, 'sha256': 'd2dd38ad47fd3ee484e07c087d2783edb10cddc8f50afacf0d78a01efece6358'}, 'numeric_inventory_sha256': 'a9550ba899f30b7f9e7526f188ee4e24a1c81f110e244b9e200056ddf7fa6d42', 'parameter_value_sha256': '053622c6be30ed8457ad1e0f538e407447ff5961c2f2659fce522de83321153f', 'runtime_requirements': {'inventory_sha256': 'f05bce44a7ab0f6b6879cb68c74bf62e0b6ca8f2042f213288b41e6f5cc0377e', 'numeric': '2.5.1+cpu', 'python': '3.12.14', 'schema': 'gm066-cpu-runtime-v1', 'threads': 4}}
TARGET = "compatibility/cpu-fast-png-target.json"
RUNTIME_PREFIXES = ("runtime/cpu/", "runtime/io/", "runtime/solver/", "runtime/cpython/")
EXES = ("GradeMirror-CLI.exe", "GradeMirror.exe", "workers/cpu/GradeMirror-cpu.exe",
        "workers/io/GradeMirror-io.exe", "workers/control/GradeMirror-control.exe")

def bind_bytes(path, expected, code):
    reject_reparse(path)
    need(path.is_file() and path.stat().st_size == expected["bytes"] and sha_file(path) == expected["sha256"], code)

def checked_recipe(directory, saved, source, reference_binding, active_runtime, *, operation):
    current = math_closure("fast")
    mathematical = saved["mathematical"]
    scope = SCOPE
    if mathematical["math_files"] == current:
        return True
    need(operation == "replay" and portable.enabled() and runtime.application()
         and manifest().get("edition") == "cpu", "C_CODEC_SCOPE")
    need((saved["bundle_id"], saved["math_id"], saved["mode"], saved["provenance"]["producer"]) ==
         (scope["bundle_id"], scope["math_id"], "fast", scope["producer"]), "C_CODEC_SAVED_RECIPE")
    need(mathematical["math_files"] == scope["old_math_files"] and current == scope["new_math_files"],
         "C_CODEC_MATH_SCOPE")
    need(mathematical["r0_parameters"]["value_sha256"] == scope["parameter_value_sha256"], "C_CODEC_PARAMETERS")
    for member, expected in scope["recipe_assets"].items():
        code = "C_CODEC_PARAMETERS" if member.endswith("parameters.npz") else "C_CODEC_SAVED_RECIPE"
        bind_bytes(directory / member, expected, code)
    bind_bytes(directory.parent / "project.json", scope["project"], "C_CODEC_PROJECT")
    need(all(source.get(k) == v for k, v in scope["source"].items()), "C_CODEC_SOURCE")
    need(reference_binding is not None and all(reference_binding.get(k) == v for k,v in scope["reference"].items()),
         "C_CODEC_REFERENCE")
    need(saved["runtime_requirements"] == active_runtime == scope["runtime_requirements"], "C_CODEC_RUNTIME_LOCK")
    package = manifest()
    rows = package["files"]
    numeric = {n: v for n, v in rows.items() if n.startswith(RUNTIME_PREFIXES)}
    need(digest(numeric) == scope["numeric_inventory_sha256"], "C_CODEC_TARGET_NUMERIC")
    build = package["build"]["sha256"]
    expected_target = dict(schema="gm095-c-codec-target-v1", build=build, scope=digest(scope),
                           numerical_inventory=digest(numeric), launchers={n: rows[n] for n in EXES})
    need(strict_load(packaged(runtime.ROOT / TARGET)) == expected_target, "C_CODEC_TARGET_BRIDGE")
    for name in EXES:
        packaged(runtime.ROOT / name)
    for name in numeric:
        packaged(runtime.ROOT / name)
    return True

def check_plan(directory, saved, source, reference, environment):
    row=environment["cpu"]
    declared=dict(schema="gm066-cpu-runtime-v1",inventory_sha256=row["lock"],
                  numeric=row["versions"]["torch"],python=row["python"],threads=4)
    need(saved["runtime_requirements"]==declared,"PREVIEW_RECIPE_RUNTIME_INCOMPATIBLE")
    return checked_recipe(directory,saved,source,reference,declared,operation="replay")
