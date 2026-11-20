"""Explicit, fixed-pair portable CPU replay; production activation remains false."""
BACKEND = 'r0-cpu-portable-v1'


def replay_cpu_recipe(*args, **kwargs):
    from .api import replay_cpu_recipe as replay
    return replay(*args, **kwargs)
