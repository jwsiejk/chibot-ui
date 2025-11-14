const TEXT_ENCODER = new TextEncoder();
const TEXT_DECODER = new TextDecoder();

function toUint8Array(value) {
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new TypeError("Expected ArrayBuffer or typed array");
}

function writeBinaryHeader(bytes, length) {
  if (length <= 0xff) {
    bytes.push(0xc4, length);
    return;
  }
  if (length <= 0xffff) {
    bytes.push(0xc5, (length >> 8) & 0xff, length & 0xff);
    return;
  }
  bytes.push(
    0xc6,
    (length >>> 24) & 0xff,
    (length >>> 16) & 0xff,
    (length >>> 8) & 0xff,
    length & 0xff,
  );
}

function writeUint16(bytes, value) {
  bytes.push((value >> 8) & 0xff, value & 0xff);
}

function writeUint32(bytes, value) {
  bytes.push(
    (value >>> 24) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 8) & 0xff,
    value & 0xff,
  );
}

function writeUint64(bytes, value) {
  let bigint;
  if (typeof value === "bigint") {
    bigint = value;
  } else if (typeof value === "number" && Number.isFinite(value)) {
    if (!Number.isInteger(value)) {
      throw new TypeError("Cannot encode fractional number as uint64");
    }
    if (value < 0) {
      throw new TypeError("Cannot encode negative number as uint64");
    }
    bigint = BigInt(value);
  } else {
    throw new TypeError("Unsupported uint64 value");
  }
  for (let shift = 56n; shift >= 0n; shift -= 8n) {
    const byte = Number((bigint >> shift) & 0xffn);
    bytes.push(byte);
  }
}

function writeInt64(bytes, value) {
  let bigint;
  if (typeof value === "bigint") {
    bigint = value;
  } else if (typeof value === "number" && Number.isFinite(value)) {
    if (!Number.isInteger(value)) {
      throw new TypeError("Cannot encode fractional number as int64");
    }
    bigint = BigInt(value);
  } else {
    throw new TypeError("Unsupported int64 value");
  }
  const mask = 0xffn;
  for (let shift = 56n; shift >= 0n; shift -= 8n) {
    const byte = Number((bigint >> shift) & mask);
    bytes.push(byte);
  }
}

function writeFloat64(bytes, value) {
  const buffer = new ArrayBuffer(8);
  new DataView(buffer).setFloat64(0, value);
  const view = new Uint8Array(buffer);
  for (let i = 0; i < view.length; i += 1) {
    bytes.push(view[i]);
  }
}

