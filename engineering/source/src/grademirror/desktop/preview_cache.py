"""Small exact RGB8 reply cache; never approximates or resamples pixels."""
from collections import OrderedDict
import copy
import hashlib
import threading

from ..common import canonical_json


class ExactPreviewCache:
    MAX_BYTES = 32 * 1024**2

    def __init__(self):
        self._items = OrderedDict()
        self.bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def key(store, job, attempt, binding, view, alpha, side):
        return (str(store), job, attempt, canonical_json(binding),
                view, alpha if view == "result" else 0., side, "Q8_then_Pillow_LANCZOS-v1")

    def clear(self):
        with self._lock:
            self._items.clear()
            self.bytes = 0

    def get(self, key, request_id):
        with self._lock:
            reply = self._items.pop(key, None)
            if reply is None:
                return None
            self._items[key] = reply
            result = {**reply, "preview": copy.deepcopy(reply["preview"])}
        result["preview"]["request_id"] = request_id
        return result

    def put(self, key, reply, request_id, binding, alpha):
        meta, raw = reply["preview"], reply["pixels"]
        shape = meta.get("shape")
        if (not isinstance(raw, bytes) or not isinstance(shape, list) or len(shape) != 3
                or any(type(n) is not int or n <= 0 for n in shape) or shape[2] != 3
                or len(raw) != shape[0]*shape[1]*3 or meta.get("roi") is not None
                or meta.get("request_id") != request_id or meta.get("binding") != binding
                or meta.get("alpha") != alpha
                or hashlib.sha256(raw).hexdigest() != meta.get("pixel_sha256")):
            return
        if len(raw) > self.MAX_BYTES:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self.bytes -= len(old["pixels"])
            self._items[key] = {**reply, "preview": copy.deepcopy(meta)}
            self.bytes += len(raw)
            while self.bytes > self.MAX_BYTES:
                _, discarded = self._items.popitem(last=False)
                self.bytes -= len(discarded["pixels"])
