import React from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';
import { LocalGpuValidationPanel } from './LocalGpuValidationPanel';

export const AdminDashboardPage = () => (
  <main>
    <h1>AskChappy local-first admin dashboard</h1>
    <p>Use this local production governance shell to manage Voice Studio and avatar readiness for AskChappy.</p>

    <section aria-label="local production status">
      <h2>Current local production status</h2>
      <p>Voice status: standard voice active/default; cloned Chappy voice not configured yet.</p>
      <p>Avatar status: placeholder avatar active; no real avatar asset yet.</p>
    </section>

    <section aria-label="admin tools">
      <h2>Admin tools</h2>
      <article>
        <h3>Voice Studio</h3>
        <p>Review voice profile lifecycle and future publish controls for local-only governance.</p>
        <Link to={ROUTES.adminVoice}>Open Voice Studio</Link>
      </article>
      <article>
        <h3>Avatar review</h3>
        <p>Review avatar placeholder readiness and future avatar states for local MVP rollout.</p>
        <Link to={ROUTES.adminAvatar}>Open Avatar review</Link>
      </article>
    </section>

    <LocalGpuValidationPanel />
  </main>
);
