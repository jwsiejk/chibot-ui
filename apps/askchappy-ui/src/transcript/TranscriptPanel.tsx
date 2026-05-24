import React from 'react';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';

export const TranscriptPanel = ({ messages }: { messages: TranscriptMessage[] }) => (
  <section aria-label="transcript panel">
    <h3>Transcript</h3>
    {messages.length === 0 ? (
      <p>Ask Chappy anything. Type a question or use voice to start your session.</p>
    ) : (
      <ul>
        {messages.map((message) => (
          <li key={message.id}>
            <b>{message.role}:</b> {message.text}
          </li>
        ))}
      </ul>
    )}
  </section>
);
