import React from 'react';
import { AuthGate } from '../components/AuthGate';
import { AppNav } from '../components/AppNav';

export const ChappyEntry = () => (
  <AuthGate>
    <main>
      <h1>AskChappy entry placeholder</h1>
      <AppNav />
      <p>Welcome to AskChappy MVP/demo entry.</p>
    </main>
  </AuthGate>
);
