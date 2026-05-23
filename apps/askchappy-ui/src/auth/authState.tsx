import React, { createContext, useContext, useMemo, useState } from 'react';
import { AuthRole, getRoleForEmail } from '../../../../shared/contracts/auth';

type AuthUser = {
  email: string;
  role: AuthRole;
};

type AuthContextValue = {
  user: AuthUser | null;
  login: (email: string) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser] = useState<AuthUser | null>(null);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      login: (email: string) => {
        const normalizedEmail = email.trim().toLowerCase();
        setUser({
          email: normalizedEmail,
          role: getRoleForEmail(normalizedEmail),
        });
      },
      logout: () => setUser(null),
    }),
    [user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return context;
};
