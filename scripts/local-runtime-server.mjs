/* global process, console */
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const html = readFileSync(resolve(process.cwd(), 'docs/LOCAL_FIRST_RUN_GUIDE.md'), 'utf8');
const host = '127.0.0.1';
const port = 4173;

createServer((_req, res) => {
  res.statusCode = 200;
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.end(
    'AskChappy local-first runtime wiring is present. Run npm test for route/runtime verification.\n\n' +
      'See docs/LOCAL_FIRST_RUN_GUIDE.md for current local-first start workflow:\n\n' +
      html,
  );
}).listen(port, host, () => {
  console.log(`AskChappy local-first runtime verification endpoint: http://${host}:${port}/chappy`);
});
