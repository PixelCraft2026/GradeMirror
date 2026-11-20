"""Retired algorithm data only: historical metadata and already committed transactions.

No numeric imports, model code, worker module, or executable dispatch is retained.
"""
from .common import GMError

B0="independent-b0-v1"
C5="independent-c5-lab-m05-v1"
RETIRED_PROFILES={'independent-b0-v1': {'id': 'independent-b0-v1', 'label': 'B0', 'version': '0.2.0', 'fit_contract': 'gm005-fit-v1', 'core_sha256': '8070e8d3a1bc483130cdfe071e869d72569e5e86ffeddf1a899d1b9a4381bfbd', 'contract_sha256': 'aaec6a8c691160d0e323a041deb2bd471b732b8c7c9ad1640b435a17ebf7c6ec', 'settings': {'rgb_width': 64, 'xy_width': 32, 'context_width': 64, 'omega': 30.0, 'position_decay': 0.01, 'updates': 1000, 'windows': 484, 'window_side': 13, 'learning_rate': 0.001, 'final_learning_rate': 0.0001, 'seed': 42, 'long_side': 512, 'tile_side': 512}, 'shapes': {'color_features.weight': (64, 3, 1, 1), 'color_features.bias': (64,), 'position_features.weight': (32, 2, 1, 1), 'position_features.bias': (32,), 'mix_features.weight': (64, 96, 1, 1), 'mix_features.bias': (64,), 'neighborhood.weight': (64, 1, 3, 3), 'decode_features.weight': (64, 64, 1, 1), 'decode_features.bias': (64,), 'edit_head.weight': (3, 64, 1, 1), 'edit_head.bias': (3,)}, 'artifact_kind': 'independent_b0_recipe'}, 'independent-c5-lab-m05-v1': {'id': 'independent-c5-lab-m05-v1', 'label': 'Lab + M05', 'version': '0.3.0', 'fit_contract': 'gm006-fit-v1', 'core_sha256': '7745d262dc27c3129cec04f2892e2ca128adc935ec66ccae7fb1aed4d795a9af', 'contract_sha256': '6ff38889780a5f65fae29938562cfc4612a70ca6be40fe6f5cf06502ceaa6899', 'settings': {'rgb_width': 32, 'rgb_depth': 2, 'xy_width': 32, 'xy_depth': 2, 'context_width': 64, 'depthwise_bias': False, 'omega': 30, 'nonhead_weight_init': 'B0_frozen_law_omega30', 'nonhead_bias_init': 'zero', 'head_weight_init': 'zero', 'head_bias_init': 'conv_uniform', 'optimizer': 'Adam', 'optimizer_objects': 1, 'beta1': 0.9, 'beta2': 0.9, 'epsilon': 1e-08, 'learning_rate': 0.001, 'final_learning_rate': 0.0001, 'scheduler_T_max': 1000, 'scheduler': 'B0_cosine_post_step', 'gradient_clip_norm': None, 'decay_xy_weight': 0.1, 'decay_xy_bias': 0, 'decay_rgb': 0, 'decay_fusion': 0, 'window_side': 12, 'halo': False, 'windows': 484, 'supervision': 'all_core_pixels', 'updates': 1000, 'train_cudnn_tf32': False, 'train_matmul_tf32': False, 'train_benchmark': False, 'train_deterministic': True, 'sampling': 'B0_private_integer_generator', 'training_size': 'long_side_512', 'resize': 'cv2_INTER_AREA_float32', 'post_resize_q8': False, 'source_training_precision': 'native_uint16', 'native_render_precision': 'native_uint16', 'native_tile_side': 512, 'render_fp32_strict': True, 'color_domain': 'CIELAB_D65_normalized', 'loss': 'normalized_Lab_L1_mean', 'intensity': 1, 'lab_mode': 'lab_domain', 'mapping_coefficient': 0.05, 'mapping_penalty_domain': 'encoded_sRGB_through_Lab_facade', 'seed': 43, 'long_side': 512, 'tile_side': 512}, 'shapes': {'color_features.weight': (32, 3, 1, 1), 'color_features.bias': (32,), 'position_features.weight': (32, 2, 1, 1), 'position_features.bias': (32,), 'mix_features.weight': (64, 64, 1, 1), 'mix_features.bias': (64,), 'neighborhood.weight': (64, 1, 3, 3), 'decode_features.weight': (64, 64, 1, 1), 'decode_features.bias': (64,), 'edit_head.weight': (3, 64, 1, 1), 'edit_head.bias': (3,), 'rgb_extra.0.weight': (32, 32, 1, 1), 'rgb_extra.0.bias': (32,), 'xy_extra.0.weight': (32, 32, 1, 1), 'xy_extra.0.bias': (32,)}, 'artifact_kind': 'independent_c5_lab_m05_recipe'}}
ALIASES={"B0":B0,"C5":C5,"independent_b0":B0,"independent_c5_lab_m05":C5}

def is_retired(profile_id):
    return isinstance(profile_id,str) and (profile_id in RETIRED_PROFILES or profile_id in ALIASES)

def historical_profile(profile_id):
    if profile_id not in RETIRED_PROFILES:
        raise GMError("ALGORITHM_VERSION_UNAVAILABLE","Unknown historical algorithm identity",4)
    return RETIRED_PROFILES[profile_id]

def historical_settings(profile_id,training_hw=None):
    profile=historical_profile(profile_id);settings=dict(profile["settings"])
    if profile_id==C5:
        if not isinstance(training_hw,list) or len(training_hw)!=2 or any(type(n)is not int or n<13 for n in training_hw):
            raise GMError("TRAINING_GEOMETRY_INVALID","Historical training grid is invalid",7)
        settings["baseline_training_hw"]=list(training_hw)
    return settings
