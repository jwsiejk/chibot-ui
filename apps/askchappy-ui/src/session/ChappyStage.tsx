import React from 'react';
import type { SessionState } from '../../../../shared/contracts/session';

export const ChappyStage = ({ state }: { state: SessionState }) => (
  <section aria-label="chappy stage">
    <h2>Chappy stage placeholder</h2>
    <p>Local production scaffold only — no real avatar, voice, or model runtime.</p>
    <p>State: {state}</p>
  </section>
);
