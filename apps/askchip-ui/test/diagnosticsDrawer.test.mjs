import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { DiagnosticsDrawer } from '../.test-dist/diagnostics/DiagnosticsDrawer.js';

const baseProps = {
  connectionState: 'connected',
  topLevelState: 'ready',
  modelName: 'phi4-mini',
  audioDiagnostics: {
    selectedDeviceLabel: 'Desk mic',
    selectedDeviceId: 'mic-1',
    permissionState: 'granted',
    availability: 'available',
    streamActive: false,
    streamError: null,
    liveLevel: 0,
  },
  webrtcDiagnostics: {
    sessionId: null,
    connectionState: 'idle',
    iceConnectionState: 'new',
    signalingState: 'stable',
    lastError: null,
  },
  events: [],
  timings: [],
  config: null,
  readiness: null,
  speechState: { activeMessageId: null, pendingMessageId: null, speechError: null },
  turnLatencySummaries: [],
  readinessError: null,
  collapsed: false,
  onToggle: () => {},
};

describe('DiagnosticsDrawer', () => {
  it('renders speech playback diagnostics errors without blocking typed chat', () => {
    const markup = renderToStaticMarkup(React.createElement(DiagnosticsDrawer, {
      ...baseProps,
      speechState: { activeMessageId: null, pendingMessageId: null, speechError: 'audio output unavailable' },
    }));

    assert.match(markup, /Latest speech playback\/TTS error: audio output unavailable/);
    assert.match(markup, /No readiness snapshot loaded yet/);
  });

  it('renders readiness diagnostics load errors separately from the main API state', () => {
    const markup = renderToStaticMarkup(React.createElement(DiagnosticsDrawer, {
      ...baseProps,
      readinessError: 'readiness fetch failed',
    }));

    assert.match(markup, /Readiness diagnostics load error: readiness fetch failed/);
  });
});
