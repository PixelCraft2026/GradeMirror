"""Display preview limits, safe to import without an image or model runtime."""
MAX_FIT_SIDE = 4096
MAX_FIT_PIXELS = MAX_FIT_SIDE ** 2
MAX_PREVIEW_BATCH_TILES = 16


class PreviewSuperseded(Exception):
    """A newer GUI intent replaced pending work; the resident host stays alive."""
