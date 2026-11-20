"""Processed-primary TIFF policy. Only explicitly understood values are copied."""
from copy import copy
import struct

from .exif_structure import LINKS, STORAGE, need
from .export_layout import EXIF_SAFE, TIFF_SAFE, image_ranges, merge_ranges, tiff_plan

POLICY = "source-exif-tiff-compact-v1"
RULE = "processed-primary-and-relocatable-exif-v1"

DIRECTORIES = {
    "IFD0": {34665, 34853},
    "IFD0/Exif0": {40965},
    "IFD0/Next": {34665, 34853},
    "IFD0/Next/Exif0": {40965},
}


def omission_reason(row, source_pixels):
    path, tag = row["ifd"], row["tag"]
    if path == "IFD0" and tag in STORAGE:
        return None
    from .export_thumbnail import IMAGE_TAGS
    if tag == "JPEGPreview" or (path == "IFD0/Next" and tag in IMAGE_TAGS):
        return "UNPROCESSED_PREVIEW"
    if row["occurrence"]:
        return "DUPLICATE_METADATA_TAG"
    start = row["value_offset"]
    end = start + len(row["_raw"])
    if any(start < high and low < end for low, high in source_pixels):
        return "VALUE_OVERLAPS_SOURCE_IMAGE"
    if tag in LINKS:
        return (None if tag in DIRECTORIES.get(path, ()) and row["type"] in (4, 13) and row["count"] == 1
                else "UNSUPPORTED_DIRECTORY_LINK")
    if tag == 37893 and path in ("IFD0/Exif0", "IFD0/Next/Exif0"):
        if row["type"] == 10 and row["count"] == 1 and len(row["_raw"]) == 8:
            return None
        return "CAMERA_ELEVATION_ANGLE_SCHEMA_UNPROVEN"
    if row["type"] == 13:
        return "UNSUPPORTED_INDIRECT_VALUE"
    if path in ("IFD0", "IFD0/Next"):
        safe = tag in TIFF_SAFE - {700, 33723}
    elif path in ("IFD0/Exif0", "IFD0/Next/Exif0"):
        safe = tag in EXIF_SAFE - {41995}
    elif path in ("IFD0/GPS0", "IFD0/Next/GPS0"):
        safe = 0 <= tag <= 31
    elif path in ("IFD0/Exif0/Interop0", "IFD0/Next/Exif0/Interop0"):
        safe = tag in (1, 2, 4096, 4097, 4098)
    else:
        safe = False
    return None if safe else "PRIVATE_OR_EMBEDDED_VALUE_CLOSURE_UNPROVEN"


def compact_plan(source, processed):
    paths = {row["ifd"] for row in source.rows}
    old_pixels = image_ranges(source, paths)
    filtered = copy(source)
    filtered.rows = []
    omitted = []
    validated_exif = []
    standard_evidence = []
    for row in source.rows:
        reason = omission_reason(row, old_pixels)
        if reason is None and row["tag"] == 37893:
            numerator, denominator = struct.unpack(source.end + "ii", row["_raw"])
            if not denominator:
                reason = "CAMERA_ELEVATION_ANGLE_ZERO_DENOMINATOR"
            else:
                validated_exif.append((row["ifd"], row["tag"], row["occurrence"]))
                standard_evidence.append({key: row[key] for key in ("ifd", "tag", "type", "count", "sha256", "value_offset")}
                                         | {"numerator": numerator, "denominator": denominator})
        if reason:
            omitted.append({key: row[key] for key in ("ifd", "tag", "type", "count", "sha256")} | {"reason": reason})
        else:
            filtered.rows.append(row)
    # TIFF representation comes entirely from the encoded result. Do not
    # inherit any encoded metadata outside the verified storage fields.
    plan = tiff_plan(filtered, processed, validated_exif=validated_exif)
    need(not plan["source_copies"], "TIFF_COMPACT_SOURCE_COPY")
    need(not plan["private_relocation_blockers"], "TIFF_COMPACT_PRIVATE_LAYOUT")
    spans = merge_ranges([(0, 8)] + [(o, o + len(b)) for o, b in plan["blobs"]]
                         + [(new, new + count) for _, count, new in plan["transfers"]])
    processed_bytes = sum(n for _, n, _ in plan["transfers"])
    overhead = 8 + sum(len(blob) for _, blob in plan["blobs"])
    need(sum(b - a for a, b in spans) == processed_bytes + overhead, "TIFF_COMPACT_OUTPUT_OVERLAP")
    plan.update(mode=POLICY, source_image_bytes=sum(b - a for a, b in old_pixels),
                source_image_bytes_omitted=sum(b - a for a, b in old_pixels),
                omitted_metadata=omitted, validated_standard_extensions=standard_evidence,
                processed_stream_bytes=processed_bytes,
                header_and_metadata_bytes=overhead,
                padding_bytes=plan["final_bytes"] - processed_bytes - overhead)
    return filtered, plan
