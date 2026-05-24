import React from 'react';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';

export const TranscriptPanel = ({ messages }: { messages: TranscriptMessage[] }) => (
  <section className="card panel" aria-label="transcript panel">
    <h3>Transcript</h3>
    {messages.length === 0 ? (
      <p>Ask Chappy anything to begin this session.</p>
    ) : (
      <ul className="transcript-list">
        {messages.map((message) => (
          <li key={message.id} className={`msg ${message.role}`}>
            <b>{message.role}:</b> {message.text}
          </li>
        ))}
      </ul>
    )}
  </section>
);