function encodeMessagePack(value) {
  const bytes = [];

  const writeBuffer = (buffer) => {
    const view = toUint8Array(buffer);
    for (let i = 0; i < view.length; i += 1) {
      bytes.push(view[i]);
    }
  };

  const encodeValue = (val) => {
    if (val === null || typeof val === "undefined") {
      bytes.push(0xc0);
      return;
    }
    if (typeof val === "boolean") {
      bytes.push(val ? 0xc3 : 0xc2);
      return;
    }
    if (typeof val === "number") {
      if (Number.isInteger(val)) {
        if (val >= 0) {
          if (val <= 0x7f) {
            bytes.push(val);
            return;
          }
          if (val <= 0xff) {
            bytes.push(0xcc, val);
            return;
          }
          if (val <= 0xffff) {
            bytes.push(0xcd);
            writeUint16(bytes, val);
            return;
          }
          if (val <= 0xffffffff) {
            bytes.push(0xce);
            writeUint32(bytes, val);
            return;
          }
          bytes.push(0xcf);
          writeUint64(bytes, val);
          return;
        }
        if (val >= -32) {
          bytes.push(0xe0 | (val + 32));
          return;
        }
        if (val >= -128) {
          bytes.push(0xd0, (val + 0x100) & 0xff);
          return;
        }
        if (val >= -32768) {
          bytes.push(0xd1);
          writeUint16(bytes, (val + 0x10000) & 0xffff);
          return;
        }
        if (val >= -2147483648) {
          bytes.push(0xd2);
          writeUint32(bytes, (val + 0x100000000) >>> 0);
          return;
        }
        bytes.push(0xd3);
        writeInt64(bytes, val);
        return;
      }
      bytes.push(0xcb);
      writeFloat64(bytes, val);
      return;
    }
    if (typeof val === "bigint") {
      if (val >= 0n) {
        if (val <= 0x7fn) {
          bytes.push(Number(val));
          return;
        }
        if (val <= 0xffn) {
          bytes.push(0xcc, Number(val));
          return;
        }
        if (val <= 0xffffn) {
          bytes.push(0xcd);
          writeUint16(bytes, Number(val));
          return;
        }
        if (val <= 0xffffffffn) {
          bytes.push(0xce);
          writeUint32(bytes, Number(val));
          return;
        }
        bytes.push(0xcf);
        writeUint64(bytes, val);
        return;
      }
      if (val >= -32n) {
        bytes.push(0xe0 | Number(val + 32n));
        return;
      }
      if (val >= -128n) {
        bytes.push(0xd0, Number((val + 0x100n) & 0xffn));
        return;
      }
      if (val >= -32768n) {
        bytes.push(0xd1);
        writeUint16(bytes, Number((val + 0x10000n) & 0xffffn));
        return;
      }
      if (val >= -2147483648n) {
        bytes.push(0xd2);
        writeUint32(bytes, Number((val + 0x100000000n) & 0xffffffffn));
        return;
      }
      bytes.push(0xd3);
      writeInt64(bytes, val);
      return;
    }
    if (typeof val === "string") {
      const utf8 = TEXT_ENCODER.encode(val);
      const len = utf8.length;
      if (len <= 31) {
        bytes.push(0xa0 | len);
      } else if (len <= 0xff) {
        bytes.push(0xd9, len);
      } else if (len <= 0xffff) {
        bytes.push(0xda);
        writeUint16(bytes, len);
      } else {
        bytes.push(0xdb);
        writeUint32(bytes, len);
      }
      writeBuffer(utf8);
      return;
    }
    if (Array.isArray(val)) {
      const len = val.length;
      if (len <= 15) {
        bytes.push(0x90 | len);
      } else if (len <= 0xffff) {
        bytes.push(0xdc);
        writeUint16(bytes, len);
      } else {
        bytes.push(0xdd);
        writeUint32(bytes, len);
      }
      for (let i = 0; i < len; i += 1) {
        encodeValue(val[i]);
      }
      return;
    }
    if (ArrayBuffer.isView(val) || val instanceof ArrayBuffer) {
      const view = toUint8Array(val);
      writeBinaryHeader(bytes, view.byteLength);
      writeBuffer(view);
      return;
    }
    if (typeof val === "object") {
      const entries = Object.entries(val);
      const len = entries.length;
      if (len <= 15) {
        bytes.push(0x80 | len);
      } else if (len <= 0xffff) {
        bytes.push(0xde);
        writeUint16(bytes, len);
      } else {
        bytes.push(0xdf);
        writeUint32(bytes, len);
      }
      for (const [key, value] of entries) {
        encodeValue(String(key));
        encodeValue(value);
      }
      return;
    }
    throw new TypeError(`Unsupported msgpack type: ${typeof val}`);
  };

  encodeValue(value);
  return Uint8Array.from(bytes);
}

function ensureAvailable(view, offset, length) {
  if (offset + length > view.byteLength) {
    throw new Error("Truncated msgpack buffer");
  }
}

