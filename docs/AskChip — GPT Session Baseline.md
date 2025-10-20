diff --git a/static/js/voice/runtime/AdaptiveRuntime.js b/static/js/voice/runtime/AdaptiveRuntime.js
index 202590066732660ba2b9bb49ee6cc9b36b4c5cc1..46146926f316730c2625b6291bdf4d0feb42d89e 100644
--- a/static/js/voice/runtime/AdaptiveRuntime.js
+++ b/static/js/voice/runtime/AdaptiveRuntime.js
@@ -1432,80 +1432,94 @@ function shouldCloseTurn(ctx) {
         clientSilent,
         asrSilent,
         quietMs: quietLogMs,
         silentDebounceMs: silentSince,
       });
     } catch {}
   }
   const debounceReached = Number.isFinite(silentSince) && silentSince >= DUAL_VAD_SILENCE_DEBOUNCE_MS;
   if (debounceReached) {
     return true;
   }
   return quietMs > quietCloseMs(ctx);
 }
 
 const frameIndicatesAsrResult = (frame) => {
   const type = normalizeVadLabel(frame?.type);
   const event = normalizeVadLabel(frame?.event);
   return type === 'result' || type === 'results' || event === 'result' || event === 'results';
 };
 
 function handleWsFrame(ctx, frame) {
   if (!ctx?.state) return;
   const type = normalizeVadLabel(frame?.type);
   const event = normalizeVadLabel(frame?.event);
 
-  if (
+  const isAssistantAudio = type === 'assistant_audio' || event === 'assistant_audio';
+  const isExplicitTtsStart = (
     type === 'tts:start'
     || event === 'tts:start'
     || type === 'tts_start'
     || event === 'tts_start'
-  ) {
+  );
+
+  if (isExplicitTtsStart) {
     try {
       const maybePromise = handleClientTtsStartTelemetry(ctx, frame);
       if (maybePromise && typeof maybePromise.catch === 'function') {
         maybePromise.catch(() => {});
       }
     } catch {}
   }
 
+  if (isExplicitTtsStart || isAssistantAudio) {
+    const phaseState = typeof frame?.phase === 'string' && frame.phase.trim()
+      ? frame.phase
+      : 'assistant_speaking';
+    applyTtsState(ctx, phaseState, frame);
+  }
+
   if (type === 'state') {
     if (applyTtsState(ctx, frame?.phase, frame)) {
       return;
     }
   }
 
   if (type === 'barge_in' || event === 'barge_in') {
     applyTtsState(ctx, frame?.phase || 'paused', frame);
     return;
   }
 
   if (type === 'barge_resume' || event === 'barge_resume') {
     applyTtsState(ctx, frame?.phase || 'assistant_speaking', frame);
     return;
   }
 
+  if (type === 'utteranceend' || event === 'utteranceend') {
+    applyTtsState(ctx, 'ended', frame);
+  }
+
   if (frameIndicatesAsrResult(frame)) {
     const confidence = extractAsrConfidence(frame);
     onAsrPartial(ctx, { confidence });
     if (dualVadEnabled(ctx) && Number.isFinite(confidence) && confidence >= commitConfidenceThreshold(ctx)) {
       if (!ctx.state.pendingCommitReason) {
         ctx.state.pendingCommitReason = 'asr_confidence';
       }
     }
     if (dualVadEnabled(ctx)) {
       const attempt = maybeCommitSpeech(ctx);
       if (attempt && typeof attempt.then === 'function') {
         attempt.catch(() => {});
       }
     }
     const isFinal = Boolean(
       frame?.channel?.is_final ?? frame?.is_final ?? frame?.final ?? frame?.detail?.is_final
     );
     if (isFinal) {
       const asrState = ensureAsrState(ctx);
       asrState.speaking = false;
       if (!Number.isFinite(asrState.lastActivityTs) || asrState.lastActivityTs <= 0) {
         asrState.lastActivityTs = nowMs();
       }
     }
     return;
