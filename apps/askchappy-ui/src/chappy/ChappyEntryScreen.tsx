import React from 'react';
import { GUIDED_MODE_CARDS } from '../modes/guidedModes';

export const ChappyEntryScreen = ({ onStartOpenQa }: { onStartOpenQa: () => void }) => (
  <section className="card entry-hero" aria-label="askchappy pre-call lobby">
    <p className="state-pill">Pre-call lobby</p>
    <h1>AskChappy Room</h1>
    <p>Join Chappy in a focused DDN vPTM working session. Local-first runtime, voice-first conversation.</p>
    <section className="card panel room-preview" aria-label="chappy room preview">
      <p>Room preview</p>
      <h2>Chappy • Open Q&amp;A</h2>
      <p>Your transcript and meeting controls stay available during the session.</p>
      <button className="btn" type="button" onClick={onStartOpenQa}>Join Chappy Room</button>
    </section>

    <h3>Guided overlays</h3>
    <div className="guided-grid" aria-label="guided mode chips">
      {GUIDED_MODE_CARDS.map((card) => (
        <article className="mode-chip" key={card.mode}>
          <strong>{card.title}</strong>
        </article>
      ))}
    </div>
  </section>
);
