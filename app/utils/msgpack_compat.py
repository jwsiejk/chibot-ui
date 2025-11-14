from __future__ import annotations

import struct
from typing import Any, Iterable

__all__ = ["packb", "unpackb"]


def _ensure_bytes(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, memoryview):
        return data.tobytes()
    raise TypeError("Expected binary-compatible type")


def _encode(value: Any, out: bytearray, *, use_bin_type: bool) -> None:
    if value is None:
        out.append(0xc0)
        return
    if value is True:
        out.append(0xc3)
        return
    if value is False:
        out.append(0xc2)
        return
    if isinstance(value, int):
        if 0 <= value <= 0x7f:
            out.append(value)
            return
        if -32 <= value < 0:
            out.append(0xe0 | (value + 32))
            return
        if 0 <= value <= 0xff:
            out.extend((0xcc, value))
            return
        if -0x80 <= value < 0:
            out.extend((0xd0, value & 0xff))
            return
        if 0 <= value <= 0xffff:
            out.append(0xcd)
            out.extend(struct.pack(">H", value))
            return
        if -0x8000 <= value < 0:
            out.append(0xd1)
            out.extend(struct.pack(">H", value & 0xffff))
            return
        if 0 <= value <= 0xffffffff:
            out.append(0xce)
            out.extend(struct.pack(">I", value))
            return
        if -0x80000000 <= value < 0:
            out.append(0xd2)
            out.extend(struct.pack(">I", value & 0xffffffff))
            return
        if value >= 0:
            out.append(0xcf)
            out.extend(struct.pack(">Q", value))
            return
        out.append(0xd3)
        out.extend(struct.pack(">q", value))
        return
    if isinstance(value, float):
        out.append(0xcb)
        out.extend(struct.pack(">d", value))
        return
    if isinstance(value, str):
        data = value.encode("utf-8")
        length = len(data)
        if length <= 31:
            out.append(0xa0 | length)
        elif length <= 0xff:
            out.extend((0xd9, length))
        elif length <= 0xffff:
            out.append(0xda)
            out.extend(struct.pack(">H", length))
        else:
            out.append(0xdb)
            out.extend(struct.pack(">I", length))
        out.extend(data)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        if not use_bin_type:
            string_value = value.decode("utf-8")
            _encode(string_value, out, use_bin_type=use_bin_type)
            return
        data = _ensure_bytes(value)
        length = len(data)
        if length <= 0xff:
            out.extend((0xc4, length))
        elif length <= 0xffff:
            out.append(0xc5)
            out.extend(struct.pack(">H", length))
        else:
            out.append(0xc6)
            out.extend(struct.pack(">I", length))
        out.extend(data)
        return
    if isinstance(value, (list, tuple)):
        length = len(value)
        if length <= 15:
            out.append(0x90 | length)
        elif length <= 0xffff:
            out.append(0xdc)
            out.extend(struct.pack(">H", length))
        else:
            out.append(0xdd)
            out.extend(struct.pack(">I", length))
        for item in value:
            _encode(item, out, use_bin_type=use_bin_type)
        return
    if isinstance(value, dict):
        items: Iterable[tuple[Any, Any]] = value.items()
        length = len(value)
        if length <= 15:
            out.append(0x80 | length)
        elif length <= 0xffff:
            out.append(0xde)
            out.extend(struct.pack(">H", length))
        else:
            out.append(0xdf)
            out.extend(struct.pack(">I", length))
        for key, item in items:
            _encode(str(key), out, use_bin_type=use_bin_type)
            _encode(item, out, use_bin_type=use_bin_type)
        return
    raise TypeError(f"Unsupported type for msgpack: {type(value)!r}")


def packb(value: Any, *, use_bin_type: bool = False) -> bytes:
    out = bytearray()
    _encode(value, out, use_bin_type=use_bin_type)
    return bytes(out)


def _read_uint(data: memoryview, offset: int, length: int) -> tuple[int, int]:
    end = offset + length
    if end > len(data):
        raise ValueError("Unexpected end of msgpack payload")
    return int.from_bytes(data[offset:end], "big"), end


def _read_int(data: memoryview, offset: int, length: int) -> tuple[int, int]:
    end = offset + length
    if end > len(data):
        raise ValueError("Unexpected end of msgpack payload")
    chunk = data[offset:end].tobytes()
    if length == 1:
        return struct.unpack(">b", chunk)[0], end
    if length == 2:
        return struct.unpack(">h", chunk)[0], end
    if length == 4:
        return struct.unpack(">i", chunk)[0], end
    if length == 8:
        return struct.unpack(">q", chunk)[0], end
    raise ValueError("Unsupported integer length")


