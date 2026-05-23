import React from 'react';
import { VOICE_PROFILE_STATES, type VoiceProfileState } from '../../../../../shared/contracts/voice';

const FUTURE_WORKFLOW_STEPS = [
  'Record or upload voice samples',
  'Create draft profile',
  'Test generated speech',
  'Approve profile',
  'Publish global voice',
  'Disable cloned profile (standard voice remains active)',
] as const;

const CLONED_VOICE_READINESS_STATES = [
  'Not configured',
  'Missing provider config',
  'Consent required',
  'Published profile required',
  'Ready for provider adapter',
] as const;

const formatState = (state: VoiceProfileState) => state.replace('_', ' ');

export const VoiceStudioPage = () => (
  <main>
    <h1>Voice Studio shell (admin only)</h1>
    <p>This local-first Voice Studio shell governs optional cloned Chappy voice readiness for AskChappy local production.</p>

    <section aria-label="voice profile lifecycle">
      <h2>Voice profile lifecycle</h2>
      <ul>
        {VOICE_PROFILE_STATES.map((state) => (
          <li key={state}>{formatState(state)}</li>
        ))}
      </ul>
    </section>

    <section aria-label="current voice status">
      <h2>Current voice status</h2>
      <p>Standard voice active (default).</p>
      <p>Optional cloned Chappy voice is not required for AskChappy runtime.</p>
      <p>Cloned voice status: Cloned voice not configured.</p>
      <h3>Cloned Chappy voice readiness</h3>
      <ul>
        {CLONED_VOICE_READINESS_STATES.map((status) => (
          <li key={status}>{status}</li>
        ))}
      </ul>
      <p>Cloned Chappy voice is only considered active after readiness checks pass and a provider adapter is wired.</p>
      <p>Chapman voice use must be approved before publishing a cloned voice profile.</p>
    </section>

    <section aria-label="future voice workflow controls">
      <h2>Future workflow controls (inactive in this phase)</h2>
      {FUTURE_WORKFLOW_STEPS.map((step) => (
        <button key={step} type="button" disabled aria-disabled="true" title="Future phase; inactive in Phase 13">
          {step}
        </button>
      ))}
      <p>These controls are intentionally inert and do not persist data in this phase.</p>
    </section>
  </main>
);
