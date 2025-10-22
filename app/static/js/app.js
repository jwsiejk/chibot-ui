// AskChip UI interactions + waveform visual
(() => {
  // --- Config & mock current user for gating ---
  // Set admins here (could also be injected server-side)
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

  // --- Start/End button demo wiring ---
  const startBtn = document.getElementById('startBtn');
  const endBtn = document.getElementById('endBtn');
  const sidText = document.getElementById('sid-text');
  const connLabel = document.getElementById('connLabel');
  const statusDot = document.querySelector('.status-dot');

  function randomSID(){ return 'sid-' + Math.random().toString(36).slice(2, 10); }

  startBtn.addEventListener('click', async () => {
    startBtn.disabled = true; endBtn.disabled = false;
    sidText.textContent = randomSID();
    connLabel.textContent = "Connected";
    statusDot.classList.add('on');
    // try enabling mic visual
    await Waveform.start();
  });
  endBtn.addEventListener('click', () => {
    startBtn.disabled = false; endBtn.disabled = true;
    connLabel.textContent = "Disconnected";
    statusDot.classList.remove('on');
    Waveform.stop();
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
      // gradient wash
      const g = ctx.createLinearGradient(0,0,w,h);
      g.addColorStop(0,'#0b1222'); g.addColorStop(1,'#0a0f1b');
      ctx.fillStyle = g; ctx.fillRect(0,0,w,h);
      // subtle grid
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
      const hueA = 22; // brandish orange
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
    // initial paint
    drawBackground(); drawSynth();
    return { start, stop };
  })();

  // Latency demo ticker
  const latencyHint = document.getElementById('latencyHint');
  setInterval(() => {
    latencyHint.textContent = `${Math.round(20 + Math.random()*15)} ms`;
  }, 1400);
})();