def _read_float(data: memoryview, offset: int, length: int) -> tuple[float, int]:
    end = offset + length
    if end > len(data):
        raise ValueError("Unexpected end of msgpack payload")
    chunk = data[offset:end].tobytes()
    if length == 4:
        return struct.unpack(">f", chunk)[0], end
    if length == 8:
        return struct.unpack(">d", chunk)[0], end
    raise ValueError("Unsupported float length")


def unpackb(data: bytes | bytearray | memoryview, *, raw: bool = False) -> Any:
    view = memoryview(data)

    def decode(offset: int = 0) -> tuple[Any, int]:
        if offset >= len(view):
            raise ValueError("Unexpected end of msgpack payload")
        prefix = view[offset]
        offset += 1
        if prefix <= 0x7f:
            return prefix, offset
        if prefix >= 0xe0:
            return prefix - 0x100, offset
        if 0x80 <= prefix <= 0x8f:
            size = prefix & 0x0f
            result: dict[str, Any] = {}
            for _ in range(size):
                key, offset = decode(offset)
                value, offset = decode(offset)
                result[str(key)] = value
            return result, offset
        if 0x90 <= prefix <= 0x9f:
            size = prefix & 0x0f
            items = []
            for _ in range(size):
                item, offset = decode(offset)
                items.append(item)
            return items, offset
        if 0xa0 <= prefix <= 0xbf:
            length = prefix & 0x1f
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk.decode("utf-8"), offset
        if prefix == 0xc0:
            return None, offset
        if prefix == 0xc2:
            return False, offset
        if prefix == 0xc3:
            return True, offset
        if prefix == 0xc4:
            length, offset = _read_uint(view, offset, 1)
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk, offset
        if prefix == 0xc5:
            length, offset = _read_uint(view, offset, 2)
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk, offset
        if prefix == 0xc6:
            length, offset = _read_uint(view, offset, 4)
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk, offset
        if prefix == 0xca:
            value, offset = _read_float(view, offset, 4)
            return value, offset
        if prefix == 0xcb:
            value, offset = _read_float(view, offset, 8)
            return value, offset
        if prefix == 0xcc:
            value, offset = _read_uint(view, offset, 1)
            return value, offset
        if prefix == 0xcd:
            value, offset = _read_uint(view, offset, 2)
            return value, offset
        if prefix == 0xce:
            value, offset = _read_uint(view, offset, 4)
            return value, offset
        if prefix == 0xcf:
            value, offset = _read_uint(view, offset, 8)
            return value, offset
        if prefix == 0xd0:
            value, offset = _read_int(view, offset, 1)
            return value, offset
        if prefix == 0xd1:
            value, offset = _read_int(view, offset, 2)
            return value, offset
        if prefix == 0xd2:
            value, offset = _read_int(view, offset, 4)
            return value, offset
        if prefix == 0xd3:
            value, offset = _read_int(view, offset, 8)
            return value, offset
        if prefix == 0xd9:
            length, offset = _read_uint(view, offset, 1)
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk.decode("utf-8"), offset
        if prefix == 0xda:
            length, offset = _read_uint(view, offset, 2)
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk.decode("utf-8"), offset
        if prefix == 0xdb:
            length, offset = _read_uint(view, offset, 4)
            end = offset + length
            if end > len(view):
                raise ValueError("Unexpected end of msgpack payload")
            chunk = view[offset:end].tobytes()
            offset = end
            return chunk if raw else chunk.decode("utf-8"), offset
        if prefix == 0xdc:
            length, offset = _read_uint(view, offset, 2)
            items = []
            for _ in range(length):
                item, offset = decode(offset)
                items.append(item)
            return items, offset
        if prefix == 0xdd:
            length, offset = _read_uint(view, offset, 4)
            items = []
            for _ in range(length):
                item, offset = decode(offset)
                items.append(item)
            return items, offset
        if prefix == 0xde:
            length, offset = _read_uint(view, offset, 2)
            result: dict[str, Any] = {}
            for _ in range(length):
                key, offset = decode(offset)
                value, offset = decode(offset)
                result[str(key)] = value
            return result, offset
        if prefix == 0xdf:
            length, offset = _read_uint(view, offset, 4)
            result: dict[str, Any] = {}
            for _ in range(length):
                key, offset = decode(offset)
                value, offset = decode(offset)
                result[str(key)] = value
            return result, offset
        raise ValueError(f"Unsupported msgpack prefix: 0x{prefix:02x}")

    value, offset = decode(0)
    if offset != len(view):
        raise ValueError("Trailing bytes after msgpack payload")
    return value
