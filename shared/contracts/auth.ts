export const MVP_ADMIN_EMAIL = 'jsiejk@ddn.com';
export const AUTH_ROLES = ['admin', 'standard_user'] as const;
export type AuthRole = (typeof AUTH_ROLES)[number];
