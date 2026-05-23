import { describe, expect, it } from 'vitest';
import { getHealth } from '../api/server';

describe('askchappy-api scaffold', () => {
  it('returns placeholder health', () => {
    expect(getHealth()).toEqual({ service: 'askchappy-api', status: 'placeholder' });
  });
});
