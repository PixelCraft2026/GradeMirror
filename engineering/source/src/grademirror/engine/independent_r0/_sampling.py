"""Requested-pixel gather used by the R0 private sampler."""
import torch
from .tensor_ops import coordinates

class WindowPicker:
    """Gather O(M s^2) requested pixels, without a full-image unfold."""

    def __init__(self, before: torch.Tensor, after: torch.Tensor, side: int, seed: int):
        self.before = before
        self.after = after
        self.height, self.width = before.shape[-2:]
        if min(self.height, self.width) < side:
            raise ValueError("Training image is smaller than the supervised window")
        self.side = side
        self.xy = coordinates(self.height, self.width, before.device)
        self.packed = torch.cat((before, self.xy), dim=1)[0].reshape(5, -1)
        self.target = after[0].reshape(3, -1)
        self.generator = torch.Generator(device=before.device).manual_seed(seed)
        self.halo_offset = torch.arange(-1, side + 1, device=before.device)
        self.core_offset = torch.arange(side, device=before.device)

    def extract(self, tops: torch.Tensor, lefts: torch.Tensor):
        yy = tops[:, None, None] + self.halo_offset[None, :, None]
        xx = lefts[:, None, None] + self.halo_offset[None, None, :]
        mask = ((yy >= 0) & (yy < self.height) & (xx >= 0) & (xx < self.width))
        indices = yy.clamp(0, self.height - 1) * self.width + xx.clamp(0, self.width - 1)
        patch = self.packed[:, indices].permute(1, 0, 2, 3).contiguous(
            memory_format=torch.channels_last
        )
        cy = tops[:, None, None] + self.core_offset[None, :, None]
        cx = lefts[:, None, None] + self.core_offset[None, None, :]
        target = self.target[:, cy * self.width + cx].permute(1, 0, 2, 3).contiguous(
            memory_format=torch.channels_last
        )
        return patch[:, :3], patch[:, 3:], mask[:, None].to(torch.float32), target

