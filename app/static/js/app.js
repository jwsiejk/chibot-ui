(() => {
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-dynamic="${src}"]`);
      if (existing) {
        if (existing.dataset.loaded === "true") {
          resolve();
        } else {
          existing.addEventListener("load", resolve, { once: true });
          existing.addEventListener("error", reject, { once: true });
        }
        return;
      }
      const el = document.createElement("script");
      el.src = src;
      el.async = false;
      el.dataset.dynamic = src;
      el.addEventListener("load", () => {
        el.dataset.loaded = "true";
        resolve();
      }, { once: true });
      el.addEventListener("error", reject, { once: true });
      document.head.appendChild(el);
    });
  }

  async function ensureRuntimeModules() {
    if (!window.AppState) {
      await loadScript("./state.js");
    }
    if (!window.AudioPlayer) {
      await loadScript("./audio_player.js");
    }
    if (!window.WSClient) {
      await loadScript("./ws_client.js");
    }
    if (!window.AudioRecorder) {
      await loadScript("./audio_recorder.js");
    }
    if (!window.PolicyBadges) {
      await loadScript("./policy_badges.js");
    }
  }

  async function init() {
    await ensureRuntimeModules();

    const AppState = window.AppState;
    const WSClient = window.WSClient;
    if (window.PolicyBadges && typeof window.PolicyBadges.init === "function") {
      try {
        window.PolicyBadges.init();
      } catch (err) {
        console.warn("Failed to initialize PolicyBadges", err);
      }
    }

    // --- Config & mock current user for gating ---
    window.ADMIN_EMAILS = (window.ADMIN_EMAILS || ["admin@askchip.ai"]).map(e => e.toLowerCase());
    const urlParams = new URLSearchParams(window.location.search);
    const currentUserEmail = (urlParams.get("email") || "user@example.com").toLowerCase();
    const isAdmin = window.ADMIN_EMAILS.includes(currentUserEmail);

    // --- Top-right brand dropdown ---
    const brandBtn = document.getElementById('brandBtn');
    const brandMenu = document.getElementById('brandMenu');
    const adminItem = document.getElementById('adminItem');
    if (!isAdmin) {
      adminItem.setAttribute('aria-disabled', 'true');
      adminItem.title = "Admins only";
    }
    function closeMenu(){ brandMenu.classList.remove('open'); brandBtn.setAttribute('aria-expanded','false'); }
    brandBtn.addEventListener('click', (e) => {
      const open = brandMenu.classList.toggle('open');
      brandBtn.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', (e) => {
      if (!brandMenu.contains(e.target) && !brandBtn.contains(e.target)) closeMenu();
    });
    adminItem.addEventListener('click', () => {
      if (isAdmin) {
        alert("Open Admin UI (placeholder).");
        closeMenu();
      }
    });
    document.getElementById('profileItem').addEventListener('click', () => {
      alert(`Profile for ${currentUserEmail} (placeholder).`); closeMenu();
    });
    document.getElementById('logoutItem').addEventListener('click', () => {
      alert("Logging out… (placeholder)"); closeMenu();
    });    

    // --- Chat toggle ---
    const openChatBtn = document.getElementById('openChatBtn');
    const chatPanel = document.querySelector('.chat');
    openChatBtn.addEventListener('click', () => {
      const hidden = chatPanel.classList.toggle('hidden');
      openChatBtn.setAttribute('aria-pressed', String(!hidden));
    });

    // --- Start/End button wiring ---
    const startBtn = document.getElementById('startBtn');
    const endBtn = document.getElementById('endBtn');
    const sidText = document.getElementById('sid-text');
    const connLabel = document.getElementById('connLabel');
    const statusDot = document.querySelector('.status-dot');
    const latencyHint = document.getElementById('latencyHint');

    const accessTokenParam = urlParams.get('access_token') || urlParams.get('token');
    const defaultToken = (window.DEMO_ACCESS_TOKEN || accessTokenParam || '').trim();

    function resolveAccessToken() {
      return defaultToken || 'demo-token';
    }

    startBtn.addEventListener('click', async () => {
      startBtn.disabled = true;
      const accessToken = resolveAccessToken();
      const state = AppState.getState();
      const resumeState = state && typeof state.resume === 'object' ? state.resume : null;
      let resumeToken = null;
      if (resumeState && typeof resumeState.token === 'string' && Number.isFinite(resumeState.expiresAt) && Date.now() < resumeState.expiresAt) {
        resumeToken = resumeState.token;
      }
      try {
        const options = resumeToken ? { resumeToken } : undefined;
        AppState.setState({ resumeError: null });
        if (options) {
          WSClient.open(accessToken, options);
        } else {
          WSClient.open(accessToken);
        }
      } catch (err) {
        console.error('Failed to open WS client', err);
        AppState.setState({ connectionState: 'disconnected' });
        startBtn.disabled = false;
        endBtn.disabled = true;
      }
    });

    endBtn.addEventListener('click', () => {
      WSClient.close('user_requested');
      if (typeof AppState.clearResume === 'function') {
        AppState.clearResume();
      }
      if (window.AudioRecorder) {
        try {
          window.AudioRecorder.stop();
        } catch (err) {
          console.warn('Failed to stop audio recorder', err);
        }
      }
      if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
        window.AudioPlayer.interrupt();
      }
    });

    // --- Resume banner ---
    const resumeBanner = document.createElement('div');
    resumeBanner.setAttribute('role', 'status');
    resumeBanner.setAttribute('aria-live', 'polite');
    resumeBanner.style.position = 'fixed';
    resumeBanner.style.top = '16px';
    resumeBanner.style.right = '16px';
    resumeBanner.style.display = 'none';
    resumeBanner.style.alignItems = 'center';
    resumeBanner.style.gap = '12px';
    resumeBanner.style.padding = '10px 14px';
    resumeBanner.style.borderRadius = '10px';
    resumeBanner.style.background = 'rgba(12, 19, 35, 0.92)';
    resumeBanner.style.color = '#fff';
    resumeBanner.style.fontSize = '13px';
    resumeBanner.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.25)';
    resumeBanner.style.zIndex = '1000';
    resumeBanner.style.backdropFilter = 'blur(8px)';
    resumeBanner.style.webkitBackdropFilter = 'blur(8px)';

    const resumeText = document.createElement('span');
    resumeText.textContent = '';

    const resumeAction = document.createElement('button');
    resumeAction.type = 'button';
    resumeAction.textContent = 'Start new session';
    resumeAction.style.background = '#2251ff';
    resumeAction.style.color = '#fff';
    resumeAction.style.border = 'none';
    resumeAction.style.borderRadius = '6px';
    resumeAction.style.padding = '6px 10px';
    resumeAction.style.fontSize = '12px';
    resumeAction.style.cursor = 'pointer';
    resumeAction.style.fontWeight = '600';

    resumeBanner.append(resumeText, resumeAction);
    document.body.appendChild(resumeBanner);

    let resumeBannerMode = 'hidden';
    let resumeCountdownId = null;

    function stopResumeCountdown() {
      if (resumeCountdownId) {
        clearInterval(resumeCountdownId);
        resumeCountdownId = null;
      }
    }

    function hideResumeBanner() {
      if (resumeBannerMode === 'hidden') return;
      stopResumeCountdown();
      resumeBannerMode = 'hidden';
      resumeBanner.style.display = 'none';
    }

    function renderResumeCountdown() {
      const state = AppState.getState();
      const resume = state && typeof state.resume === 'object' ? state.resume : null;
      if (!resume || !Number.isFinite(resume.expiresAt)) {
        return false;
      }
      const remainingMs = Math.max(0, resume.expiresAt - Date.now());
      const seconds = Math.max(0, Math.ceil(remainingMs / 1000));
      resumeText.textContent = `Reconnecting… (${seconds}s)`;
      return true;
    }

    function showResumeCountdown() {
      if (!renderResumeCountdown()) {
        hideResumeBanner();
        return;
      }
      if (resumeBannerMode !== 'countdown') {
        stopResumeCountdown();
        resumeBannerMode = 'countdown';
        resumeBanner.style.display = 'flex';
        resumeAction.disabled = false;
        resumeCountdownId = setInterval(() => {
          if (!renderResumeCountdown()) {
            hideResumeBanner();
          }
        }, 1000);
      }
    }

    function showResumeError() {
      stopResumeCountdown();
      resumeBannerMode = 'error';
      resumeBanner.style.display = 'flex';
      resumeText.textContent = 'Session resume unavailable. Start a new session to continue.';
      resumeAction.disabled = false;
    }

    function updateResumeBanner(state) {
      const hasCountdown = state.connectionState === 'resuming' && state.resume && Number.isFinite(state.resume.expiresAt);
      if (hasCountdown) {
        showResumeCountdown();
        return;
      }
      if (state.resumeError === 'invalid') {
        showResumeError();
        return;
      }
      hideResumeBanner();
    }

    resumeAction.addEventListener('click', () => {
      resumeAction.disabled = true;
      try {
        WSClient.close('user_restart');
      } catch (err) {
        console.warn('Resume banner close failed', err);
      }
      if (typeof AppState.clearResume === 'function') {
        AppState.clearResume();
      }
      AppState.setState({ resumeError: null });
      const accessToken = resolveAccessToken();
      try {
        WSClient.open(accessToken);
      } catch (err) {
        console.error('Failed to start new session', err);
        AppState.setState({ connectionState: 'disconnected' });
      } finally {
        resumeAction.disabled = false;
      }
    });

    // --- Waveform visual inside the Chip window ---
    const Waveform = (() => {
      const canvas = document.getElementById('waveCanvas');
      const ctx = canvas.getContext('2d', {alpha:false});
      let raf = 0, analyser = null, source = null, audioCtx = null, dataArray = null;
      let synthMode = true, t = 0;

      function resizeCanvas(){
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        canvas.width = canvas.clientWidth * dpr;
        canvas.height = canvas.clientHeight * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      window.addEventListener('resize', resizeCanvas, {passive:true});
      resizeCanvas();

      function drawBackground(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        const g = ctx.createLinearGradient(0,0,w,h);
        g.addColorStop(0,'#0b1222'); g.addColorStop(1,'#0a0f1b');
        ctx.fillStyle = g; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.lineWidth = 1;
        const gap = 24;
        ctx.beginPath();
        for(let x=0;x<w;x+=gap){ ctx.moveTo(x,0); ctx.lineTo(x,h); }
        for(let y=0;y<h;y+=gap){ ctx.moveTo(0,y); ctx.lineTo(w,y); }
        ctx.stroke();        
      }

      function drawSynth(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        const cx = w/2, cy = h/2; const amp = Math.min(120, h*0.28);
        const bars = 96;
        ctx.save();
        ctx.translate(0, cy);
        const hueA = 22;
        const hueB = 204;
        for(let i=0;i<bars;i++){
          const k = i/(bars-1);
          const phase = t*0.015 + k*6.283;
          const value = Math.sin(phase) * Math.sin(k*Math.PI);
          const y = value * amp * (0.85 + 0.15*Math.sin(t*0.01 + k*12));
          const x = k * w;
          const hue = hueA*(1-k) + hueB*k;
          ctx.strokeStyle = `hsla(${hue}, 85%, ${40 + 15*Math.sin(t*0.02 + k*5)}%, 0.9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, y);
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, -y*0.6);
          ctx.stroke();
        }
        ctx.restore();
        t += 1;
      }

      function drawAnalyser(){
        const w = canvas.clientWidth, h = canvas.clientHeight;
        if (!analyser || !dataArray){ drawSynth(); return; }
        analyser.getByteFrequencyData(dataArray);
        const bars = 96;
        const step = Math.max(1, Math.floor(dataArray.length / bars));
        ctx.save();
        ctx.translate(0, h/2);
        for(let i=0;i<bars;i++){
          const v = dataArray[i*step] / 255;
          const y = (v*v) * (h*0.35);
          const k = i/(bars-1);
          ctx.strokeStyle = `hsla(${22*(1-k) + 204*k}, 85%, ${45 + v*20}%, .9)`;
          ctx.lineWidth = 2.2;
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), y); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(i*(w/(bars-1)), 0); ctx.lineTo(i*(w/(bars-1)), -y*0.6); ctx.stroke();
        }
        ctx.restore();
      }

      function loop(){
        drawBackground();
        if (synthMode) drawSynth(); else drawAnalyser();
        raf = requestAnimationFrame(loop);
      }

      async function start(){
        if (raf) return;
        try{
          const stream = await navigator.mediaDevices.getUserMedia({audio:true});
          audioCtx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: 48000});
          analyser = audioCtx.createAnalyser();
          analyser.fftSize = 2048;
          dataArray = new Uint8Array(analyser.frequencyBinCount);
          source = audioCtx.createMediaStreamSource(stream);
          source.connect(analyser);
          synthMode = false;
        }catch(err){
          console.warn("Mic not available; using synth waveform.", err);
          synthMode = true;
        }
        loop();
      }
      function stop(){
        cancelAnimationFrame(raf); raf = 0;
        drawBackground(); drawSynth();
        if (source){ try{ source.disconnect(); }catch{} source = null; }
        if (audioCtx){ try{ audioCtx.close(); }catch{} audioCtx = null; }
      }
      drawBackground(); drawSynth();
      return { start, stop };
    })();

    let previousConnectionState = AppState.getState().connectionState;
    AppState.subscribe((state) => {
      sidText.textContent = state.sid || '—';
      let label = 'Disconnected';
      if (state.connectionState === 'connected') label = 'Connected';
      else if (state.connectionState === 'connecting') label = 'Connecting…';
      else if (state.connectionState === 'resuming') label = 'Resuming…';
      connLabel.textContent = label;
      const active = state.connectionState !== 'disconnected';
      statusDot.classList.toggle('on', active);
      startBtn.disabled = active;
      endBtn.disabled = !active;
      if (state.latencyMs != null) {
        latencyHint.textContent = `${Math.round(state.latencyMs)} ms`;
      } else {
        latencyHint.textContent = '—';
      }
      if (previousConnectionState !== 'connected' && state.connectionState === 'connected') {
        Waveform.start();
        if (window.AudioRecorder && typeof window.AudioRecorder.start === 'function') {
          window.AudioRecorder.start().catch((err) => {
            console.error('AudioRecorder start error', err);
          });
        }
      } else if (previousConnectionState !== 'disconnected' && state.connectionState === 'disconnected') {
        Waveform.stop();
        if (window.AudioRecorder && typeof window.AudioRecorder.stop === 'function') {
          try {
            window.AudioRecorder.stop();
          } catch (err) {
            console.warn('AudioRecorder stop error', err);
          }
        }
        if (window.AudioPlayer && typeof window.AudioPlayer.interrupt === 'function') {
          window.AudioPlayer.interrupt();
        }
      }
      previousConnectionState = state.connectionState;
      updateResumeBanner(state);
    });

    // --- Smoke test harness (opt-in via ?wsSmoke=1) ---
    if (urlParams.get('wsSmoke') === '1') {
      const transitions = [];
      const unsubscribe = AppState.subscribe((s) => transitions.push(s.connectionState));

      class MockSocket extends EventTarget {
        constructor() {
          super();
          this.readyState = WebSocket.CONNECTING;
          setTimeout(() => {
            this.readyState = WebSocket.OPEN;
            this.dispatchEvent(new Event('open'));
          }, 0);
        }
        send(payload) {
          this.lastSent = payload;
        }
        close(code = 1000, reason = '') {
          this.readyState = WebSocket.CLOSED;
          const ev = new Event('close');
          ev.code = code;
          ev.reason = reason;
          ev.wasClean = true;
          this.dispatchEvent(ev);
        }
        simulateMessage(frame) {
          const payload = typeof frame === 'string' ? frame : JSON.stringify(frame);
          const ev = new Event('message');
          ev.data = payload;
          this.dispatchEvent(ev);
        }
      }

      const sockets = [];
      WSClient.__debug.setTransportFactory(() => {
        const mock = new MockSocket();
        sockets.push(mock);
        return mock;
      });

      startBtn.click();

      setTimeout(() => {
        const mock = sockets[0];
        if (!mock) return;
        mock.simulateMessage({
          type: 'info',
          meta: { sid: 'smoke-sid', resume_token: 'smoke-resume', resume_ttl_ms: 5000 }
        });
        WSClient.__debug.recordPing(Date.now() - 42);
        mock.simulateMessage({ type: 'pong', t: Date.now() });
        setTimeout(() => {
          endBtn.click();
          console.log('WSClient smoke transitions', transitions);
          console.log('WSClient smoke latency', AppState.getState().latencyMs);
          WSClient.__debug.resetTransportFactory();
          unsubscribe();
        }, 20);
      }, 20);
    }
  }

  init();
})();
