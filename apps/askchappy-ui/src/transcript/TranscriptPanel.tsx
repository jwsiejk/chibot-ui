import React from 'react';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';

export const TranscriptPanel = ({ messages }: { messages: TranscriptMessage[] }) => (
  <aside className="card panel meeting-chat-panel" aria-label="transcript panel">
    <h3>Transcript</h3>
    {messages.length === 0 ? (
      <p>Ask Chappy anything. He’ll keep it conversational and go deeper when you ask.</p>
    ) : (
      <ul className="transcript-list" aria-label="transcript message list">
        {messages.map((message) => (
          <li key={message.id} className={`msg ${message.role}`}>
            <b>{message.role}:</b> {message.text}
          </li>
        ))}
      </ul>
    )}
  </aside>
);
