"""Retain M05's RGB mapping definition around a normalized-Lab field."""
from .color_lab import lab_to_rgb,display_model
from .mapping_penalty import sampled_penalty as original_RGB_sampled_penalty

def sampled_penalty_lab(model,patch,xy,valid,generator):
 # Training patches are normalized Lab. M05's neutral RGB perturbation, RGB
 # curvature and RGB luminance gain retain their original units and masks.
 rgb=lab_to_rgb(patch)
 return original_RGB_sampled_penalty(display_model(model),rgb,xy,valid,generator)

def install(module):
 assert module.sampled_penalty is original_RGB_sampled_penalty,'M05_ADAPTER_DOUBLE_INSTALL'
 module.sampled_penalty=sampled_penalty_lab
