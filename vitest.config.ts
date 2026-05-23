import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['./apps/askchappy-ui/src/tests/setup.ts'],
    include: [
      'apps/askchappy-ui/src/tests/**/*.test.ts?(x)',
      'services/askchappy-api/src/tests/**/*.test.ts?(x)',
    ],
  },
});
