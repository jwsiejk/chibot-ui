import { describe, expect, it } from 'vitest';
import { RETIRED_ROUTES, ROUTES } from '../../../../shared/contracts/askchappy';
import { AUTH_ROLES, getRoleForEmail, isAuthRole, MVP_ADMIN_EMAIL } from '../../../../shared/contracts/auth';
import { DEFAULT_SESSION_MODE, isSessionMode, SESSION_MODES } from '../../../../shared/contracts/modes';
import { DEFAULT_METADATA, isAskChappyMetadata, isSessionState, SESSION_STATES } from '../../../../shared/contracts/session';
import {
  isTranscriptMessage,
  isTranscriptRole,
  isTranscriptSource,
  TRANSCRIPT_ROLES,
  TRANSCRIPT_SOURCES,
} from '../../../../shared/contracts/transcript';
import { isVoiceProfileState, VOICE_PROFILE_STATES } from '../../../../shared/contracts/voice';

describe('mode contracts', () => {
  it('validates allowed session modes and default', () => {
    expect(DEFAULT_SESSION_MODE).toBe('open_qa');
    for (const mode of SESSION_MODES) {
      expect(isSessionMode(mode)).toBe(true);
    }
  });

  it('rejects invalid modes', () => {
    expect(isSessionMode('invalid_mode')).toBe(false);
  });
});

describe('auth contracts', () => {
  it('validates roles', () => {
    for (const role of AUTH_ROLES) {
      expect(isAuthRole(role)).toBe(true);
    }
    expect(isAuthRole('owner')).toBe(false);
  });

  it('maps admin email and all others correctly', () => {
    expect(MVP_ADMIN_EMAIL).toBe('jsiejk@ddn.com');
    expect(getRoleForEmail('jsiejk@ddn.com')).toBe('admin');
    expect(getRoleForEmail('someone@example.com')).toBe('standard_user');
  });
});

describe('transcript contracts', () => {
  const validMessage = {
    id: 'msg_1',
    ts: '2026-05-23T00:00:00.000Z',
    role: 'user',
    text: 'hello',
    source: 'typed',
    session_id: 'session_1',
    meta: {},
  } as const;

  it('validates required transcript fields', () => {
    expect(isTranscriptMessage(validMessage)).toBe(true);
    expect(isTranscriptMessage({ ...validMessage, text: undefined })).toBe(false);
    expect(isTranscriptMessage({ ...validMessage, id: 123 })).toBe(false);
  });

  it('does not accept content as transcript body field', () => {
    expect(isTranscriptMessage({ ...validMessage, content: 'wrong field' })).toBe(false);
    expect('content' in validMessage).toBe(false);
  });

  it('validates transcript roles', () => {
    for (const role of TRANSCRIPT_ROLES) {
      expect(isTranscriptRole(role)).toBe(true);
    }
    expect(isTranscriptRole('tool')).toBe(false);
  });

  it('validates transcript sources', () => {
    for (const source of TRANSCRIPT_SOURCES) {
      expect(isTranscriptSource(source)).toBe(true);
    }
    expect(isTranscriptSource('email')).toBe(false);
  });
});

describe('session contracts', () => {
  it('validates session states', () => {
    for (const state of SESSION_STATES) {
      expect(isSessionState(state)).toBe(true);
    }
    expect(isSessionState('idle')).toBe(false);
  });

  it('uses metadata.askchappy and not metadata.expert_desk', () => {
    expect(DEFAULT_METADATA.askchappy).toBeDefined();
    expect(DEFAULT_METADATA.askchappy.session_mode).toBe('open_qa');
    expect(Object.hasOwn(DEFAULT_METADATA, 'expert_desk')).toBe(false);
    expect(isAskChappyMetadata(DEFAULT_METADATA)).toBe(true);
    expect(
      isAskChappyMetadata({
        expert_desk: {},
      }),
    ).toBe(false);
  });
});

describe('voice contracts', () => {
  it('validates voice lifecycle states', () => {
    for (const state of VOICE_PROFILE_STATES) {
      expect(isVoiceProfileState(state)).toBe(true);
    }
    expect(isVoiceProfileState('archived')).toBe(false);
  });
});

describe('route contracts', () => {
  it('keeps canonical routes active constants', () => {
    expect(Object.values(ROUTES)).toEqual([
      '/',
      '/chappy',
      '/chappy/session/:sessionId',
      '/chappy/summary/:sessionId',
      '/dev',
      '/admin',
      '/admin/voice',
      '/admin/avatar',
    ]);
  });

  it('keeps retired routes inactive constants', () => {
    expect(RETIRED_ROUTES).toEqual([
      '/demo',
      '/demo/intake',
      '/demo/recommendation',
      '/visual-session/:sessionId',
      '/demo/summary/:sessionId',
    ]);
  });
});
