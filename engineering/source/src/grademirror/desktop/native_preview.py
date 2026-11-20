"""Bounded verified native tiles; GUI frames need no full-viewport RGB copy."""
from collections import OrderedDict
import hashlib
import json
import time

from grademirror.preview_limits import MAX_PREVIEW_BATCH_TILES, PreviewSuperseded


class NativePreviewCache:
    TILE = 512
    MAX_BYTES = 64 * 1024**2
    MAX_VIEW_PIXELS = 16 * 1024**2
    MAX_FRAME_TILE_BYTES = 64 * 1024**2

    def __init__(self):
        self.tiles = OrderedDict()
        self.bytes = 0

    def clear(self):
        self.tiles.clear()
        self.bytes = 0

    def frames(self, backend, store, job, attempt, binding, *, views, alpha, roi, grid,
               request_id, deadline=None, superseded=None):
        x0, y0, x1, y1 = roi
        width, height = x1-x0, y1-y0
        if not (0 <= x0 < x1 <= grid["width"] and 0 <= y0 < y1 <= grid["height"]
                and width*height <= self.MAX_VIEW_PIXELS):
            raise ValueError("局部查看区域过大或超出原图范围")
        deadline = time.monotonic()+30 if deadline is None else deadline

        def check():
            if superseded is not None and superseded():
                raise PreviewSuperseded()
            if time.monotonic() >= deadline:
                raise TimeoutError("局部预览超时，请稍后重试")

        token = json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False)
        descriptors, missing, loaded = [], [], {}
        for view in views:
            effective = alpha if view == "result" else 0.
            for top in range(y0//self.TILE*self.TILE, y1, self.TILE):
                for left in range(x0//self.TILE*self.TILE, x1, self.TILE):
                    tile = (left, top, min(left+self.TILE, grid["width"]), min(top+self.TILE, grid["height"]))
                    key = (str(store), job, attempt, token, view, effective, tile)
                    req = dict(view=view, alpha=effective, roi=tile,
                               request_id=f"{request_id}-{view}-{left}-{top}")
                    descriptors.append((key, req))
                    if key in self.tiles:
                        loaded[key] = self.tiles[key]
                        self.tiles.move_to_end(key)
                    else:
                        missing.append((key, req))
        # A very thin/tall viewport can expose a small part of many full tiles.
        # Keep live tile-frame payloads bounded; use the exact streaming compositor
        # for that geometry rather than retaining the entire overfetched image.
        if any(sum((r["roi"][2]-r["roi"][0])*(r["roi"][3]-r["roi"][1])*3
                   for _,r in descriptors if r["view"] == view) > self.MAX_FRAME_TILE_BYTES for view in views):
            loaded.clear()
            return [self._stream_region(backend,store,job,attempt,binding,view=view,
                alpha=alpha if view == "result" else 0.,roi=roi,grid=grid,
                request_id=request_id+"-"+view,deadline=deadline,superseded=superseded) for view in views]
        for start in range(0, len(missing), MAX_PREVIEW_BATCH_TILES):
            check()
            batch = missing[start:start+MAX_PREVIEW_BATCH_TILES]
            if hasattr(backend, "preview_many"):
                replies = backend.preview_many(store, job, attempt, binding,
                    [req for _, req in batch], superseded=superseded)
            else:
                replies = []
                for _, req in batch:
                    check()
                    replies.append(backend.preview(store, job, attempt, binding, **req))
            if len(replies) != len(batch):
                raise ValueError("局部预览块数量不一致")
            for (key, req), reply in zip(batch, replies):
                meta, raw = reply["preview"], reply["pixels"]
                left, top, right, bottom = req["roi"]
                shape = [bottom-top, right-left, 3]
                if (not isinstance(raw, bytes) or meta.get("request_id") != req["request_id"]
                        or meta.get("binding") != binding or meta.get("view_grid" if req["view"]=="reference" else "source_grid") != grid
                        or meta.get("roi") != list(req["roi"]) or meta.get("native_pixel_roi") is not True
                        or meta.get("shape") != shape or meta.get("alpha") != req["alpha"]
                        or len(raw) != shape[0]*shape[1]*3
                        or hashlib.sha256(raw).hexdigest() != meta.get("pixel_sha256")):
                    raise ValueError("局部预览身份或像素校验失败")
                item = (raw, dict(meta), {k: reply.get(k) for k in ("fit_count", "render_count")})
                loaded[key] = self.tiles[key] = item
                self.bytes += len(raw)
                while self.bytes > self.MAX_BYTES:
                    _, discarded = self.tiles.popitem(last=False)
                    self.bytes -= len(discarded[0])
        check()
        result = []
        for view in views:
            selected = [(key[-1], loaded[key]) for key, req in descriptors if req["view"] == view]
            first = selected[0][1][1]
            if any(any(meta.get(k) != first.get(k) for k in
                       ("icc_profile_base64", "color_interpretation", "source_grid", "view_grid", "coordinate_space"))
                   for _, (_, meta, _) in selected):
                raise ValueError("局部预览色彩或坐标不一致")
            manifest = [(list(tile), meta["pixel_sha256"]) for tile, (_, meta, _) in selected]
            metadata = {**first, "request_id":request_id+"-"+view, "roi":list(roi),
                "shape":[height, width, 3], "width":width, "height":height,
                "quantized_size":[width, height], "desktop_tiled_viewport":True,
                "tile_manifest_sha256":hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()}
            metadata.pop("pixel_sha256", None)
            result.append({"tiles":tuple((tile, raw, meta) for tile, (raw, meta, _) in selected),
                           "preview":metadata, **selected[-1][1][2]})
        return result

    def _stream_region(self, backend, store, job, attempt, binding, *, view, alpha,
                       roi, grid, request_id, deadline, superseded):
        x0,y0,x1,y1=roi;width,height=x1-x0,y1-y0
        pixels=bytearray(width*height*3);destination=memoryview(pixels)
        first=None;counts={}
        for top in range(y0//self.TILE*self.TILE,y1,self.TILE):
            for left in range(x0//self.TILE*self.TILE,x1,self.TILE):
                tile=(left,top,min(left+self.TILE,grid["width"]),min(top+self.TILE,grid["height"]))
                frame=self.frames(backend,store,job,attempt,binding,views=[view],alpha=alpha,
                    roi=tile,grid=grid,request_id=request_id,deadline=deadline,superseded=superseded)[0]
                _,raw,meta=frame["tiles"][0]
                if first is None:first=meta
                elif any(meta.get(k)!=first.get(k) for k in ("icc_profile_base64","color_interpretation","source_grid")):
                    raise ValueError("局部预览色彩或坐标不一致")
                counts={k:frame.get(k) for k in ("fit_count","render_count")}
                source=memoryview(raw);a,b=max(left,x0),min(tile[2],x1)
                for y in range(max(top,y0),min(tile[3],y1)):
                    start=((y-top)*(tile[2]-left)+a-left)*3;dest=((y-y0)*width+a-x0)*3
                    destination[dest:dest+(b-a)*3]=source[start:start+(b-a)*3]
        raw=bytes(pixels)
        return {"pixels":raw,"preview":{**first,"request_id":request_id,"roi":list(roi),
            "shape":[height,width,3],"width":width,"height":height,"quantized_size":[width,height],
            "pixel_sha256":hashlib.sha256(raw).hexdigest(),"desktop_streamed_viewport":True},**counts}

    def region(self, backend, store, job, attempt, binding, *, view, alpha, roi, grid,
               request_id, deadline=None, superseded=None):
        """Exact assembled compatibility path, also useful as a compositor oracle."""
        frame = self.frames(backend, store, job, attempt, binding, views=[view], alpha=alpha,
            roi=roi, grid=grid, request_id=request_id, deadline=deadline, superseded=superseded)[0]
        if "pixels" in frame:
            return {**frame,"preview":{**frame["preview"],"request_id":request_id}}
        x0, y0, x1, y1 = roi
        width, height = x1-x0, y1-y0
        pixels = bytearray(width*height*3)
        destination = memoryview(pixels)
        for tile, raw, _ in frame["tiles"]:
            left, top, right, bottom = tile
            source = memoryview(raw)
            tx0, tx1 = max(left,x0), min(right,x1)
            stride = (right-left)*3
            for y in range(max(top,y0), min(bottom,y1)):
                start = (y-top)*stride+(tx0-left)*3
                dest = ((y-y0)*width+tx0-x0)*3
                destination[dest:dest+(tx1-tx0)*3] = source[start:start+(tx1-tx0)*3]
        raw = bytes(pixels)
        metadata = {**frame["preview"], "request_id":request_id,
                    "pixel_sha256":hashlib.sha256(raw).hexdigest()}
        return {"pixels":raw, "preview":metadata,
                **{k:frame.get(k) for k in ("fit_count", "render_count")}}