function decodeMessagePack(buffer) {
  const viewSource = toUint8Array(buffer);
  const view = new DataView(viewSource.buffer, viewSource.byteOffset, viewSource.byteLength);
  let offset = 0;

  const readUint8 = () => {
    ensureAvailable(view, offset, 1);
    const value = view.getUint8(offset);
    offset += 1;
    return value;
  };

  const read = (length) => {
    ensureAvailable(view, offset, length);
    const slice = new Uint8Array(view.buffer, view.byteOffset + offset, length);
    offset += length;
    return slice;
  };

  const readUint16 = () => {
    ensureAvailable(view, offset, 2);
    const value = view.getUint16(offset);
    offset += 2;
    return value;
  };

  const readUint32 = () => {
    ensureAvailable(view, offset, 4);
    const value = view.getUint32(offset);
    offset += 4;
    return value;
  };

  const readUint64 = () => {
    ensureAvailable(view, offset, 8);
    const value = view.getBigUint64(offset);
    offset += 8;
    if (value <= BigInt(Number.MAX_SAFE_INTEGER)) {
      return Number(value);
    }
    return value;
  };

  const readInt8 = () => {
    ensureAvailable(view, offset, 1);
    const value = view.getInt8(offset);
    offset += 1;
    return value;
  };

  const readInt16 = () => {
    ensureAvailable(view, offset, 2);
    const value = view.getInt16(offset);
    offset += 2;
    return value;
  };

  const readInt32 = () => {
    ensureAvailable(view, offset, 4);
    const value = view.getInt32(offset);
    offset += 4;
    return value;
  };

  const readInt64 = () => {
    ensureAvailable(view, offset, 8);
    const value = view.getBigInt64(offset);
    offset += 8;
    if (value >= BigInt(Number.MIN_SAFE_INTEGER) && value <= BigInt(Number.MAX_SAFE_INTEGER)) {
      return Number(value);
    }
    return value;
  };

  const readFloat32 = () => {
    ensureAvailable(view, offset, 4);
    const value = view.getFloat32(offset);
    offset += 4;
    return value;
  };

  const readFloat64 = () => {
    ensureAvailable(view, offset, 8);
    const value = view.getFloat64(offset);
    offset += 8;
    return value;
  };

  const decodeValue = () => {
    const prefix = readUint8();
    if (prefix <= 0x7f) {
      return prefix;
    }
    if (prefix >= 0xe0) {
      return prefix - 0x100;
    }
    if ((prefix & 0xf0) === 0x80) {
      const size = prefix & 0x0f;
      const obj = {};
      for (let i = 0; i < size; i += 1) {
        const key = decodeValue();
        obj[String(key)] = decodeValue();
      }
      return obj;
    }
    if ((prefix & 0xf0) === 0x90) {
      const size = prefix & 0x0f;
      const arr = new Array(size);
      for (let i = 0; i < size; i += 1) {
        arr[i] = decodeValue();
      }
      return arr;
    }
    if ((prefix & 0xe0) === 0xa0) {
      const length = prefix & 0x1f;
      const slice = read(length);
      return TEXT_DECODER.decode(slice);
    }
    switch (prefix) {
      case 0xc0:
        return null;
      case 0xc2:
        return false;
      case 0xc3:
        return true;
      case 0xc4: {
        const length = readUint8();
        return new Uint8Array(read(length));
      }
      case 0xc5: {
        const length = readUint16();
        return new Uint8Array(read(length));
      }
      case 0xc6: {
        const length = readUint32();
        return new Uint8Array(read(length));
      }
      case 0xc7:
      case 0xc8:
      case 0xc9:
        throw new Error(`Unsupported msgpack ext format 0x${prefix.toString(16)}`);
      case 0xca:
        return readFloat32();
      case 0xcb:
        return readFloat64();
      case 0xcc:
        return readUint8();
      case 0xcd:
        return readUint16();
      case 0xce:
        return readUint32();
      case 0xcf:
        return readUint64();
      case 0xd0:
        return readInt8();
      case 0xd1:
        return readInt16();
      case 0xd2:
        return readInt32();
      case 0xd3:
        return readInt64();
      case 0xd4:
      case 0xd5:
      case 0xd6:
      case 0xd7:
      case 0xd8:
        throw new Error(`Unsupported msgpack fixed ext format 0x${prefix.toString(16)}`);
      case 0xd9: {
        const length = readUint8();
        const slice = read(length);
        return TEXT_DECODER.decode(slice);
      }
      case 0xda: {
        const length = readUint16();
        const slice = read(length);
        return TEXT_DECODER.decode(slice);
      }
      case 0xdb: {
        const length = readUint32();
        const slice = read(length);
        return TEXT_DECODER.decode(slice);
      }
      case 0xdc: {
        const size = readUint16();
        const arr = new Array(size);
        for (let i = 0; i < size; i += 1) {
          arr[i] = decodeValue();
        }
        return arr;
      }
      case 0xdd: {
        const size = readUint32();
        const arr = new Array(size);
        for (let i = 0; i < size; i += 1) {
          arr[i] = decodeValue();
        }
        return arr;
      }
      case 0xde: {
        const size = readUint16();
        const obj = {};
        for (let i = 0; i < size; i += 1) {
          const key = decodeValue();
          obj[String(key)] = decodeValue();
        }
        return obj;
      }
      case 0xdf: {
        const size = readUint32();
        const obj = {};
        for (let i = 0; i < size; i += 1) {
          const key = decodeValue();
          obj[String(key)] = decodeValue();
        }
        return obj;
      }
      default:
        throw new Error(`Unsupported msgpack prefix 0x${prefix.toString(16)}`);
    }
  };

  const result = decodeValue();
  if (offset !== view.byteLength) {
    const remaining = view.byteLength - offset;
    if (remaining > 0) {
      throw new Error("Trailing bytes after msgpack payload");
    }
  }
  return result;
}

export { encodeMessagePack, decodeMessagePack, toUint8Array };
