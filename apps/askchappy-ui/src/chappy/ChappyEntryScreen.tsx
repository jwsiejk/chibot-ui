import React from 'react';
import { GUIDED_MODE_CARDS } from '../modes/guidedModes';

export const ChappyEntryScreen = ({ onStartOpenQa }: { onStartOpenQa: () => void }) => (
  <section className="entry-lobby" aria-label="askchappy pre-call lobby">
    <section className="card room-preview" aria-label="chappy room preview">
      <p className="state-pill">Pre-call lobby</p>
      <h1>AskChappy Room</h1>
      <p>You are about to enter a live Chappy room. Chappy is ready for Open Q&amp;A.</p>
      <div className="chappy-video-tile lobby-tile" aria-label="chappy lobby tile">
        <div className="chappy-avatar-placeholder" aria-hidden="true">C</div>
        <h2>Chappy</h2>
        <span className="state-dot">Ready</span>
      </div>
      <button className="btn" type="button" onClick={onStartOpenQa}>Join Chappy Room</button>
    </section>
    <section className="card panel guided-compact" aria-label="guided modes compact panel">
      <h3>Guided modes</h3>
      <p>Optional overlays once in-room.</p>
      <div className="guided-chips" aria-label="guided mode chips">
        {GUIDED_MODE_CARDS.map((card) => (
          <article className="mode-chip" key={card.mode}>
            <strong>{card.title}</strong>
          </article>
        ))}
      </div>
    </section>
  </section>
);
