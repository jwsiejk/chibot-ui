/**
 * Determine whether a value is a TypedArray (excluding DataView).
 * @param {*} value - Value to test.
 * @returns {boolean} True when the value is any TypedArray view.
 */
export function isTypedArray(value) {
  if (!value) return false;
  return ArrayBuffer.isView(value) && !(value instanceof DataView);
}

/**
 * Normalize binary-like inputs to an ArrayBuffer.
 * @param {*} value - ArrayBuffer or TypedArray to convert.
 * @returns {ArrayBuffer|null} The corresponding ArrayBuffer or null when invalid.
 */
export function toArrayBuffer(value) {
  if (!value) return null;

  if (value instanceof ArrayBuffer) {
    return value;
  }

  if (isTypedArray(value)) {
    const { buffer, byteOffset, byteLength } = value;

    if (byteOffset === 0 && byteLength === buffer.byteLength) {
      return buffer;
    }

    if (typeof buffer.slice === "function") {
      return buffer.slice(byteOffset, byteOffset + byteLength);
    }

    return buffer;
  }

  return null;
}
