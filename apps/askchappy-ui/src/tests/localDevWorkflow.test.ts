import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { RETIRED_ROUTES, ROUTES } from '../../../../shared/contracts/askchappy';
import { getVoiceProviderSelection } from '../../../../services/askchappy-api/src/voice/voiceProviderSelection';

const packageJsonPath = resolve(process.cwd(), 'package.json');
const indexHtmlPath = resolve(process.cwd(), 'index.html');

describe('phase 14 local-first runtime workflow wiring', () => {
  it('defines canonical local run scripts for start/dev workflow', () => {
    const packageJson = JSON.parse(readFileSync(packageJsonPath, 'utf8')) as { scripts?: Record<string, string> };

    expect(packageJson.scripts?.dev).toBe('vite --host 127.0.0.1 --port 4173');
    expect(packageJson.scripts?.start).toBe('npm run dev');
    expect(packageJson.scripts?.['build:local-runtime']).toBe('vite build');
    expect(packageJson.scripts?.['smoke:local-runtime']).toBe(
      'npm run build:local-runtime && node scripts/smoke-local-runtime.mjs',
    );
  });

  it('wires index.html to the AskChappy React bootstrap entry', () => {
    const indexHtml = readFileSync(indexHtmlPath, 'utf8');

    expect(indexHtml).toContain('/apps/askchappy-ui/src/main.tsx');
    expect(indexHtml).not.toContain('/dist-runtime/');
  });



  it('does not reference retired dist-runtime output or local-runtime-server script in package/docs', () => {
    const packageJson = readFileSync(packageJsonPath, 'utf8');
    const docs = [
      'docs/CURRENT_IMPLEMENTATION_STATUS.md',
      'docs/LOCAL_FIRST_RUN_GUIDE.md',
      'docs/DEPENDENCY_REVIEW.md',
    ]
      .map((docPath) => readFileSync(resolve(process.cwd(), docPath), 'utf8'))
      .join('\n');

    const combined = `${packageJson}\n${docs}`;

    expect(combined).not.toContain('dist-runtime');
    expect(combined).not.toContain('scripts/local-runtime-server.mjs');
  });

  it('keeps canonical route constants unchanged while retired routes remain inactive', () => {
    expect(ROUTES.chappy).toBe('/chappy');
    expect(ROUTES.chappySession).toBe('/chappy/session/:sessionId');
    expect(ROUTES.chappySummary).toBe('/chappy/summary/:sessionId');
    expect(RETIRED_ROUTES).toContain('/demo');
    expect(RETIRED_ROUTES).toContain('/visual-session/:sessionId');
  });

  it('keeps standard voice active/default when cloned provider adapter is not implemented', () => {
    const providerSelection = getVoiceProviderSelection({ clonedVoiceConfig: null });
    expect(providerSelection.standard_voice_active).toBe(true);
    expect(providerSelection.cloned_voice_ready).toBe(false);
    expect(providerSelection.selected_provider).toBe('standard');
  });
});
