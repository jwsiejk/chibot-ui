import React from 'react';
import { GUIDED_MODE_CARDS } from '../modes/guidedModes';

export const SessionRightRail = () => (
  <aside aria-label="session right rail">
    <h3>Current mode</h3>
    <p>Open Q&amp;A</p>
    <h4>Guided modes (future local scaffold)</h4>
    <ul>
      {GUIDED_MODE_CARDS.map((card) => (
        <li key={card.mode}>{card.title}</li>
      ))}
    </ul>
  </aside>
);
