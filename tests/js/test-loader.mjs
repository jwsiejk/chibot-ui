import path from 'node:path';
import { pathToFileURL } from 'node:url';

const STUB_SPEC_PREFIX = '/static/js/ws.js';
const STUB_URL = pathToFileURL(path.resolve('./tests/js/ws_stub.mjs')).href;

export async function resolve(specifier, context, defaultResolve) {
  const normalized = specifier.replace(/\?v=.*$/, '');
  if (normalized === STUB_SPEC_PREFIX) {
    return { url: STUB_URL, shortCircuit: true };
  }
  if (normalized.startsWith('/static/js/')) {
    const withoutQuery = normalized;
    const filePath = path.resolve('.', withoutQuery.slice('/'.length));
    return { url: pathToFileURL(filePath).href, shortCircuit: true };
  }
  return defaultResolve(specifier, context, defaultResolve);
}
