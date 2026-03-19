import type { AskChipEvent } from '../types/contract';

const DEFAULT_WS_BASE = 'ws://127.0.0.1:8000';

export type ConnectionState = 'connecting' | 'connected' | 'disconnected';

export class AskChipEventsClient {
  private socket: WebSocket | null = null;

  connect(options: {
    sessionId: string | null;
    onOpen: () => void;
    onMessage: (event: AskChipEvent) => void;
    onClose: () => void;
    onError: () => void;
  }): () => void {
    const url = new URL('/ws/events', DEFAULT_WS_BASE);
    if (options.sessionId) {
      url.searchParams.set('session_id', options.sessionId);
    }

    this.socket?.close();
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.addEventListener('open', () => options.onOpen());
    socket.addEventListener('message', (messageEvent) => {
      const payload = JSON.parse(messageEvent.data) as AskChipEvent;
      options.onMessage(payload);
    });
    socket.addEventListener('close', () => {
      if (this.socket === socket) {
        this.socket = null;
      }
      options.onClose();
    });
    socket.addEventListener('error', () => options.onError());

    return () => {
      if (this.socket === socket) {
        this.socket = null;
      }
      socket.close();
    };
  }
}

export const askChipEventsClient = new AskChipEventsClient();
