"""Integer capacity model derived from the preview.21 execution graph.

Each stage lists the simultaneously live objects. An alias uses its owner's
name, and a real copy has a separate name. Limits are in bytes throughout.
"""
from dataclasses import asdict, dataclass

from ..cpu_replay.contract import digest, need
from ..engine.cpu_postprocess.geometry import stripe, workspace_bound
from ..fine_contract import coefficient_shape, validate_limits
from ..gpu_resources import render_span
from ..image_limits import GIB, MAX_IMAGE_BYTES, MAX_PIXELS, MIB, check_geometry

SCHEMA = "gm075-dynamic-admission-v2"
STEP = 64 * MIB
GPU_GUARD = 512 * MIB
CPU_PIXELS = 50_118_276
CPU_AXIS = 16_384
FORMATS = {
    "jpeg": (8,), "png": (8, 16), "tiff": (8, 16), "webp": (8,), "bmp": (8,),
}
# JPEG's field is 16-bit, while the bundled libjpeg encoder uses a lower
# JPEG_MAX_DIMENSION. WebP uses 14-bit coded dimensions with one value reserved.
CODEC_AXES = {"jpeg": (65_535, 65_500), "webp": (16_383, 16_383)}
HOST_GUARD = (256 * MIB, 256 * MIB)


def round_up(value, unit=STEP):
    return (value + unit - 1) // unit * unit


def training_grid(w, h):
    # Python's ties-to-even round, exactly and without a float intermediate.
    axis = max(w, h)
    result = []
    for n in (h, w):
        q, remainder = divmod(n * 512, axis)
        result.append(max(1, q + (2 * remainder > axis or
                                  2 * remainder == axis and q % 2 == 1)))
    return tuple(result)


def execution_grid(w, h):
    # The current fit performs this multiplication after division. A rare
    # exact half-pixel tie can round differently from the header probe.
    scale = 512 / max(w, h)
    return tuple(max(1, round(n * scale)) for n in (h, w))


