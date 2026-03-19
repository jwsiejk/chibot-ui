import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0b1020',
        panel: '#121a30',
        accent: '#78e6ff',
        accentMuted: '#1f7a8c',
      },
      boxShadow: {
        panel: '0 20px 45px rgba(15, 23, 42, 0.35)',
      },
    },
  },
  plugins: [],
} satisfies Config;
