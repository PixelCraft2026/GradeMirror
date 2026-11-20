"""R0 tensor geometry, deterministic setup and supported native tiles."""
from __future__ import annotations
import numpy as np
import torch
from torch import nn

def configure_torch(seed: int = 43) -> None:
    torch.set_num_threads(4)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False

def coordinates(
    height: int, width: int, device: torch.device | str,
    bounds: tuple[int, int, int, int] | None = None
) -> torch.Tensor:
    y0, y1, x0, x1 = bounds or (0, height, 0, width)
    y = torch.arange(y0, y1, device=device, dtype=torch.float32)
    x = torch.arange(x0, x1, device=device, dtype=torch.float32)
    y = y * (2.0 / (height - 1)) - 1.0 if height > 1 else y * 0.0
    x = x * (2.0 / (width - 1)) - 1.0 if width > 1 else x * 0.0
    yy, xx = torch.meshgrid(y, x, indexing="ij")
    return torch.stack((xx, yy))[None].contiguous(memory_format=torch.channels_last)

def to_tensor(rgb: np.ndarray, device: torch.device | str) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1)[None].to(
        device=device, dtype=torch.float32, memory_format=torch.channels_last
    )

def synchronize(device: torch.device | str) -> None:
    if torch.device(device).type == "cuda":
        torch.cuda.synchronize(device)

@torch.inference_mode()
def render_tiles(model: nn.Module, native: np.ndarray, tile_side: int = 512):
    """Yield exact native cores with global XY and sufficient context halo.

    `native` is RGB uint8/uint16 or normalized float32. Only a tile is cast
    to float32, so native integer decoding does not create a full float image.
    """
    if native.ndim != 3 or native.shape[2] != 3 or tile_side < 1:
        raise ValueError("Expected RGB source and a positive tile side")
    device = next(model.parameters()).device
    height, width = native.shape[:2]
    denominator = float(np.iinfo(native.dtype).max) if native.dtype.kind == "u" else 1.0
    model.eval()
    for y0 in range(0, height, tile_side):
        y1 = min(y0 + tile_side, height)
        sy0, sy1 = max(0, y0 - 1), min(height, y1 + 1)
        for x0 in range(0, width, tile_side):
            x1 = min(x0 + tile_side, width)
            sx0, sx1 = max(0, x0 - 1), min(width, x1 + 1)
            rgb = native[sy0:sy1, sx0:sx1].astype(np.float32) / denominator
            tensor = to_tensor(rgb, device)
            xy = coordinates(height, width, device, (sy0, sy1, sx0, sx1))
            output = model(tensor, xy)[0, :, y0-sy0:y1-sy0, x0-sx0:x1-sx0]
            array = output.permute(1, 2, 0).cpu().numpy()
            if not np.isfinite(array).all():
                raise FloatingPointError("Nonfinite rendered tile")
            yield (y0, y1, x0, x1), array
