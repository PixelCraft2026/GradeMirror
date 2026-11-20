"""Private successful semantic proofs for one explicit validation boundary.

No payload, file identity shortcut, global state, or business-state cache.
"""
import hashlib
import math
import sys
from pathlib import Path


class ValidationScope:
    __slots__ = ('_proofs', '_bytes', '_active')
    MAX_ITEMS = 64
    MAX_BYTES = 256 * 1024

    def __init__(self):
        self._proofs = set()
        self._bytes = 0
        self._active = False

    def __enter__(self):
        if self._active:
            raise RuntimeError('Validation scope cannot be re-entered')
        self._active = True
        return self

    def __exit__(self, *exc):
        self._proofs.clear()
        self._bytes = 0
        self._active = False

    def validate_p3(self, path, shape, expected_archive, expected_values, *,
                    profile_key, limits, reader, hash_file, fail):
        if not self._active:
            raise RuntimeError('Validation scope is closed')
        p = Path(path)
        if (not isinstance(shape, list) or len(shape) != 2
                or any(type(n) is not int for n in shape) or shape[1] != 3
                or not 1 <= shape[0] <= limits['K']):
            fail('P3 coefficient shape quota')
        raw_bytes = math.prod(shape) * 8
        cap = min(limits['archive_bytes'], raw_bytes + math.ceil(raw_bytes / 1000) + 65536)
        # Every use, including hits, reads the entire current file.
        if p.stat().st_size > cap:
            fail('P3 archive quota or hash differs')
        actual_archive = hash_file(p)
        if actual_archive != expected_archive:
            fail('P3 archive quota or hash differs')
        key = ('gm011-p3-proof-v1', *profile_key, actual_archive, expected_archive,
               expected_values, '<f8', *shape, 'b.npy', 'NPY-v1-C-order',
               limits['K'], limits['archive_bytes'], cap, raw_bytes + 2058, 2048)
        if key in self._proofs:
            return
        payload = reader(p, shape, expected_archive)
        if hashlib.sha256(payload).hexdigest() != expected_values:
            fail('P3 values digest differs')
        # Bind the successful parse to the digest just read, not caller belief.
        if hash_file(p) != expected_archive:
            fail('P3 archive quota or hash differs')
        size = sys.getsizeof(key) + sum(sys.getsizeof(v) for v in key)
        if (len(self._proofs) < self.MAX_ITEMS
                and self._bytes + size + sys.getsizeof(self._proofs) + 4096 <= self.MAX_BYTES):
            self._proofs.add(key)
            self._bytes += size
