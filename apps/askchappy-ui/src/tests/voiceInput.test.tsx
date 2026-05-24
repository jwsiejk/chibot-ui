import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VoiceInput } from '../session/VoiceInput';

describe('voice input browser microphone ui', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders start speaking and switches to stop recording while recording', async () => {
    const onTranscribe = vi.fn().mockResolvedValue(undefined);
    const onError = vi.fn();

    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
      configurable: true,
    });

    class MockMediaRecorder {
      static isTypeSupported = vi.fn().mockReturnValue(true);
      ondataavailable: ((event: { data: BlobPart }) => void) | null = null;
      onstop: (() => void | Promise<void>) | null = null;
      start = vi.fn();
      stop = vi.fn(() => {
        this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) });
        void this.onstop?.();
      });
    }

    vi.stubGlobal('MediaRecorder', MockMediaRecorder as unknown as typeof MediaRecorder);

    render(<VoiceInput onStart={vi.fn()} onStop={vi.fn()} onTranscribe={onTranscribe} onError={onError} />);

    fireEvent.click(screen.getByRole('button', { name: 'Start speaking' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop speaking' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Stop speaking' }));
    await waitFor(() => expect(onTranscribe).toHaveBeenCalledTimes(1));
    expect(onError).not.toHaveBeenCalled();
  });

  it('shows clear error when microphone permission is denied/unavailable', async () => {
    const onError = vi.fn();

    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    });

    render(<VoiceInput onStart={vi.fn()} onStop={vi.fn()} onTranscribe={vi.fn()} onError={onError} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start speaking' }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('Permission denied');
    });
  });
});
