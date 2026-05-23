import React, { FormEvent, useState } from 'react';
import { MVP_ADMIN_EMAIL } from '../../../../shared/contracts/auth';
import { useAuth } from '../auth/authState';

export const AuthGate = ({ children }: { children: React.ReactNode }) => {
  const { user, login } = useAuth();
  const [email, setEmail] = useState('');

  if (user) {
    return <>{children}</>;
  }

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!email.trim()) {
      return;
    }
    login(email);
  };

  return (
    <main>
      <h1>AskChappy entry placeholder</h1>
      <p>Local-first MVP login. Enter email to continue.</p>
      <form onSubmit={onSubmit} aria-label="email login form">
        <label htmlFor="email">Email</label>
        <input
          id="email"
          name="email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
        <button type="submit">Continue</button>
      </form>
      <p>Admin local MVP email: {MVP_ADMIN_EMAIL}</p>
    </main>
  );
};
