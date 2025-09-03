
/**
 * soft-bargein.js
 * Production-style barge-in controller (soft / echo-aware).
 *
 * How it works:
 *  - While the assistant is speaking, VAD hits will PAUSE playback (mute) immediately.
 *  - If speech continues for confirmMs (default 400ms), we COMMIT the interrupt:
 *      * Stop/mute playback
 *      * Send an optional interrupt command to the server (if supported)
 *      * Emit a 'chip:interrupt' DOM event for app code to react to
 *  - If speech stops before confirmMs, playback RESUMES seamlessly.
 *
 * Integration points expected from the host app:
 *  - A VAD object exposing: on(event, fn) / off(event, fn) and setSpeakingMode(isSpeaking, boost?)
 *    (If not present, we also listen to window 'chip:vad_speechstart'/'chip:vad_speechend' events)
 *  - A TTS player object exposing: mute(boolean) and stop()
 *  - A WebSocket (or socket-like) with send(JSON) for the optional interrupt command.
 *
 * This module does not assume any UI framework. It uses DOM CustomEvents for app-wide notifications.
 */

export class SoftBargeIn {
  /**
   * @param {Object} opts
   * @param {{on?:Function, off?:Function, setSpeakingMode?:Function}} opts.vad - VAD interface
   * @param {{send?:Function}} opts.socket - WS/socket to notify server (optional)
   * @param {{mute?:Function, stop?:Function}} opts.ttsPlayer - audio player adapter
   * @param {number} [opts.confirmMs=400] - duration to confirm user intent
   * @param {number} [opts.echoThresholdBoost=1.8] - VAD threshold boost while assistant speaks
   * @param {Function} [opts.onPendingUI] - callback(true|false) for pause/resume UI
   * @param {string} [opts.interruptCmd='interrupt'] - command to send to server when committed
   */
  constructor({
    vad, socket, ttsPlayer,
    confirmMs = 400,
    echoThresholdBoost = 1.8,
    onPendingUI = () => {},
    interruptCmd = 'interrupt',
  } = {}) {
    this.vad = vad;
    this.socket = socket;
    this.player = ttsPlayer;
    this.confirmMs = Math.max(200, confirmMs | 0);
    this.echoThresholdBoost = echoThresholdBoost;
    this.onPendingUI = onPendingUI;
    this.interruptCmd = interruptCmd;

    this.state = 'idle'; // idle | speaking | paused_pending | interrupted
    this._commitTimer = null;

    this._handleSpeechStart = this._handleSpeechStart.bind(this);
    this._handleSpeechEnd = this._handleSpeechEnd.bind(this);
  }

  /**
   * Wire to VAD events.
   */
  wire() {
    if (this.vad && typeof this.vad.on === 'function') {
      this.vad.on('speechstart', this._handleSpeechStart);
      this.vad.on('speechend', this._handleSpeechEnd);
    } else {
      window.addEventListener('chip:vad_speechstart', this._handleSpeechStart);
      window.addEventListener('chip:vad_speechend', this._handleSpeechEnd);
    }
  }

  /**
   * Unwire VAD events.
   */
  unwire() {
    if (this.vad && typeof this.vad.off === 'function') {
      this.vad.off('speechstart', this._handleSpeechStart);
      this.vad.off('speechend', this._handleSpeechEnd);
    } else {
      window.removeEventListener('chip:vad_speechstart', this._handleSpeechStart);
      window.removeEventListener('chip:vad_speechend', this._handleSpeechEnd);
    }
  }

  /**
   * Call when first assistant audio chunk arrives.
   */
  onAssistantAudioStart() {
    // If we are already in 'speaking' due to earlier chunk, keep it.
    if (this.state !== 'paused_pending' && this.state !== 'interrupted') {
      this.state = 'speaking';
      // Make VAD more conservative to resist echo while Chip is talking
      try { this.vad?.setSpeakingMode?.(true, this.echoThresholdBoost); } catch {}
    }
  }

  /**
   * Call when assistant stream ends.
   */
  onAssistantAudioEnd() {
    this._clearPending();
    this.state = 'idle';
    try { this.vad?.setSpeakingMode?.(false); } catch {}
  }

  /**
   * Immediate/manual interrupt (e.g., button/keypress).
   */
  immediateInterrupt(reason = 'manual') {
    if (this.state === 'idle') return;
    this._commitInterrupt(reason);
  }

  // ---------------- private ----------------

  _handleSpeechStart() {
    // Only relevant if the assistant is speaking
    if (this.state !== 'speaking') return;

    // Soft pause: mute playback but continue to buffer
    try { this.player?.mute?.(true); } catch {}
    this.onPendingUI(true);

    this.state = 'paused_pending';

    // Confirm intent after confirmMs
    this._commitTimer = setTimeout(() => {
      this._commitInterrupt('vad');
    }, this.confirmMs);
  }

  _handleSpeechEnd() {
    // If we were waiting for confirmation and the user stopped quickly, resume.
    if (this.state === 'paused_pending') {
      this._clearPending();
      this.state = 'speaking';
      this.onPendingUI(false);
      try { this.player?.mute?.(false); } catch {}
      // Keep the conservative VAD thresholds since Chip is still speaking
      try { this.vad?.setSpeakingMode?.(true, this.echoThresholdBoost); } catch {}
    }
  }

  _commitInterrupt(reason) {
    this._clearPending();

    // Stop/mute local playback
    try { this.player?.stop?.(); } catch { try { this.player?.mute?.(true); } catch {} }

    // Notify server (best-effort)
    try {
      this.socket?.send?.(JSON.stringify({ type: 'control', cmd: this.interruptCmd, reason }));
    } catch {}

    // Let the rest of the app know
    try {
      window.dispatchEvent(new CustomEvent('chip:interrupt', { detail: { reason } }));
    } catch {}

    this.state = 'interrupted';
    // Allow VAD to go back to normal thresholds for user turn
    try { this.vad?.setSpeakingMode?.(false); } catch {}
  }

  _clearPending() {
    if (this._commitTimer) {
      clearTimeout(this._commitTimer);
      this._commitTimer = null;
    }
    this.onPendingUI(false);
  }
}
