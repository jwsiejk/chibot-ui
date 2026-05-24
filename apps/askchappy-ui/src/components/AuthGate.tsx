import React, { FormEvent, useState } from 'react';
import { MVP_ADMIN_EMAIL } from '../../../../shared/contracts/auth';
import { useAuth } from '../auth/authState';

export const AuthGate = ({ children }: { children: React.ReactNode }) => {
  const { user, login } = useAuth();
  const [email, setEmail] = useState('');
  const [touched, setTouched] = useState(false);

  if (user) return <>{children}</>;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = email.trim();
    setTouched(true);
    if (!trimmed) return;
    login(trimmed);
  };

  const showError = touched && !email.trim();

  return (
    <main className="auth-overlay" aria-label="askchappy login">
      <section className="card auth-card" aria-label="local-first login modal">
        <p className="state-pill">AskChappy Lobby</p>
        <h1>Welcome to AskChappy</h1>
        <p>Enter your email to join a local-first DDN vPTM working room.</p>
        <form onSubmit={onSubmit} aria-label="email login form">
          <label htmlFor="email">Email</label>
          <input className="input" id="email" name="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} onBlur={() => setTouched(true)} required aria-invalid={showError} />
          {showError ? <p role="alert">Email is required to continue.</p> : null}
          <button className="btn" type="submit">Continue</button>
        </form>
        <p><small>Local MVP admin helper: {MVP_ADMIN_EMAIL}</small></p>
      </section>
    </main>
  );
};
