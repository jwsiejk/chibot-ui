import React from 'react';
import { VOICE_PROFILE_STATES, type VoiceProfileState } from '../../../../../shared/contracts/voice';

const FUTURE_WORKFLOW_STEPS = [
  'Record or upload voice samples',
  'Create draft profile',
  'Test generated speech',
  'Approve profile',
  'Publish global voice',
  'Disable or revert to fallback',
] as const;

const formatState = (state: VoiceProfileState) => state.replace('_', ' ');

export const VoiceStudioPage = () => (
  <main>
    <h1>Voice Studio shell (admin only)</h1>
    <p>This local-first Voice Studio shell governs future voice lifecycle workflows for AskChappy.</p>

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
      <p>No published Chappy voice profile.</p>
      <p>Fallback voice path is active.</p>
      <p>Real voice cloning is not implemented in Phase 8.</p>
      <p>Chapman voice use must be approved before publishing a cloned voice profile.</p>
    </section>

    <section aria-label="future voice workflow controls">
      <h2>Future workflow controls (inactive in this phase)</h2>
      {FUTURE_WORKFLOW_STEPS.map((step) => (
        <button key={step} type="button" disabled aria-disabled="true" title="Future phase; inactive in Phase 8">
          {step}
        </button>
      ))}
      <p>These controls are intentionally inert and do not persist data in this phase.</p>
    </section>
  </main>
);
