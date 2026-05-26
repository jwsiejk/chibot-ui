import React from 'react';
import type { CreatePresentationsGeneratedDeckHistoryItem } from '../../../../shared/contracts/createPresentationsMode';

const formatGeneratedAt = (value?: string) => {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString();
};

export const GeneratedDeckHistoryList = ({
  generatedDeckHistory,
}: {
  generatedDeckHistory?: CreatePresentationsGeneratedDeckHistoryItem[];
}) => {
  if (!generatedDeckHistory?.length) {
    return null;
  }

  return (
    <section aria-label="generated decks history">
      <h4>Generated decks</h4>
      <ul>
        {generatedDeckHistory.map((deck) => (
          <li key={deck.id}>
            <p>{deck.title ?? deck.file_name}</p>
            {deck.title ? <p>File: {deck.file_name}</p> : null}
            <p>Format: {deck.format.toUpperCase()}</p>
            {deck.theme_id ? <p>Theme: {deck.theme_id}</p> : null}
            {formatGeneratedAt(deck.generated_at) ? <p>Generated: {formatGeneratedAt(deck.generated_at) as string}</p> : null}
            <a className="btn secondary" href={deck.download_url} download={deck.file_name}>Download</a>
          </li>
        ))}
      </ul>
    </section>
  );
};
