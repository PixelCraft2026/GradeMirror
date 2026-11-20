"""Canonical untrained initialization only; no legacy forward, fit or optimizer."""
import math
from dataclasses import dataclass
from torch import nn

@dataclass(frozen=True)
class InitialSettings:
    rgb_width:int=64
    xy_width:int=32
    context_width:int=64
    omega:float=30.0
    seed:int=43

class InitializationTemplate(nn.Module):
    def __init__(self, settings: InitialSettings = InitialSettings()) -> None:
        super().__init__()
        self.settings = settings
        self.color_features = nn.Conv2d(3, settings.rgb_width, 1)
        self.position_features = nn.Conv2d(2, settings.xy_width, 1)
        self.mix_features = nn.Conv2d(
            settings.rgb_width + settings.xy_width, settings.context_width, 1
        )
        self.neighborhood = nn.Conv2d(
            settings.context_width, settings.context_width, 3,
            padding=1, groups=settings.context_width, bias=False
        )
        self.decode_features = nn.Conv2d(settings.context_width, settings.context_width, 1)
        self.edit_head = nn.Conv2d(settings.context_width, 3, 1)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        for layer in (self.color_features, self.position_features):
            bound = 1.0 / layer.in_channels
            nn.init.uniform_(layer.weight, -bound, bound)
            nn.init.zeros_(layer.bias)
        for layer in (self.mix_features, self.neighborhood, self.decode_features):
            fan_in = layer.weight.shape[1] * layer.kernel_size[0] * layer.kernel_size[1]
            bound = math.sqrt(6.0 / fan_in) / self.settings.omega
            nn.init.uniform_(layer.weight, -bound, bound)
            if layer.bias is not None:
                nn.init.zeros_(layer.bias)
        nn.init.zeros_(self.edit_head.weight)
        nn.init.zeros_(self.edit_head.bias)

