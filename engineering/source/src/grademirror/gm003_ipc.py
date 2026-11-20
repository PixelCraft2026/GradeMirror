"""Bounded local named-pipe framing for GM-003. Never transmits pickle objects."""

from __future__ import annotations

import json
import math
import threading
import time
from multiprocessing.connection import Client

MAX_FRAME = 4 * 1024 * 1024
MAX_JSON = 64 * 1024
from .image_limits import MAX_ARRAY_BYTES, MAX_PIXELS

MAX_ARRAY = MAX_ARRAY_BYTES
DTYPES = {"|u1": 1, "<u2": 2, "<f4": 4}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_IPC_KEY")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("NONFINITE_IPC_JSON: " + value)


def send_json(conn, value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    if len(payload) > MAX_JSON:
        raise ValueError("IPC_JSON_LIMIT")
    conn.send_bytes(payload)


def recv_json(conn):
    payload = conn.recv_bytes(maxlength=MAX_JSON)
    return json.loads(payload.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=_reject_constant)


def connect(address: str, auth_hex: str, timeout_s: float = 15):
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if not (isinstance(address, list) and len(address) == 2 and
                    address[0] == "127.0.0.1" and type(address[1]) is int and 0 < address[1] < 65536):
                raise ValueError("IPC_ADDRESS_INVALID")
            return Client(tuple(address), family="AF_INET", authkey=bytes.fromhex(auth_hex))
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)


def bounded_call(address, auth_hex, callback, *, deadline: float,
                 cancelled=None):
    """Bound the *whole* connect/auth/framing call by one monotonic deadline.

    A timed-out caller closes any acquired connection. Its owning operation
    must then stop its owned host Job; a blocked auth thread is never treated
    as a completed worker or allowed to authorize a later request.
    """
    finished = threading.Event()
    result = {}
    connection = [None]

    def run():
        try:
            remaining = max(.001, deadline - time.monotonic())
            with connect(address, auth_hex, timeout_s=remaining) as conn:
                connection[0] = conn
                result["value"] = callback(conn)
        except BaseException as exc:
            result["error"] = exc
        finally:
            finished.set()

    threading.Thread(target=run, name="grademirror-ipc", daemon=True).start()
    while not finished.wait(min(.02, max(.001, deadline - time.monotonic()))):
        if cancelled is not None and cancelled():
            if connection[0] is not None:
                connection[0].close()
            raise InterruptedError("IPC_CANCELLED")
        if time.monotonic() >= deadline:
            if connection[0] is not None:
                connection[0].close()
            raise TimeoutError("IPC_DEADLINE")
    if cancelled is not None and cancelled():
        raise InterruptedError("IPC_CANCELLED")
    if time.monotonic() >= deadline:
        raise TimeoutError("IPC_DEADLINE")
    if "error" in result:
        raise result["error"]
    return result["value"]


def _array_contract(dtype: str, shape: list[int], nbytes: int):
    if dtype not in DTYPES or not isinstance(shape, list) or len(shape) != 3 or shape[2] != 3:
        raise ValueError("IPC_ARRAY_CONTRACT")
    if any(type(x) is not int or x <= 0 for x in shape):
        raise ValueError("IPC_ARRAY_SHAPE")
    if shape[0] * shape[1] > MAX_PIXELS:
        raise ValueError("IPC_ARRAY_SHAPE")
    expected = math.prod(shape) * DTYPES[dtype]
    if type(nbytes) is not int or nbytes != expected or nbytes > MAX_ARRAY:
        raise ValueError("IPC_ARRAY_BYTES")


def send_array(conn, array):
    import numpy as np
    if not isinstance(array, np.ndarray) or not array.flags.c_contiguous:
        raise ValueError("IPC_ARRAY_CONTIGUOUS_REQUIRED")
    dtype, shape, nbytes = array.dtype.str, list(array.shape), int(array.nbytes)
    _array_contract(dtype, shape, nbytes)
    send_json(conn, {"kind": "array", "dtype": dtype, "shape": shape,
                     "nbytes": nbytes})
    view = memoryview(array).cast("B")
    for offset in range(0, nbytes, MAX_FRAME):
        end = min(nbytes, offset + MAX_FRAME)
        send_json(conn, {"offset": offset, "length": end - offset})
        conn.send_bytes(view[offset:end])
    send_json(conn, {"end": True})


def recv_array(conn, *, expected_dtype: str, expected_shape: tuple[int, int, int]):
    import numpy as np
    header = recv_json(conn)
    if not isinstance(header, dict) or set(header) != {"kind", "dtype", "shape", "nbytes"} or header["kind"] != "array":
        raise ValueError("IPC_ARRAY_HEADER")
    dtype, shape, nbytes = header["dtype"], header["shape"], header["nbytes"]
    _array_contract(dtype, shape, nbytes)
    if dtype != expected_dtype or tuple(shape) != expected_shape:
        raise ValueError("IPC_ARRAY_IDENTITY")
    array = np.empty(shape, dtype=np.dtype(dtype))
    target = array.view(np.uint8).reshape(-1)
    offset = 0
    while offset < nbytes:
        part = recv_json(conn)
        if not isinstance(part, dict) or set(part) != {"offset", "length"} or part["offset"] != offset:
            raise ValueError("IPC_ARRAY_OFFSET")
        length = part["length"]
        if type(length) is not int or length <= 0 or length > MAX_FRAME or offset + length > nbytes:
            raise ValueError("IPC_ARRAY_FRAME_LIMIT")
        data = conn.recv_bytes(maxlength=MAX_FRAME)
        if len(data) != length:
            raise ValueError("IPC_ARRAY_FRAME_LENGTH")
        target[offset:offset+length] = np.frombuffer(data, dtype=np.uint8)
        offset += length
    if recv_json(conn) != {"end": True}:
        raise ValueError("IPC_ARRAY_END")
    return array


def recv_rgb8_bytes(conn, *, max_pixels: int = 1024 * 1024):
    """Receive preview pixels without importing NumPy in the control process."""
    header = recv_json(conn)
    if not isinstance(header, dict) or set(header) != {"kind", "dtype", "shape", "nbytes"} or header["kind"] != "array":
        raise ValueError("IPC_ARRAY_HEADER")
    shape = header["shape"]
    _array_contract(header["dtype"], shape, header["nbytes"])
    if header["dtype"] != "|u1" or shape[0] * shape[1] > max_pixels:
        raise ValueError("IPC_PREVIEW_LIMIT")
    nbytes = header["nbytes"]
    data = bytearray(nbytes)
    offset = 0
    while offset < nbytes:
        part = recv_json(conn)
        if not isinstance(part, dict) or set(part) != {"offset", "length"} or part["offset"] != offset:
            raise ValueError("IPC_ARRAY_OFFSET")
        length = part["length"]
        if type(length) is not int or length <= 0 or length > MAX_FRAME or offset + length > nbytes:
            raise ValueError("IPC_ARRAY_FRAME_LIMIT")
        chunk = conn.recv_bytes(maxlength=MAX_FRAME)
        if len(chunk) != length:
            raise ValueError("IPC_ARRAY_FRAME_LENGTH")
        data[offset:offset + length] = chunk
        offset += length
    if recv_json(conn) != {"end": True}:
        raise ValueError("IPC_ARRAY_END")
    return bytes(data), tuple(shape)
