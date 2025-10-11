import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STUB_SPEC_PREFIX = '/static/js/ws.js';
const STUB_URL = pathToFileURL(path.resolve('./tests/js/ws_stub.mjs')).href;

export async function resolve(specifier, context, defaultResolve) {
  const prefixIndex = specifier.indexOf(STUB_SPEC_PREFIX);
  if (prefixIndex !== -1) {
    const suffix = specifier.slice(prefixIndex);
    const normalized = suffix.replace(/\?v=.*$/, '');
    if (normalized === STUB_SPEC_PREFIX) {
      return { url: STUB_URL, shortCircuit: true };
    }
  }
  return defaultResolve(specifier, context, defaultResolve);
}
