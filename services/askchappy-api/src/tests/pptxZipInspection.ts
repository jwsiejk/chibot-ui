import { inflateRawSync } from 'node:zlib';

type ZipEntry = { name: string; data: Buffer };

const EOCD_SIG = 0x06054b50;
const CENTRAL_SIG = 0x02014b50;
const LOCAL_SIG = 0x04034b50;

const findEocdOffset = (buf: Buffer) => {
  const min = Math.max(0, buf.length - 65557);
  for (let i = buf.length - 22; i >= min; i -= 1) {
    if (buf.readUInt32LE(i) === EOCD_SIG) return i;
  }
  throw new Error('Invalid ZIP: end of central directory not found.');
};

export const inspectPptxZip = (buf: Buffer) => {
  if (buf.subarray(0, 2).toString('utf8') !== 'PK') throw new Error('Invalid ZIP header.');

  const eocd = findEocdOffset(buf);
  const centralDirOffset = buf.readUInt32LE(eocd + 16);
  const entryCount = buf.readUInt16LE(eocd + 10);

  const entries: ZipEntry[] = [];
  let cdPtr = centralDirOffset;
  for (let i = 0; i < entryCount; i += 1) {
    if (buf.readUInt32LE(cdPtr) !== CENTRAL_SIG) throw new Error('Invalid central directory entry.');
    const compression = buf.readUInt16LE(cdPtr + 10);
    const compressedSize = buf.readUInt32LE(cdPtr + 20);
    const fileNameLength = buf.readUInt16LE(cdPtr + 28);
    const extraLength = buf.readUInt16LE(cdPtr + 30);
    const commentLength = buf.readUInt16LE(cdPtr + 32);
    const localHeaderOffset = buf.readUInt32LE(cdPtr + 42);
    const name = buf.subarray(cdPtr + 46, cdPtr + 46 + fileNameLength).toString('utf8');

    if (buf.readUInt32LE(localHeaderOffset) !== LOCAL_SIG) throw new Error('Invalid local ZIP header.');
    const localNameLength = buf.readUInt16LE(localHeaderOffset + 26);
    const localExtraLength = buf.readUInt16LE(localHeaderOffset + 28);
    const dataStart = localHeaderOffset + 30 + localNameLength + localExtraLength;
    const compressed = buf.subarray(dataStart, dataStart + compressedSize);

    let data: Buffer;
    if (compression === 0) data = compressed;
    else if (compression === 8) data = inflateRawSync(compressed);
    else throw new Error(`Unsupported ZIP compression method: ${compression}`);

    entries.push({ name, data });
    cdPtr += 46 + fileNameLength + extraLength + commentLength;
  }

  return {
    entries,
    getEntryText: (name: string) => entries.find((entry) => entry.name === name)?.data.toString('utf8') ?? null,
  };
};
