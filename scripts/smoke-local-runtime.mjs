/* global process, console */
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = process.cwd();
const distIndexPath = resolve(root, 'dist', 'index.html');

if (!existsSync(distIndexPath)) {
  throw new Error('Local runtime smoke failed: dist/index.html is missing. Run npm run build:local-runtime first.');
}

const distIndex = readFileSync(distIndexPath, 'utf8');
if (!distIndex.includes('<div id="root"></div>')) {
  throw new Error('Local runtime smoke failed: dist/index.html is missing #root app shell.');
}

const hashedAssetMatches = distIndex.match(/\/assets\/[^"]+\.(?:js|css)/g) ?? [];
if (hashedAssetMatches.length === 0) {
  throw new Error('Local runtime smoke failed: dist/index.html does not reference built JS/CSS assets.');
}

const hasBuiltScript = hashedAssetMatches.some((assetPath) => assetPath.endsWith('.js'));
if (!hasBuiltScript) {
  throw new Error('Local runtime smoke failed: dist/index.html does not reference a built JavaScript entry asset.');
}

console.log('Local runtime smoke passed. dist/index.html and built assets are wired for AskChappy app shell.');
