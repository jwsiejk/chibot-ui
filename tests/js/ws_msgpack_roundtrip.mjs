import assert from 'node:assert/strict';
import { encodeMessagePack, decodeMessagePack, toUint8Array } from '../../app/static/js/utils/msgpack.mjs';

function roundTrip(value) {
  const encoded = encodeMessagePack(value);
  assert.ok(encoded instanceof Uint8Array, 'encode should return Uint8Array');
  const decoded = decodeMessagePack(encoded);
  assert.deepStrictEqual(decoded, value);
}

roundTrip(null);
roundTrip(true);
roundTrip(false);
roundTrip(0);
roundTrip(127);
roundTrip(-33);
roundTrip(255);
roundTrip(65535);
roundTrip(-32768);
roundTrip(3.141592653589793);
roundTrip('hello');
roundTrip(['alpha', 1, { nested: 'beta' }]);
roundTrip({ type: 'ping', t: 123, meta: { ok: true } });

const binary = new Uint8Array([1, 2, 3, 4, 5]);
const encodedBinary = encodeMessagePack({ payload: binary });
const decodedBinary = decodeMessagePack(encodedBinary);
assert.ok(decodedBinary.payload instanceof Uint8Array, 'decoded binary should be Uint8Array');
assert.deepStrictEqual(Array.from(decodedBinary.payload), Array.from(binary));

const bufferRoundtrip = decodeMessagePack(toUint8Array(encodeMessagePack({ k: 'v' })));
assert.deepStrictEqual(bufferRoundtrip, { k: 'v' });

console.log(JSON.stringify({ ok: true }));
