export const MVP_ADMIN_EMAIL = 'jsiejk@ddn.com';

export const AUTH_ROLES = ['admin', 'standard_user'] as const;
export type AuthRole = (typeof AUTH_ROLES)[number];

export const isAuthRole = (value: unknown): value is AuthRole =>
  typeof value === 'string' && AUTH_ROLES.includes(value as AuthRole);

export const assertAuthRole = (value: unknown): asserts value is AuthRole => {
  if (!isAuthRole(value)) {
    throw new Error(`Invalid auth role: ${String(value)}`);
  }
};

export const getRoleForEmail = (email: string): AuthRole =>
  email.trim().toLowerCase() === MVP_ADMIN_EMAIL ? 'admin' : 'standard_user';
