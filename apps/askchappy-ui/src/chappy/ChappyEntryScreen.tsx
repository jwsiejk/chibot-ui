import React from 'react';
import { GUIDED_MODE_CARDS } from '../modes/guidedModes';

export const ChappyEntryScreen = ({ onStartOpenQa }: { onStartOpenQa: () => void }) => (
  <section className="card entry-hero" aria-label="askchappy entry">
    <h1>AskChappy</h1>
    <p>Partner enablement with Chappy, your DDN virtual Partner Technical Manager.</p>
    <button className="btn" type="button" onClick={onStartOpenQa}>Start Open Q&amp;A</button>

    <h2>Guided modes</h2>
    <div className="guided-grid" aria-label="guided mode cards">
      {GUIDED_MODE_CARDS.map((card) => (
        <article className="mode-chip" key={card.mode}>
          <strong>{card.title}</strong>
          <p>{card.guidance}</p>
        </article>
      ))}
    </div>
  </section>
);
