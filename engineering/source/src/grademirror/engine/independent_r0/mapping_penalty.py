"""Exact independent M05 regularizer definitions; no old batch/runtime imports."""
import torch
from torch.nn import functional as F

REGULARIZATION = {"windows": 64, "difference_step": 4 / 255,
                  "jitter_range": .10, "generator_seed": 74193,
                  "gain_floor": .05, "gain_ceiling": 3.0, "gain_weight": .25}

LUMA = (.2126, .7152, .0722)

def penalty_from_predictions(center, plus, minus, mask, config=REGULARIZATION):
    h = config["difference_step"]
    weights = center.new_tensor(LUMA)[None, :, None, None]
    curvature = ((plus + minus - 2 * center) / h).square().mean(1, keepdim=True)
    gain = (((plus - minus) / (2 * h)) * weights).sum(1, keepdim=True)
    gain_penalty = F.relu(config["gain_floor"] - gain).square() + F.relu(gain - config["gain_ceiling"]).square()
    denominator = mask.sum().clamp_min(1)
    curvature_mean = (curvature * mask).sum() / denominator
    gain_mean = (gain_penalty * mask).sum() / denominator
    return curvature_mean + config["gain_weight"] * gain_mean, curvature_mean, gain_mean

def sampled_penalty(model, patch, xy, valid, generator):
    count = min(REGULARIZATION["windows"], len(patch))
    patch, xy, valid = patch[:count], xy[:count], valid[:count]
    h = REGULARIZATION["difference_step"]
    shift = (torch.rand((count, 1, 1, 1), generator=generator, device=patch.device) * 2 - 1) * REGULARIZATION["jitter_range"]
    jittered = patch + shift
    safe = ((jittered.amin(1, keepdim=True) >= h) &
            (jittered.amax(1, keepdim=True) <= 1 - h)) | (valid < .5)
    mask = (F.avg_pool2d(safe.float(), 3, stride=1) == 1).float()
    batched = torch.cat((jittered, jittered + h, jittered - h), 0).contiguous(memory_format=torch.channels_last)
    output = model(batched, torch.cat((xy, xy, xy), 0), torch.cat((valid, valid, valid), 0))[:, :, 1:-1, 1:-1]
    center, plus, minus = output.split(count, 0)
    total, curve, gain = penalty_from_predictions(center, plus, minus, mask)
    return total, curve, gain, mask.mean()
