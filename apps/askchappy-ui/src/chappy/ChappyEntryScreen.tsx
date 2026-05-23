import React from 'react';
import { GUIDED_MODE_CARDS } from '../modes/guidedModes';

export const ChappyEntryScreen = ({ onStartOpenQa }: { onStartOpenQa: () => void }) => (
  <section aria-label="askchappy entry">
    <h1>AskChappy</h1>
    <p>Join Chappy for a local-first DDN virtual Partner Technical Manager working session.</p>
    <p>Use Open Q&amp;A now and explore guided mode scaffolding for local production planning.</p>
    <button type="button" onClick={onStartOpenQa}>
      Start Open Q&amp;A
    </button>

    <h2>Guided modes (local scaffold)</h2>
    <ul>
      {GUIDED_MODE_CARDS.map((card) => (
        <li key={card.mode}>
          <strong>{card.title}</strong>
          <span> — Planned local scaffold; mode switching is not active in Phase 5.</span>
        </li>
      ))}
    </ul>
  </section>
);
