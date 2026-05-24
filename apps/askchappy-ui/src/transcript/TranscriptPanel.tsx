import React from 'react';
import type { TranscriptMessage } from '../../../../shared/contracts/transcript';

const getSpeakerLabel = (role: TranscriptMessage['role']): string => {
  if (role === 'assistant') return 'vChappy';
  if (role === 'user') return 'You';
  return 'System';
};

export const TranscriptPanel = ({ messages }: { messages: TranscriptMessage[] }) => (
  <aside className="meeting-chat-panel" aria-label="transcript panel">
    <header className="transcript-header">
      <h3>Transcript</h3>
      {messages.length > 0 ? <span>{messages.length} messages</span> : null}
    </header>

    {messages.length === 0 ? (
      <div className="transcript-empty">
        <p>Ask Chappy anything. He’ll keep it conversational and go deeper when you ask.</p>
      </div>
    ) : (
      <ul className="transcript-list" aria-label="transcript message list">
        {messages.map((message) => (
          <li key={message.id} className={`transcript-row ${message.role}`}>
            <article className={`msg ${message.role}`}>
              <span className="msg-speaker">{getSpeakerLabel(message.role)}</span>
              <span className="msg-text">{message.text}</span>
            </article>
          </li>
        ))}
      </ul>
    )}
  </aside>
);
