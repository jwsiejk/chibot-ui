import type { TranscriptMessage } from '../types/contract';
import { MessageRow } from './MessageRow';

export function MessageGroup({ messages }: { messages: TranscriptMessage[] }) {
  return (
    <div className="space-y-3">
      {messages.map((message) => (
        <MessageRow key={message.id} message={message} />
      ))}
    </div>
  );
}
