import { defineConfig, loadEnv } from 'vite';
import { tryHandlePresentationDownloadRoute } from './services/askchappy-api/src/api/presentationDownloadRoute';

const LOCAL_RUNTIME_ENV_KEYS = [
  'OLLAMA_BASE_URL',
  'OLLAMA_MODEL',
  'OLLAMA_KEEP_ALIVE',
  'OLLAMA_NUM_CTX',
  'KOKORO_TTS_BASE_URL',
  'KOKORO_TTS_VOICE',
  'KOKORO_TTS_FORMAT',
  'KOKORO_TTS_TIMEOUT_MS',
  'FASTER_WHISPER_BASE_URL',
  'FASTER_WHISPER_MODEL',
  'FASTER_WHISPER_LANGUAGE',
  'FASTER_WHISPER_TIMEOUT_MS',
] as const;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  const defineProcessEnv = LOCAL_RUNTIME_ENV_KEYS.reduce<Record<string, string>>((acc, key) => {
    acc[`process.env.${key}`] = JSON.stringify(env[key] ?? '');
    return acc;
  }, {});

  return {
    plugins: [
      {
        name: 'askchappy-presentation-download-route',
        configureServer(server) {
          server.middlewares.use(async (req, res, next) => {
            const handled = await tryHandlePresentationDownloadRoute(req, res);
            if (!handled) next();
          });
        },
        configurePreviewServer(server) {
          server.middlewares.use(async (req, res, next) => {
            const handled = await tryHandlePresentationDownloadRoute(req, res);
            if (!handled) next();
          });
        },
      },
    ],
    define: defineProcessEnv,
    server: {
      host: '127.0.0.1',
      port: 4173,
    },
    preview: {
      host: '127.0.0.1',
      port: 4173,
    },
  };
});
