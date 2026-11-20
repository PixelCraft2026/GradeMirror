"""Integer global dependencies, independent of tensor libraries and CUDA.

Intervals are half open and always use the original level's coordinates.
No cropped image is treated as a new image boundary.
"""
from dataclasses import dataclass


def union(a, b):
    return min(a[0], b[0]), max(a[1], b[1])


def down_preimage(output, length):
    a, b = output
    return max(0, 2*a-1), min(length, 2*(b-1)+3)


def up_preimage(output, coarse_length):
    a, b = output
    # floor((i-.5)/2) == (2*i-1)//4, in exact integers.
    return max(0, (2*a-1)//4), min(coarse_length, (2*(b-1)-1)//4+2)


@dataclass(frozen=True)
class Stripe:
    core: tuple
    shapes: tuple
    reconstruction: tuple
    gaussian: tuple

    @property
    def base_rows(self):
        a, b = self.gaussian[0]
        return b-a

    def pixels(self, regions):
        return sum((b-a)*self.shapes[j][1] for j, (a,b) in enumerate(regions))


def stripe(height, width, start, end):
    if (any(type(v) is not int for v in (height,width,start,end)) or
            not (height > 0 and width > 0 and 0 <= start < end <= height)):
        raise ValueError('GLOBAL_REGION')
    shapes = [(height,width)]
    for _ in range(7):
        h,w = shapes[-1]
        shapes.append(((h+1)//2,(w+1)//2))
    reconstruction = [(start,end)]
    for j in range(1,8):
        reconstruction.append(up_preimage(reconstruction[-1],shapes[j][0]))
    gaussian = reconstruction.copy()
    # Union every band's own dependency with every downsample dependency.
    for j in range(6,-1,-1):
        gaussian[j] = union(gaussian[j],down_preimage(gaussian[j+1],shapes[j][0]))
    return Stripe((start,end),tuple(shapes),tuple(reconstruction),tuple(gaussian))


def plan(height, width, core_rows):
    if core_rows not in (256,512):
        raise ValueError('CORE_ROWS')
    return [stripe(height,width,a,min(height,a+core_rows)) for a in range(0,height,core_rows)]


def workspace_bound(s):
    """Conservative simultaneously live capacity, excluding external complete O.

    Eight FP32 Gaussian-capacity slots cover input, remap/adjacent pyramids,
    exp/difference, D scratch and index-select temporaries. Eight FP32
    reconstruction slots cover bands, cached laps, masks/weights and U.
    Six FP64 base slots cover P3 gather/multiply/add and coordinates.
    Four RGB core slots cover proof/compose/IPC, plus 8MiB metadata/frames.
    P3 is completed before LLF: these two phases do not coexist. Take their
    maximum, retaining proof buffers across both. No full P3 RGB footprint.
    """
    gp=s.pixels(s.gaussian);rp=s.pixels(s.reconstruction)
    base=s.base_rows*s.shapes[0][1]
    core=(s.core[1]-s.core[0])*s.shapes[0][1]
    return dict(gaussian_slots_bytes=8*4*gp,reconstruction_slots_bytes=8*4*rp,
                p3_slots_bytes=6*8*base,proof_compose_ipc_bytes=4*3*4*core,
                fixed_bytes=8*1024**2,
                total_bytes=max(8*4*(gp+rp),6*8*base)+4*3*4*core+8*1024**2)
