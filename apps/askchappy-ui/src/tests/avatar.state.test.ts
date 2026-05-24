import { describe, expect, it } from 'vitest';
import { SESSION_STATES, type SessionState } from '../../../../shared/contracts/session';
import {
  CHAPPY_AVATAR_STATE_CONFIG,
  CHAPPY_AVATAR_STATE_KEYS,
  getChappyAvatarRuntimeStatus,
  getChappyAvatarStateConfig,
} from '../avatar/avatarState';

describe('phase 11 avatar state module', () => {
  it('maps every shared SESSION_STATES value to avatar display config', () => {
    for (const state of SESSION_STATES) {
      expect(CHAPPY_AVATAR_STATE_CONFIG[state]).toBeDefined();
      expect(getChappyAvatarStateConfig(state).state).toBe(state);
      expect(getChappyAvatarStateConfig(state).label.length).toBeGreaterThan(0);
    }
  });

  it('does not miss any shared session state', () => {
    expect(Object.keys(CHAPPY_AVATAR_STATE_CONFIG).sort()).toEqual([...SESSION_STATES].sort());
    expect([...CHAPPY_AVATAR_STATE_KEYS]).toEqual([...SESSION_STATES]);
  });

  it('returns placeholder runtime hooks for future avatar assets and animation', () => {
    const runtime = getChappyAvatarRuntimeStatus('ready');
    expect(runtime).toEqual({
      avatar_asset_status: 'placeholder',
      supports_visemes: false,
      supports_speaking_animation: false,
      current_state: 'ready',
    });
  });

  it.each([
    ['ready', 'vChappy is ready'],
    ['listening', 'vChappy is listening'],
    ['transcribing', 'vChappy is transcribing'],
    ['thinking', 'vChappy is thinking'],
    ['speaking', 'vChappy is speaking'],
    ['error', 'vChappy needs attention'],
  ] as const)('maps %s to expected label', (state, label) => {
    expect(getChappyAvatarStateConfig(state as SessionState).label).toBe(label);
  });
});
