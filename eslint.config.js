import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/build/**',
      '**/coverage/**',
      '.venv/**',
      '**/.venv/**',
      'apps/askchip-ui/dist/**',
      'services/askchip-api/.venv/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: [
      'apps/askchappy-ui/**/*.{ts,tsx}',
      'services/askchappy-api/**/*.ts',
      'shared/contracts/**/*.ts',
      '*.config.{js,ts}',
    ],
    languageOptions: {
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      'no-undef': 'off',
    },
  }
);
