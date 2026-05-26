import React from 'react';
import type { SessionMode } from '../../../../shared/contracts/modes';
import type { CreatePresentationsGeneratedDeckHistoryItem, CreatePresentationsGeneratedPresentationState } from '../../../../shared/contracts/createPresentationsMode';
import { MODE_DEFINITIONS, MODE_LOOKUP } from '../modes/guidedModes';
import { GeneratedDeckHistoryList } from './GeneratedDeckHistoryList';

export const SessionRightRail = ({
  activeMode,
  onSelectMode,
  compact = false,
  generatedPresentation,
  generatedDeckHistory,
}: {
  activeMode: SessionMode;
  onSelectMode: (mode: SessionMode) => void;
  compact?: boolean;
  generatedPresentation?: CreatePresentationsGeneratedPresentationState;
  generatedDeckHistory?: CreatePresentationsGeneratedDeckHistoryItem[];
}) => (
  <aside className={`card panel guided-modes-panel${compact ? ' compact' : ''}`} aria-label="session right rail">
    {generatedPresentation ? (<section aria-label="presentation export status">
      <h4>Presentation Export</h4>
      {generatedPresentation.status === 'generated' ? (<><p>Status: Ready</p><p>Type: PPTX</p>{generatedPresentation.file_name ? <p>File: {generatedPresentation.file_name}</p> : null}{generatedPresentation.download_url ? <a className="btn secondary" href={generatedPresentation.download_url} download={generatedPresentation.file_name ?? true}>Download PowerPoint</a> : null}</>) : null}
      {generatedPresentation.status === 'generating' ? <p>Status: Generating PPTX…</p> : null}
      {generatedPresentation.status === 'error' ? <p role="alert">Export failed: {generatedPresentation.error_message ?? 'Unknown error.'}</p> : null}
    </section>) : null}
    <GeneratedDeckHistoryList generatedDeckHistory={generatedDeckHistory} />
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
