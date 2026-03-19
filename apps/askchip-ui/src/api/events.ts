import { runtimeConfig } from '../config/runtime';
import type { AskChipEvent } from '../types/contract';
import { createConnectionFinalizer } from './connectionFinalizer';

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
    const url = new URL('/ws/events', runtimeConfig.wsBaseUrl);
    if (options.sessionId) {
      url.searchParams.set('session_id', options.sessionId);
    }

    this.socket?.close();
    const socket = new WebSocket(url);
    this.socket = socket;

    let lastFailure: 'error' | 'close' | null = null;
    const finalize = createConnectionFinalizer({
      isCurrentSocket: () => this.socket === socket,
      clearCurrentSocket: () => {
        this.socket = null;
      },
      onError: options.onError,
      onClose: options.onClose,
    });

    socket.addEventListener('open', () => {
      lastFailure = null;
      options.onOpen();
    });
    socket.addEventListener('message', (messageEvent) => {
      const payload = JSON.parse(messageEvent.data) as AskChipEvent;
      options.onMessage(payload);
    });
    socket.addEventListener('error', () => {
      lastFailure = 'error';
      finalize('error');
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close();
      }
    });
    socket.addEventListener('close', () => finalize(lastFailure ?? 'close'));

    return () => {
      if (this.socket === socket) {
        this.socket = null;
      }
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close();
      }
    };
  }
}

export const askChipEventsClient = new AskChipEventsClient();
