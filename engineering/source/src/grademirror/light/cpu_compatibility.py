"""Explicit read-only compatibility for the single reviewed legacy CPU recipe."""
from pathlib import Path

from ..common import atomic_json, strict_load
from ..cpu_replay.contract import binding, need, verify
from ..cpu_recipe.generated import complete

LEGACY=dict(
    bundle_id="4011c98294f78a2e2b97bd1a4ef70bce14d3a7886c2f233c48ce6e4a623b0bec",
    math_id="fe114f646419f2589a3d5bfbe684b081dd3665d81307d64b58d2e84bf2eb1446",
    producer="original_cpu_fit_loop",
    runtime_sha256="da0f7a4f42b81b45ce5f814da27a383b84de0a43e7517c40193ff74fca3f530b",
    source_sha256="64db59daa73c460dfdaa498c3ab29c865ea3032a39cd4e6d32beda092de4b60c",
    parameters_sha256="f060fed595526cd0a0f3dd9d7cfef5a17a93cb4bd122c5950f4fd061688048e0",
)


def contract(directory,manifest,runtime,build):
    m=manifest
    math=m["mathematical"]
    actual=dict(bundle_id=m["bundle_id"],math_id=m["math_id"],producer=m["provenance"]["producer"],
                runtime_sha256=m["runtime_requirements"].get("inventory",{}).get("sha256"),
                source_sha256=math["inputs"]["source_identity"]["file_sha256"],
                parameters_sha256=math["r0_parameters"]["value_sha256"])
    need(m["version"]==2 and m["mode"]=="fast" and m["provenance"]["TEST_ONLY"] is False and actual==LEGACY,
         "LIGHT_LEGACY_RECIPE_UNSUPPORTED")
    need(runtime["numeric"]=="2.5.1+cpu" and runtime["threads"]==4,"LIGHT_LEGACY_CPU_RUNTIME_REQUIRED")
    return dict(schema="gm067-exact-legacy-cpu-v1",origin=actual,old_runtime=m["runtime_requirements"],
                recipe=binding(Path(directory)/"COMPLETE.json"),target=runtime,build=build,
                validation="EXACT_SCOPE_REPLAY_CANDIDATE")


def read(path,directory,manifest,runtime,build):
    row=strict_load(path)
    need(row==contract(directory,manifest,runtime,build),"LIGHT_LEGACY_COMPATIBILITY_CHANGED")
    verify(row["recipe"])
    return row


def color_matches(saved, source, display_reference):
    # The exact bound legacy recipe stores policy plus provenance, whereas
    # preview-generated bundles store the ID alone. A legacy replay can
    # verify the source header and reference byte binding without decoding
    # the reference a second time.
    if not isinstance(saved, dict) or not display_reference:
        return False
    if set(saved) != {"color_policy_id", "source", "reference"}:
        return False
    color = source["color"]
    expected_source = dict(file_sha256=source["sha256"], icc_sha256=color["icc_sha256"],
                           interpretation=color["interpretation"])
    reference = saved["reference"]
    return (saved["color_policy_id"] == color["color_policy_id"]
            and saved["source"] == expected_source and isinstance(reference, dict)
            and set(reference) == {"file_sha256", "interpretation", "marker"}
            and reference["file_sha256"] == display_reference["file"]["sha256"]
            and (reference["interpretation"], reference["marker"]) == ("assumed_sRGB", "absent"))


def import_object(directory,runtime,build,out):
    directory=Path(directory).resolve(strict=True)
    m,_=complete(directory)
    row=contract(directory,m,runtime,build)
    need(not Path(out).exists(),"LIGHT_LEGACY_DESTINATION_EXISTS")
    atomic_json(Path(out),row)
    return row
