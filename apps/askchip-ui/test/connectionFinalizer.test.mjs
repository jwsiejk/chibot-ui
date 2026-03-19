import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { createConnectionFinalizer } from '../.test-dist/api/connectionFinalizer.js';

describe('createConnectionFinalizer', () => {
  it('reports an error once even if close follows', () => {
    let current = true;
    let clearCalls = 0;
    let errorCalls = 0;
    let closeCalls = 0;

    const finalize = createConnectionFinalizer({
      isCurrentSocket: () => current,
      clearCurrentSocket: () => {
        clearCalls += 1;
        current = false;
      },
      onError: () => {
        errorCalls += 1;
      },
      onClose: () => {
        closeCalls += 1;
      },
    });

    finalize('error');
    finalize('close');

    assert.equal(clearCalls, 1);
    assert.equal(errorCalls, 1);
    assert.equal(closeCalls, 0);
  });

  it('finalizes close once and skips socket clearing when it is no longer current', () => {
    let closeCalls = 0;
    let clearCalls = 0;

    const finalize = createConnectionFinalizer({
      isCurrentSocket: () => false,
      clearCurrentSocket: () => {
        clearCalls += 1;
      },
      onError: () => {},
      onClose: () => {
        closeCalls += 1;
      },
    });

    finalize('close');
    finalize('close');

    assert.equal(closeCalls, 1);
    assert.equal(clearCalls, 0);
  });
});
