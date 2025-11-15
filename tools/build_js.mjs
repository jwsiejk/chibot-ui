import { build } from 'esbuild';
import { promises as fs } from 'fs';
import path from 'path';
import url from 'url';

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..');
const entryPoint = path.join('app', 'static', 'js', 'main_entry.js');
const outDir = path.join('app', 'static', 'dist');
const manifestPath = path.join(outDir, 'manifest.json');

async function ensureOutDir() {
  await fs.mkdir(outDir, { recursive: true });
}

async function clearOutDir() {
  const entries = await fs.readdir(outDir, { withFileTypes: true }).catch((err) => {
    if (err && err.code === 'ENOENT') {
      return [];
    }
    throw err;
  });
  await Promise.all(
    entries
      .map((entry) => {
        const targetPath = path.join(outDir, entry.name);
        if (entry.isDirectory()) {
          return fs.rm(targetPath, { recursive: true, force: true });
        }
        if (entry.isFile()) {
          return fs.unlink(targetPath).catch((err) => {
            if (err && err.code === 'ENOENT') {
              return;
            }
            throw err;
          });
        }
        return Promise.resolve();
      }),
  );
}

async function writeManifest(mainFile) {
  const manifest = {
    main_js: mainFile,
  };
  const contents = `${JSON.stringify(manifest, null, 2)}\n`;
  await fs.writeFile(manifestPath, contents, 'utf8');
}

async function main() {
  await ensureOutDir();
  await clearOutDir();

  const result = await build({
    absWorkingDir: repoRoot,
    entryPoints: [entryPoint],
    bundle: true,
    format: 'esm',
    splitting: true,
    sourcemap: true,
    minify: true,
    target: ['es2018'],
    outdir: outDir,
    entryNames: 'main.[hash]',
    chunkNames: 'chunk.[hash]',
    assetNames: 'asset.[hash]',
    metafile: true,
    logLevel: 'info',
  });

  const outputs = result.metafile?.outputs ?? {};
  const entryOutput = Object.entries(outputs).find(([, info]) => {
    if (!info.entryPoint) {
      return false;
    }
    const normalized = path.normalize(info.entryPoint);
    const expected = path.normalize(entryPoint);
    return normalized === expected;
  });
  if (!entryOutput) {
    throw new Error('Failed to locate entry bundle in esbuild outputs');
  }
  const [outputPath, info] = entryOutput;
  if (!info.entryPoint) {
    throw new Error('Unexpected esbuild metadata structure for main entry');
  }
  const mainFile = path.basename(outputPath);
  await writeManifest(mainFile);
  console.log(`Wrote manifest pointing to ${mainFile}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
