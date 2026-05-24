import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { App } from '../app/App';
import * as serverApi from '../../../../services/askchappy-api/src/api/server';
import { getLocalSession } from '../../../../services/askchappy-api/src/api/server';
import { RETIRED_ROUTES, ROUTES } from '../../../../shared/contracts/askchappy';
import { MVP_ADMIN_EMAIL } from '../../../../shared/contracts/auth';
import { VOICE_PROFILE_STATES } from '../../../../shared/contracts/voice';
import { routeMap } from '../routes/routeMap';

describe('route map', () => {
  it('contains all active askchappy routes', () => {
    expect(routeMap).toEqual([
      ROUTES.home,
      ROUTES.chappy,
      ROUTES.chappySession,
      ROUTES.chappySummary,
      ROUTES.dev,
      ROUTES.admin,
      ROUTES.adminVoice,
      ROUTES.adminAvatar,
    ]);
  });

  it('does not include retired routes as active UX routes', () => {
    for (const route of RETIRED_ROUTES) {
      expect(routeMap).not.toContain(route);
    }
  });
});

describe('phase 22 chappy UI', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, 'Audio').mockImplementation(() => ({
      play: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn(),
      currentTime: 0,
    } as unknown as HTMLAudioElement));
  });

  it('renders email login gate on /chappy', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByText('Enter your email to join a local-first DDN vPTM working room.')).toBeInTheDocument();
  });

  it('renders /chappy entry screen after login', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.getByRole('heading', { name: 'AskChappy Room' })).toBeInTheDocument();
    expect(screen.queryByText(/Phase 5|scaffold/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Join Chappy Room' })).toBeInTheDocument();
  });

  it('shows admin nav links for admin login', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.getByRole('link', { name: 'Admin' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Voice Studio' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Avatar' })).toBeInTheDocument();
  });

  it('hides admin nav links for standard users', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Voice Studio' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Avatar' })).not.toBeInTheDocument();
  });

  it.each([ROUTES.admin, ROUTES.adminVoice, ROUTES.adminAvatar] as const)(
    'blocks standard user direct access to %s with not authorized',
    (path) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>,
      );

      expect(screen.getByText('Not authorized')).toBeInTheDocument();
    },
  );

  it.each([
    ['Admin', 'AskChappy local-first admin dashboard'],
    ['Voice Studio', 'Voice Studio shell (admin only)'],
    ['Avatar', 'Avatar setup and review shell (admin only)'],
  ] as const)('allows admin navigation access to %s', (linkName, heading) => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: linkName }));

    expect(screen.getByText(heading)).toBeInTheDocument();
  });



  it('shows local gpu validation panel for admin dashboard only', async () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Admin' }));

    expect(screen.getByRole('heading', { name: 'Local GPU Validation' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText(/Status: /).length).toBeGreaterThan(0));
  });

  it('renders local-first admin dashboard shell with voice and avatar links', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Admin' }));

    expect(screen.getByRole('heading', { name: 'AskChappy local-first admin dashboard' })).toBeInTheDocument();
    expect(screen.getByText('Voice status: standard voice active/default; cloned Chappy voice not configured yet.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Voice Studio' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Avatar review' })).toBeInTheDocument();
  });

  it('renders voice studio shell using shared voice lifecycle states with inactive controls', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Voice Studio' }));

    expect(screen.getByRole('heading', { name: 'Voice Studio shell (admin only)' })).toBeInTheDocument();
    for (const lifecycleState of VOICE_PROFILE_STATES) {
      expect(screen.getByText(lifecycleState)).toBeInTheDocument();
    }
    expect(screen.getByText('Standard voice active (default).')).toBeInTheDocument();
    expect(screen.getByText('Optional cloned Chappy voice is not required for AskChappy runtime.')).toBeInTheDocument();
    expect(screen.getByText('Cloned voice status: Cloned voice not configured.')).toBeInTheDocument();
    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.getByText('Missing provider config')).toBeInTheDocument();
    expect(screen.getByText('Consent required')).toBeInTheDocument();
    expect(screen.getByText('Published profile required')).toBeInTheDocument();
    expect(screen.getByText('Ready for provider adapter')).toBeInTheDocument();

    const futureControlsSection = screen.getByRole('region', { name: 'future voice workflow controls' });
    const disabledControls = within(futureControlsSection).getAllByRole('button', {
      name: /Record or upload voice samples|Create draft profile|Test generated speech|Approve profile|Publish global voice|Disable cloned profile \(standard voice remains active\)/,
    });
    expect(disabledControls).toHaveLength(6);
    disabledControls.forEach((control) => expect(control).toBeDisabled());
  });




  it('renders GPU validation from services array and manual guidance in admin modal', async () => {
    vi.spyOn(serverApi, 'getLocalGpuValidationReport').mockResolvedValue({
      generated_at: '2026-05-24T00:00:00.000Z',
      services: [
        { service: 'ollama', status: 'gpu_confirmed', reason: 'Ollama on CUDA', suggested_commands: ['nvidia-smi -l 1'] },
        { service: 'faster_whisper', status: 'cpu_only', reason: 'Running on CPU', suggested_commands: ['nvidia-smi -l 1'] },
        { service: 'kokoro_onnx', status: 'unknown', reason: 'Provider omitted', suggested_commands: ['nvidia-smi -l 1'] },
      ],
      manual_guidance: ['Use nvidia-smi -l 1', 'Trigger each service and watch utilization'],
    });

    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await waitFor(() => {
      expect(screen.getByText('Ollama GPU: gpu_confirmed — Ollama on CUDA')).toBeInTheDocument();
      expect(screen.getByText('faster-whisper GPU: cpu_only — Running on CPU')).toBeInTheDocument();
      expect(screen.getByText('Kokoro provider/GPU: unknown — Provider omitted')).toBeInTheDocument();
      expect(screen.getByText('Use nvidia-smi -l 1')).toBeInTheDocument();
    });
  });

  it('renders unknown status when a GPU service entry is missing', async () => {
    vi.spyOn(serverApi, 'getLocalGpuValidationReport').mockResolvedValue({
      generated_at: '2026-05-24T00:00:00.000Z',
      services: [{ service: 'ollama', status: 'unknown', reason: 'No endpoint confirmation', suggested_commands: [] }],
      manual_guidance: ['Use nvidia-smi -l 1'],
    });

    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));

    await waitFor(() => {
      expect(screen.getByText('faster-whisper GPU: unknown — Service validation entry not available in this report.')).toBeInTheDocument();
      expect(screen.getByText('Kokoro provider/GPU: unknown — Service validation entry not available in this report.')).toBeInTheDocument();
    });
  });

  it('keeps transcript text visible in unmuted mode while assistant turn runs', async () => {
    vi.spyOn(serverApi, 'generateLocalAssistantMessage').mockResolvedValue({ ok: true, text: 'assistant response', runtime: 'local_ollama' } as never);

    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));
    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'hello' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(screen.getByText(/hello/)).toBeInTheDocument());
  });

  it('does not call TTS when muted', async () => {
    vi.spyOn(serverApi, 'generateLocalAssistantMessage').mockResolvedValue({ ok: true, text: 'assistant muted response', runtime: 'local_ollama' } as never);
    const synthSpy = vi.spyOn(serverApi, 'synthesizeLocalAssistantMessage').mockResolvedValue({ audio_status: 'unavailable', audio_base64: null, audio_format: null });

    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));
    fireEvent.click(screen.getByRole('button', { name: 'Mute Chappy' }));
    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'hello muted' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(screen.getByText(/hello muted/)).toBeInTheDocument());
    expect(synthSpy).not.toHaveBeenCalled();
  });



  it('shows turn latency section in admin console only', async () => {
    render(<MemoryRouter initialEntries={[ROUTES.chappy]}><App /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));
    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));
    expect(screen.getByRole('heading', { name: 'Turn Latency' })).toBeInTheDocument();

    render(<MemoryRouter initialEntries={[ROUTES.chappy]}><App /></MemoryRouter>);
  });

  it('opens and closes Admin Runtime Console from toolbar for admin only', async () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    fireEvent.click(screen.getByRole('button', { name: 'Admin' }));
    expect(screen.getByRole('dialog', { name: 'Admin Runtime Console' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Local Runtime Readiness' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'GPU Validation' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Service Endpoints' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Troubleshooting' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Diagnostics' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Admin Runtime Console' })).not.toBeInTheDocument());
  });

  it('does not expose local gpu validation panel in normal /chappy/session/:sessionId', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    expect(screen.queryByRole('heading', { name: 'Local GPU Validation' })).not.toBeInTheDocument();
    expect(screen.queryByText(/nvidia-smi -l 1/)).not.toBeInTheDocument();
  });

  it('does not show Voice Studio controls in normal /chappy/session/:sessionId user sessions', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    expect(screen.queryByRole('heading', { name: 'Voice Studio shell (admin only)' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Record or upload voice samples' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create draft profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Test generated speech' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve profile' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Publish global voice' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Disable or revert to fallback' })).not.toBeInTheDocument();
  });

  it('keeps admin voice/avatar shells free of committed private voice/avatar asset imports', () => {
    const voiceShellSource = readFileSync(resolve(process.cwd(), 'apps/askchappy-ui/src/admin/voice/VoiceStudioPage.tsx'), 'utf8');
    const avatarShellSource = readFileSync(resolve(process.cwd(), 'apps/askchappy-ui/src/admin/avatar/AvatarAdminPage.tsx'), 'utf8');
    const combinedSource = `${voiceShellSource}\n${avatarShellSource}`.toLowerCase();

    expect(combinedSource).not.toMatch(/\.(wav|mp3|m4a|ogg|flac|webm|bin|pt|ckpt|onnx|npy|npz|pkl|emb|embedding|jpg|jpeg|png|webp|gif|glb|gltf|fbx|obj)['"]/);
    expect(combinedSource).not.toContain('/assets/voice');
    expect(combinedSource).not.toContain('/assets/avatar');
  });

  it('renders phase 11 avatar shell placeholder readiness status', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: MVP_ADMIN_EMAIL } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('link', { name: 'Avatar' }));

    expect(screen.getByRole('heading', { name: 'Avatar setup and review shell (admin only)' })).toBeInTheDocument();
    expect(screen.getByText('Placeholder avatar is active.')).toBeInTheDocument();
    expect(screen.getByText('Real avatar implementation is not implemented in Phase 11.')).toBeInTheDocument();
    expect(screen.getByText('State-aware avatar scaffold is active.')).toBeInTheDocument();
    expect(screen.getByText('Speaking/viseme-capable avatar is not implemented in Phase 11.')).toBeInTheDocument();
    expect(screen.getByText('No private avatar assets are committed.')).toBeInTheDocument();
  });

  it('start open q&a creates local session and navigates to session shell', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    expect(screen.getByLabelText('askchappy session room')).toHaveClass('meeting-room', 'session-shell');
    expect(screen.getByLabelText('top meeting bar')).toBeInTheDocument();
    expect(screen.getByLabelText('meeting body')).toHaveClass('meeting-content');
    expect(screen.getByLabelText('chappy stage')).toBeInTheDocument();
    expect(screen.getByLabelText('meeting stage')).toBeInTheDocument();
    expect(screen.getByLabelText('chappy avatar placeholder')).toBeInTheDocument();
    expect(screen.getByText('Chappy is ready')).toBeInTheDocument();
    expect(screen.getByText('Primary participant')).toBeInTheDocument();
    expect(screen.getByLabelText('transcript panel')).toHaveClass('meeting-chat-panel');
    expect(screen.getByText('Ask Chappy anything by typing or using your mic.')).toBeInTheDocument();
    expect(screen.getByText('Cloned voice status: Cloned voice not configured.')).toBeInTheDocument();
    expect(screen.getByLabelText('transcript panel')).toBeInTheDocument();
    expect(screen.getByLabelText('meeting side column')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'bottom meeting toolbar' })).toHaveClass('meeting-toolbar');
    expect(screen.getByLabelText('voice input panel')).toBeInTheDocument();
    expect(screen.getByLabelText('typed input form')).toBeInTheDocument();
    expect(screen.getByText(/Local runtime status: checking/)).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: 'Speak' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mute Chappy' })).toBeInTheDocument();

    expect(screen.queryByText('Start recording')).not.toBeInTheDocument();
    expect(screen.queryByText('Stop recording')).not.toBeInTheDocument();
    expect(screen.queryByText('Start speaking')).not.toBeInTheDocument();
    expect(screen.queryByText('Stop speaking')).not.toBeInTheDocument();
    expect(screen.queryByText('Avatar asset status')).not.toBeInTheDocument();
    expect(screen.queryByText('Supports visemes')).not.toBeInTheDocument();
    expect(screen.queryByText('Supports speaking animation')).not.toBeInTheDocument();
    const rightRail = screen.getByRole('complementary', { name: 'session right rail' });
    expect(rightRail).toHaveClass('guided-modes-panel', 'compact');
  });

  it('shows local runtime readiness statuses with reason text', async () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    await waitFor(() => {
      expect(screen.getByText('Runtime')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Runtime'));
    expect(screen.getByText(/Ollama: .* — /)).toBeInTheDocument();
    expect(screen.getByText(/Kokoro TTS: .* — /)).toBeInTheDocument();
    expect(screen.getByText(/faster-whisper STT: .* — /)).toBeInTheDocument();
    expect(screen.getByText(/Browser mic: .* — /)).toBeInTheDocument();
    expect(screen.getByText(/Standard voice: selected_default — /)).toBeInTheDocument();
    expect(screen.getByText(/Cloned voice: optional_gated — /)).toBeInTheDocument();
  });


  it('typed input appends user canonical transcript message with typed source and text', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'hello chappy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(screen.getByText('user:')).toBeInTheDocument();
    expect(screen.getByText(/hello chappy/)).toBeInTheDocument();
  });


  it('avatar stage state text does not append transcript messages or fake assistant messages', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    expect(screen.getByText('Chappy is ready')).toBeInTheDocument();
    expect(screen.queryByText('assistant:')).not.toBeInTheDocument();
    expect(screen.queryByText('user:')).not.toBeInTheDocument();
  });

  it('does not show avatar admin controls in normal /chappy/session/:sessionId user sessions', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    expect(screen.queryByRole('heading', { name: 'Avatar setup and review shell (admin only)' })).not.toBeInTheDocument();
    expect(screen.queryByText('State-aware avatar scaffold is active.')).not.toBeInTheDocument();
  });

  it('empty typed input does not append transcript message', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(screen.queryByText('user:')).not.toBeInTheDocument();
  });

  it('shows no-speech notice, keeps session recoverable, and does not append transcript on STT no_speech', async () => {
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
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: '   ' }) }));

    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    fireEvent.click(screen.getByRole('button', { name: 'Mic ready to record' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mic recording' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Mic recording' }));

    await waitFor(() => {
      expect(screen.getByText('Mic state: No speech detected')).toBeInTheDocument();
      expect(screen.getByLabelText('meeting stage')).toBeInTheDocument();
    });

    expect(screen.queryByText('user:')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mic ready to record' })).toBeInTheDocument();
  });

  it('shows transcription-failed notice when STT runtime returns HTTP 500', async () => {
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
    process.env.FASTER_WHISPER_BASE_URL = 'http://127.0.0.1:8890';
    process.env.FASTER_WHISPER_MODEL = 'base.en';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: { error: 'transcription_failed', detail: 'decode failed' } }),
    }));

    render(<MemoryRouter initialEntries={[ROUTES.chappy]}><App /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));
    fireEvent.click(screen.getByRole('button', { name: 'Mic ready to record' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Mic recording' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Mic recording' }));

    await waitFor(() => expect(screen.getByText(/Mic state: STT failed during transcription\./)).toBeInTheDocument());
    expect(screen.queryByText('user:')).not.toBeInTheDocument();
  });

  it('renders right rail in open_qa mode initially', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    const rightRail = screen.getByRole('complementary', { name: 'session right rail' });
    expect(within(rightRail).getByRole('heading', { name: 'Current mode' })).toBeInTheDocument();
    expect(within(rightRail).getByRole('button', { name: 'Open Q&A' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(rightRail).getByText('Ask Chappy anything about DDN positioning, use cases, or partner scenarios.')).toBeInTheDocument();
  });

  it('switches guided modes and keeps session id/transcript intact', () => {
    render(
      <MemoryRouter initialEntries={[ROUTES.chappy]}>
        <App />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'person@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join Chappy Room' }));

    const sessionText = screen.getByLabelText('top meeting bar').textContent ?? '';
    const sessionId = (sessionText.match(/session_[a-z0-9-]+/i)?.[0]) ?? '';
    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'keep this transcript' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    fireEvent.click(screen.getByRole('button', { name: 'Learn DDN' }));
    expect(screen.getByText('Build foundational DDN understanding from basics to field usage.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Meeting Prep' }));
    expect(screen.getByText('Prepare meeting objectives, agenda, discovery questions, and talk tracks.')).toBeInTheDocument();
    expect(screen.getByText(/keep this transcript/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open Q&A' }));
    expect(screen.getByText('Ask Chappy anything about DDN positioning, use cases, or partner scenarios.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Type a message'), { target: { value: 'still typing after mode switch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(screen.getByText(/still typing after mode switch/)).toBeInTheDocument();

    const session = getLocalSession(sessionId);
    expect(session?.session_id).toBe(sessionId);
    expect(session?.metadata.askchappy.session_mode).toBe('open_qa');
    expect(session?.metadata.askchappy.persona_id).toBe('ddn_chappy_vptm');
    expect(session?.metadata.askchappy.persona_label).toBe('Chappy');

    const modeChanges = session?.events.filter((event) => event.event_type === 'mode_change') ?? [];
    expect(modeChanges).toHaveLength(3);
    expect(modeChanges.map((event) => ({
      event_type: event.event_type,
      from_mode: event.meta.from_mode,
      to_mode: event.meta.to_mode,
      actor: event.meta.actor,
    }))).toEqual([
      { event_type: 'mode_change', from_mode: 'open_qa', to_mode: 'learn_ddn', actor: 'user' },
      { event_type: 'mode_change', from_mode: 'learn_ddn', to_mode: 'meeting_prep', actor: 'user' },
      { event_type: 'mode_change', from_mode: 'meeting_prep', to_mode: 'open_qa', actor: 'user' },
    ]);

    expect(session?.transcript.every((message) => message.role === 'user')).toBe(true);
    expect(session?.transcript.every((message) => message.text !== 'mode_change')).toBe(true);
    expect(screen.queryByText('mode_change')).not.toBeInTheDocument();
  });
  it('keeps voice studio controls absent in normal /chappy/session route', () => {
    render(
      <MemoryRouter initialEntries={['/chappy/session/session_123']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Voice Studio')).not.toBeInTheDocument();
  });

  const retiredRouteCases = ['/demo', '/demo/intake', '/demo/recommendation', '/visual-session/session_123', '/demo/summary/session_123'] as const;

  it.each(retiredRouteCases)('resolves retired route %s as non-active UX', (path) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Route not found' })).toBeInTheDocument();
  });
});
