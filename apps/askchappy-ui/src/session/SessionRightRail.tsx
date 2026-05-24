import React from 'react';
import type { SessionMode } from '../../../../shared/contracts/modes';
import { MODE_DEFINITIONS, MODE_LOOKUP } from '../modes/guidedModes';

export const SessionRightRail = ({
  activeMode,
  onSelectMode,
}: {
  activeMode: SessionMode;
  onSelectMode: (mode: SessionMode) => void;
}) => (
  <aside className="card panel" aria-label="session right rail">
    <h3>Current mode</h3>
    <p>{MODE_LOOKUP[activeMode].title}</p>
    <p>{MODE_LOOKUP[activeMode].guidance}</p>
    <h4>Guided modes</h4>
    <ul>
      {MODE_DEFINITIONS.map((card) => (
        <li key={card.mode}>
          <button className="btn secondary" type="button" onClick={() => onSelectMode(card.mode)} aria-pressed={card.mode === activeMode}>
            {card.title}
          </button>
        </li>
      ))}
    </ul>
  </aside>
);
