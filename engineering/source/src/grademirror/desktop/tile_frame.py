"""Qt images for already verified immutable RGB8 tiles, reused across pans."""
import base64
from PySide6.QtGui import QColorSpace, QImage


class TileFrame:
    def __init__(self, reply, previous=None):
        self.roi = tuple(reply["preview"]["roi"])
        meta = reply["preview"]
        self.binding = dict(meta["binding"])
        self.color_key = (meta.get("icc_profile_base64", ""), meta.get("color_interpretation"))
        reusable = (previous.images if isinstance(previous, TileFrame)
                    and previous.binding == self.binding and previous.color_key == self.color_key else {})
        old_patches = previous.patches if reusable else {}
        color = None
        if self.color_key[0]:
            color = QColorSpace.fromIccProfile(base64.b64decode(self.color_key[0]))
        elif self.color_key[1] in {"assumed_sRGB", "marked_sRGB"}:
            color = QColorSpace(QColorSpace.SRgb)
        self.images = {}
        self.patches = {}
        for bounds, raw, metadata in reply["tiles"]:
            key = (bounds, metadata["pixel_sha256"])
            image = reusable.get(key)
            if image is None:
                width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
                image = QImage(raw, width, height, width*3, QImage.Format_RGB888).copy()
                if color is not None and color.isValid():
                    image.setColorSpace(color)
            self.images[key] = image
            clipped = (max(bounds[0],self.roi[0]),max(bounds[1],self.roi[1]),
                       min(bounds[2],self.roi[2]),min(bounds[3],self.roi[3]))
            patch_key = (clipped,metadata["pixel_sha256"])
            patch = old_patches.get(patch_key)
            if patch is None:
                patch = (image if clipped == bounds else image.copy(clipped[0]-bounds[0],
                    clipped[1]-bounds[1],clipped[2]-clipped[0],clipped[3]-clipped[1]))
            self.patches[patch_key] = patch

    def width(self):
        return self.roi[2]-self.roi[0]

    def height(self):
        return self.roi[3]-self.roi[1]

    def pixelColor(self, x, y):
        x, y = x+self.roi[0], y+self.roi[1]
        for (bounds, _), image in self.images.items():
            if bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]:
                return image.pixelColor(x-bounds[0], y-bounds[1])
        raise IndexError("Pixel outside native frame")
