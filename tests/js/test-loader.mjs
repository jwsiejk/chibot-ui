import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STUB_SPEC = '/static/js/ws.js?v=v20250911b';
const STUB_URL = pathToFileURL(path.resolve('./tests/js/ws_stub.mjs')).href;

export async function resolve(specifier, context, defaultResolve) {
  if (specifier === STUB_SPEC) {
    return { url: STUB_URL, shortCircuit: true };
  }
  return defaultResolve(specifier, context, defaultResolve);
}