@dataclass(frozen=True)
class Photo:
    width: int
    height: int
    bits: int
    codec: str
    file_bytes: int | None

    @property
    def pixels(self):
        return self.width * self.height

    @property
    def rgb(self):
        return self.pixels * 3 * (self.bits // 8)

    @property
    def grid(self):
        return training_grid(self.width, self.height)

    @classmethod
    def from_header(cls, header, *, output=False):
        w, h, bits = (header[k] for k in ("width", "height", "bits"))
        check_geometry(w, h)
        if max(w, h) < 512:
            from ..preview.startup_fixture import allowed as startup_fixture
            need(startup_fixture(header), "IMAGE_TOO_SMALL")
        codec = header["format"].casefold()
        need(type(bits) is int and bits in FORMATS.get(codec, ()), "IMAGE_FORMAT_UNSUPPORTED")
        need(header.get("channels", 3) == 3, "IMAGE_RGB_SCHEMA")
        source_axis, output_axis = CODEC_AXES.get(codec, (MAX_PIXELS, MAX_PIXELS))
        need(max(w, h) <= source_axis, "IMAGE_CODEC_AXIS", codec)
        if output:
            need(max(w, h) <= output_axis, "IMAGE_OUTPUT_CODEC_AXIS", codec)
        if "training_grid" in header:
            need(tuple(header["training_grid"]) == training_grid(w, h), "IMAGE_GRID_CHANGED")
        length = header.get("bytes")
        need(length is None or type(length) is int and 0 < length <= MAX_IMAGE_BYTES, "IMAGE_FILE_SIZE")
        return cls(w, h, bits, codec, length)


@dataclass(frozen=True)
class Buffer:
    name: str
    owner: str
    ram: int = 0
    commit: int = 0
    cuda: int = 0
    shared: bool = False


def host(name, owner, size, *, shared=False):
    return Buffer(name, owner, size, size, shared=shared)


def cuda(name, size):
    return Buffer(name, "compute", cuda=size)


@dataclass(frozen=True)
class Stage:
    name: str
    objects: tuple
    runtime_ram: int
    runtime_commit: int
    uncertainty_ram: int = 0
    uncertainty_commit: int = 0
    cuda_cache: int = 0

    def limits(self):
        # The draft reports whole-stage live capacity. Runtime incremental
        # binding requires an allocation inventory, which is still pending.
        need(len({item.name for item in self.objects}) == len(self.objects), "DYNAMIC_BUFFER_ALIAS")
        ram = self.runtime_ram + self.uncertainty_ram + sum(item.ram for item in self.objects)
        commit = self.runtime_commit + self.uncertainty_commit + sum(item.commit for item in self.objects)
        allocated = sum(item.cuda for item in self.objects)
        reserved = round_up(allocated + self.cuda_cache) if allocated else 0
        return dict(stage=self.name, ram=round_up(ram + HOST_GUARD[0]),
                    commit=round_up(commit + HOST_GUARD[1]), allocated=allocated,
                    reserved=reserved, vram=reserved + GPU_GUARD if reserved else 0,
                    object_ram=sum(item.ram for item in self.objects),
                    object_commit=sum(item.commit for item in self.objects),
                    runtime_ram=self.runtime_ram, runtime_commit=self.runtime_commit,
                    uncertainty_ram=self.uncertainty_ram, uncertainty_commit=self.uncertainty_commit,
                    external_ram_guard=HOST_GUARD[0], external_commit_guard=HOST_GUARD[1])


def pyramid(h, w):
    shapes = [(h, w)]
    for _ in range(7):
        h, w = (h + 1) // 2, (w + 1) // 2
        shapes.append((h, w))
    return shapes


def workspace(h, w, core):
    # The seven reductions give a period of 128 rows. Full interior
    # stripes therefore have the same capacity at offsets of 256/512.
    # Keep the eight stripes at either edge and one interior sample.
    n = (h + core - 1) // core
    starts = {*range(min(n, 8)), *range(max(n - 8, 0), n), n // 2}
    return max(workspace_bound(stripe(h, w, i * core, min(h, (i + 1) * core)))["total_bytes"]
               for i in starts)


def decoder_scratch(photo):
    if photo.codec == "png":
        # imagecodecs receives a complete compressed bytes object.
        compressed = photo.file_bytes if photo.file_bytes is not None else MAX_IMAGE_BYTES
        return compressed + photo.rgb
    if photo.codec == "tiff":
        # The decoder may retain compressed segments and a native staging image.
        compressed = photo.file_bytes if photo.file_bytes is not None else MAX_IMAGE_BYTES
        return compressed + photo.rgb
    # PIL RGB uses four bytes per pixel internally. Orientation may create
    # another PIL surface before NumPy materializes the owned RGB array.
    return 8 * photo.pixels


def encoder_scratch(photo):
    rows = photo.width * min(64, photo.height)
    if photo.codec == "tiff":
        return min(photo.rgb, 8 * rows * 3 * (photo.bits // 8)) + MIB
    if photo.codec == "png":
        # Whole PNG bytes plus a replacement buffer during ICC insertion.
        packed = photo.rgb + photo.height
        return 2 * (packed + (packed + 99) // 100 + 2 * MIB)
    if photo.codec == "webp":
        # PIL ARGB surface plus libwebp transform/token/bitstream working area.
        # This intentionally remains an analysis allowance, pending large WebP.
        return 24 * photo.pixels + 32 * MIB
    return 4 * photo.pixels + 4 * MIB


def output_decode_scratch(photo):
    if photo.codec == "png":
        packed = photo.rgb + photo.height
        return packed + (packed + 99) // 100 + 2 * MIB + photo.rgb
    if photo.codec == "tiff":
        return photo.rgb + encoder_scratch(photo)
    return decoder_scratch(photo)


def model(source, reference, backend, quality, operation):
    need(backend in ("cpu", "gpu", "mixed") and quality in ("fast", "fine") and
         operation in ("fit", "refine", "replay"), "DYNAMIC_OPERATION")
    need(quality == "fine" or operation != "refine", "DYNAMIC_OPERATION")
    need(backend != "mixed" or quality == "fine" and operation != "refine", "MIXED_OPERATION_UNQUALIFIED")
    s = Photo.from_header(source, output=True)
    r = Photo.from_header(reference)
    need(s.grid == r.grid, "PAIR_ASPECT_RATIO_MISMATCH")
    if backend == "cpu":
        need(s.pixels <= CPU_PIXELS, "CPU_NATIVE_GEOMETRY_SCOPE")
    if backend == "mixed":
        need(s.pixels <= CPU_PIXELS, "LIGHT_MIXED_NATIVE_GEOMETRY_SCOPE")
    fine = quality == "fine"
    trains = operation == "fit"
    solves = fine and operation != "replay"
    k = coefficient_shape((r.height, r.width, 3))[0] if fine else 0
    if fine:
        validate_limits(source, reference)
    tiles = ((s.height + 511) // 512) * ((s.width + 511) // 512)
    tile_area = render_span(s.height) * render_span(s.width)
    grid_area = s.grid[0] * s.grid[1]
    f = 12 * s.pixels
    rf = 12 * r.pixels
    hr = 24 * r.pixels
    shapes = pyramid(s.height, s.width)
    levels = sum(h * w for h, w in shapes)
    band_levels = levels - shapes[-1][0] * shapes[-1][1]
    core = {"cpu": 256, "mixed": 512}.get(backend) if fine else None
    work = workspace(s.height, s.width, core) if core else 0
    # Existing Job/resource envelopes bound running processes. This object
    # tracks the analytical requirement independently for a future binding.
    from ..fine_resources import solver_memory_bytes
    sparse = (solver_memory_bytes((s.width, s.height), (r.width, r.height))
              - 512 * MIB) if solves else 0

    gpu_r0 = backend in ("gpu", "mixed")
    need_reference = trains or solves
    source_map = host("source_rgb", "io", s.rgb, shared=backend != "gpu")
    ref_map = host("reference_rgb", "io", r.rgb, shared=backend != "gpu")
    o = host("O", "io" if backend != "gpu" else "compute", f, shared=backend != "gpu")
    y = host("Y", "io", f, shared=backend != "gpu")
    coeff = host("b64", "compute", 24 * k)
    input_objects = (source_map, ref_map) if need_reference else (source_map,)
    stage_specs = []

    def add(name, objects, ram, commit, *, uncertain=128, cache=0):
        stage_specs.append(Stage(name, tuple(objects), ram * MIB, commit * MIB,
                                 uncertain * MIB, uncertain * MIB, cache * MIB))

    add("probe", (), 160, 256, uncertain=96)
    decode_objects = (host("source_decode_temporary", "io", decoder_scratch(s)),)
    if need_reference:
        # Source is decoded first. At the second decoder entry the first
        # source mapping can coexist with the reference decoder temporaries.
        decode_objects = (host("sequential_decoder_temporary", "io",
                               max(decoder_scratch(s), decoder_scratch(r))),)
    add("decode", (*input_objects, *decode_objects), 224, 384)
    if backend != "cpu":
        add("reference_fetch", (source_map, ref_map,
                host("reference_decode_temporary", "io", decoder_scratch(r))), 224, 384)
    if trains:
        # downscale_native is sequential, while both final training tensors
        # and source/reference integer mappings coexist during fitting.
        resize = host("normalization_pair_temporary", "compute", 2 * max(f, rf))
        pair = host("training_grids_and_rgb_context", "compute", 128 * grid_area)
        add("training_prepare", (*input_objects, resize, pair), 512, 960)
        if gpu_r0:
            add("training", (*input_objects, pair, cuda("r0_train_activations", 1536 * grid_area)),
                1024, 2432, cache=896)
        else:
            add("training", (*input_objects, pair,
                             host("r0_train_activations", "compute", 1536 * grid_area)), 896, 1280)
    render_objects = (source_map, o, host("tile_host", "compute", 48 * tile_area))
    if backend == "gpu":
        render_objects += (host("source_ipc_copy", "compute", s.rgb),)
    if backend == "mixed":
        # Its staged input/renderer handoff can overlap an owned O with
        # the destination mapping. The CPU portable worker writes into O.
        render_objects += (host("mixed_handoff_O", "io", f, shared=True),)
    if solves:
        render_objects += (ref_map,)
    if gpu_r0:
        render_objects += (cuda("r0_tile_activations", 1536 * tile_area),)
        # GM063 first-fit complete-tree peaks exceed the numeric-only phase
        # sampler. Residual after the declared R0 objects, rounded to 64MiB,
        # gives 1600/3328MiB for the training context retained into rendering.
        # The cold replay context has separate evidence.
        ram_base, commit_base = (1600, 3328) if trains else (1024, 2176)
        add("R0", render_objects, ram_base, commit_base, cache=256)
    else:
        render_objects += (host("r0_tile_activations", "compute", 1536 * tile_area),)
        add("R0", render_objects, 704, 1024)
    if solves:
        area_rows = (s.height + r.height - 1) // r.height + 1
        area_temp = max(36 * s.width * area_rows + 144 * (s.width + r.width),
                        64 * r.height * area_rows)
        add("P3_prepare", (source_map, o, ref_map, host("H64", "compute", hr),
                           host("T64", "compute", hr), host("area_and_T_temporary", "compute",
                                                          max(hr, area_temp))),
            768, 1536 if gpu_r0 else 1024)
        add("P3", (source_map, o, host("H64", "compute", hr), host("T64", "compute", hr),
                   host("H_T_child_arrays", "solver", 2 * hr),
                   host("solver_wire_frame", "solver", 8 * MIB),
                   host("sparse_A_D_Q_CG", "solver", sparse), coeff),
            1152, 2176 if gpu_r0 else 1472, uncertain=192)
    if core:
        add("CPU", (source_map, o, y, coeff, host("footprint_workspace", "compute", work)),
            832, 1280, uncertain=192)
    elif fine:
        p = host("P_or_LP", "compute", f)
        lo = host("LO_or_Y", "compute", f)
        p3_strip = s.width * min(s.height, 128)
        add("P3_expand", (source_map, o, p, coeff,
                          cuda("P3_fp64_gather_and_coordinates", 144 * p3_strip +
                               24 * k + 32 * (s.width + s.height))),
            1024, 2304, cache=256)
        # LLF retains g, two adjacent remap pyramids, two lazy lap caches
        # and output bands. One level's masks, weights, interpolation and
        # reconstruction scratch can coexist. Geometry holds index arrays.
        indices = sum(32 * (ch + cw) + 20 * (h + w)
                      for (h, w), (ch, cw) in zip(shapes, shapes[1:]))
        llf_cuda = (cuda("three_scalar_pyramids", 12 * levels),
                    cuda("bands_and_two_lap_caches", 12 * band_levels),
                    cuda("scalar_masks_and_operator_scratch", 16 * s.pixels),
                    cuda("D_U_indices", indices))
        cache = 384
        # Windows can charge CUDA backing to the compute Job's commit.
        # Reserve one rounded allocator envelope independently of host RSS.
        backing = round_up(sum(item.cuda for item in llf_cuda) + cache * MIB)
        add("GPU_LLF", (source_map, o, p, lo, coeff,
                        host("scalar_transfer_and_finite_masks", "compute", 10 * s.pixels),
                        Buffer("windows_cuda_backing", "compute", commit=backing), *llf_cuda),
            1152, 2944, uncertain=192, cache=cache)
        add("compose", (source_map, o, p, lo, host("FP64_compose_rows", "compute",
                                                   96 * s.width * min(s.height, 64))),
            1024, 2176)
    if not core:
        add("handoff", (source_map, host("Y_private" if fine else "O", "compute", f), y,
                        host("handoff_finite_mask", "io", 3 * s.width * min(s.height, 64))),
            960 if backend == "gpu" else 448, 1152 if backend == "gpu" else 768)
        add("handoff_receive", (source_map, y, host("handoff_finite_mask", "io", 3 * s.width * min(s.height, 64))),
            448, 768)
    add("resident", (source_map, y), 320, 512, uncertain=64)
    if core == 512:
        add("ready_quantization", (source_map, y,
                host("blend_quantize_rows", "io", 102 * s.width * min(s.height, 64))),
            352, 576, uncertain=64)
    if backend != "cpu":
        add("blend", (source_map, y, host("blend_quantized", "io", s.pixels * 6),
                host("blend_quantize_rows", "io", 96 * s.width * min(s.height, 64))),
            448, 768)
    add("roi", (source_map, y, host("roi_or_preview_frame", "io", 16 * MIB)),
        352, 576, uncertain=64)
    add("reference_roi", (source_map, y, ref_map,
                          host("reference_decode_temporary", "io", decoder_scratch(r)),
                          host("roi_or_preview_frame", "io", 16 * MIB)), 352, 576, uncertain=64)
    encode_row = s.width * min(s.height, 64)
    add("export", (source_map, y, host("output_quantized", "io", s.rgb),
                   host("codec_staging", "io", encoder_scratch(s)),
                   host("blend_quantize_rows", "io", 96 * encode_row)), 448, 768)
    readback = (source_map, y, host("encoded_readback_pixels", "io", s.rgb),
                host("readback_decode_temporary", "io", output_decode_scratch(s)))
    if backend == "mixed" and s.codec == "jpeg":
        readback += (host("output_quantized", "io", s.rgb),)
    add("readback", readback, 448, 768)
    rows = [row.limits() for row in stage_specs]
    limits = ("ram", "commit", "vram", "reserved", "allocated")
    # These are finite process/Job constraints. They never enter startup
    # demand as preallocated objects. Runtime attribution is deliberately
    # conservative until each role's actual process graph is bound.
    role_caps = {}
    for stage in stage_specs:
        for role in {item.owner for item in stage.objects if item.commit}:
            working = sum(item.commit for item in stage.objects if item.owner == role)
            cap = round_up(working + stage.runtime_commit + stage.uncertainty_commit)
            role_caps[role] = max(role_caps.get(role, 0), cap)
    if solves:
        role_caps["solver"] = max(3 * GIB, role_caps["solver"])
    need(all(cap <= 256 * GIB for cap in role_caps.values()), "DYNAMIC_JOB_CAPACITY")
    notes = []
    if backend in ("cpu", "mixed") and max(s.width, s.height) > CPU_AXIS:
        notes.append("OUTSIDE_REAL_WITNESS_AXIS_SCOPE")
    if trains and any(execution_grid(photo.width, photo.height) != photo.grid for photo in (s, r)):
        notes.append("HEADER_AND_FIT_GRID_ROUNDING_DIVERGENCE")
    if core and work > 512 * MIB:
        notes.append("PREVIEW21_CPU_WORKSPACE_CAP")
    result = dict(schema=SCHEMA, backend=backend, quality=quality, operation=operation,
                  source=asdict(s), reference=asdict(r), training_grid=list(s.grid),
                  geometry=dict(tiles=tiles, maximum_render_pixels=tile_area, coefficient_k=k,
                                pyramid=[list(item) for item in shapes], cpu_core_rows=core,
                                cpu_workspace=work), stages=rows,
                  objects={row.name: [asdict(item) for item in row.objects] for row in stage_specs},
                  proposed_job_caps=role_caps,
                  structural_notes=notes,
                  startup={name: max(row[name] for row in rows) for name in limits})
    result["capacity_id"] = digest(result)
    return result

