export const ROUTES = {
  home: '/',
  chappy: '/chappy',
  chappySession: '/chappy/session/:sessionId',
  chappySummary: '/chappy/summary/:sessionId',
  dev: '/dev',
  admin: '/admin',
  adminVoice: '/admin/voice',
  adminAvatar: '/admin/avatar',
} as const;

export const RETIRED_ROUTES = [
  '/demo',
  '/demo/intake',
  '/demo/recommendation',
  '/visual-session/:sessionId',
  '/demo/summary/:sessionId',
] as const;
