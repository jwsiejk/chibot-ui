// Ask Chip — Admin Console wiring
// Uses GET/POST /api/v1/admin/config and the new diagnostics routes.

async function apiGet(url){ const r = await fetch(url, {credentials:'include'}); if(!r.ok) throw new Error(await r.text()); return r.json(); }
async function apiPost(url, body){ const r = await fetch(url,{method:'POST',headers:{'content-type':'application/json'},credentials:'include',body:JSON.stringify(body||{})}); if(!r.ok) throw new Error(await r.text()); return r.json(); }

// ---- Tabs
const tabsEl = document.getElementById('tabs');
tabsEl.addEventListener('click', (e)=>{
  const btn = e.target.closest('button'); if(!btn) return;
  [...tabsEl.querySelectorAll('button')].forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const id = btn.dataset.tab;
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById(`tab-${id}`).classList.add('active');
});

// ---- Load config into UI
async function loadConfig(){
  const cfg = await apiGet('/api/v1/admin/config');

  // UX
  setVal('theme', cfg.theme ?? 'light');
  setChk('show_instruction_strip', !!cfg.show_instruction_strip);
  setChk('show_state_dots', !!cfg.show_state_dots);

  setChk('suggestions_enabled', !!cfg.suggestions_enabled);
  setVal('suggestions_max_items', cfg.suggestions_max_items ?? 4);
  setVal('suggestions_max_words', cfg.suggestions_max_words ?? 7);

  setChk('nudges_enabled', !!cfg.nudges_enabled);
  setVal('nudge_delay_ms', cfg.nudge_delay_ms ?? 4200);
  setVal('nudge_backoff_after_ignored', cfg.nudge_backoff_after_ignored ?? 2);

  setVal('ws_ping_interval_ms', cfg.ws_ping_interval_ms ?? 25000);
  setVal('ws_idle_timeout_ms', cfg.ws_idle_timeout_ms ?? 30000);

  // Flow
  setVal('tm_summarize_next_actions', cfg.tm_summarize_next_actions ?? 0.5);
  setVal('tm_check_understanding',   cfg.tm_check_understanding   ?? 0.5);
  setVal('tm_deep_dive',             cfg.tm_deep_dive             ?? 0.5);
  setVal('confirm_ms', cfg.confirm_ms ?? 420);
  setVal('echo_threshold_boost', cfg.echo_threshold_boost ?? 1.9);
  setVal('min_speech_ms', cfg.min_speech_ms ?? 200);

  // Vendor/VAD
  setVal('deepgram_model', cfg.deepgram_model ?? 'nova-3');
  setVal('deepgram_language', cfg.deepgram_language ?? 'en');
  setChk('deepgram_smart_format', !!cfg.deepgram_smart_format);
  setVal('deepgram_listen_url', cfg.deepgram_listen_url ?? 'wss://api.deepgram.com/v1/listen');
  setVal('deepgram_encoding', cfg.deepgram_encoding ?? 'opus');
  setVal('deepgram_sample_rate', cfg.deepgram_sample_rate ?? 48000);
  setChk('deepgram_interim_results', !!cfg.deepgram_interim_results);

  setVal('vad_attack_ms', cfg.vad_attack_ms ?? 12);
  setVal('vad_release_ms', cfg.vad_release_ms ?? 240);
  setVal('vad_dbfs_threshold', cfg.vad_dbfs_threshold ?? -42);

  // PTM
  setVal('openai_model', cfg.openai_model ?? 'gpt-4o-mini');
  setVal('gen_temperature', cfg.gen_temperature ?? 0.3);
  setVal('gen_top_p', cfg.gen_top_p ?? 1.0);
  setVal('gen_max_sentences', cfg.gen_max_sentences ?? 4);
  setVal('gen_target_verbosity', cfg.gen_target_verbosity ?? 'medium');

  setVal('nebraska_persona_level', cfg.nebraska_persona_level ?? 0.13);
  setChk('nebraska_quotes_enabled', cfg.nebraska_quotes_enabled ?? true);

  setChk('nlu_intent', cfg.nlu_intent ?? true);
  setChk('nlu_sentiment', cfg.nlu_sentiment ?? true);
  setChk('nlp_memory_blend', cfg.nlp_memory_blend ?? true);
  setVal('nlg_summary_pref', cfg.nlg_summary_pref ?? 'auto');
}

function setVal(id, v){ const el=document.getElementById(id); if(el) el.value = v; }
function setChk(id, v){ const el=document.getElementById(id); if(el) el.checked = !!v; }
function num(id){ const el=document.getElementById(id); return el ? Number(el.value) : undefined; }
function chk(id){ const el=document.getElementById(id); return el ? !!el.checked : undefined; }
function val(id){ const el=document.getElementById(id); return el ? el.value : undefined; }

async function saveUX(){
  const updates = {
    theme: val('theme'),
    show_instruction_strip: chk('show_instruction_strip'),
    show_state_dots: chk('show_state_dots'),

    suggestions_enabled: chk('suggestions_enabled'),
    suggestions_max_items: num('suggestions_max_items'),
    suggestions_max_words: num('suggestions_max_words'),

    nudges_enabled: chk('nudges_enabled'),
    nudge_delay_ms: num('nudge_delay_ms'),
    nudge_backoff_after_ignored: num('nudge_backoff_after_ignored'),

    ws_ping_interval_ms: num('ws_ping_interval_ms'),
    ws_idle_timeout_ms: num('ws_idle_timeout_ms'),
  };
  await apiPost('/api/v1/admin/config', { updates });
  alert('Saved UX');
}

async function saveFlow(){
  const updates = {
    tm_summarize_next_actions: Number(val('tm_summarize_next_actions')),
    tm_check_understanding:   Number(val('tm_check_understanding')),
    tm_deep_dive:             Number(val('tm_deep_dive')),

    confirm_ms: num('confirm_ms'),
    echo_threshold_boost: Number(val('echo_threshold_boost')),
    min_speech_ms: num('min_speech_ms'),
  };
  await apiPost('/api/v1/admin/config', { updates });
  alert('Saved Flow');
}

async function saveVendor(){
  const updates = {
    deepgram_model: val('deepgram_model'),
    deepgram_language: val('deepgram_language'),
    deepgram_smart_format: chk('deepgram_smart_format'),
    deepgram_listen_url: val('deepgram_listen_url'),
    deepgram_encoding: val('deepgram_encoding'),
    deepgram_sample_rate: num('deepgram_sample_rate'),
    deepgram_interim_results: chk('deepgram_interim_results'),

    vad_attack_ms: num('vad_attack_ms'),
    vad_release_ms: num('vad_release_ms'),
    vad_dbfs_threshold: num('vad_dbfs_threshold'),
  };

  // Validation
  if(!updates.deepgram_listen_url.startsWith('wss://')) return alert('listen_url must start with wss://');
  if(updates.deepgram_encoding !== 'opus') return alert("encoding must be 'opus'");
  if(updates.deepgram_sample_rate !== 48000) return alert('sample_rate must be 48000');

  await apiPost('/api/v1/admin/config', { updates });
  alert('Saved Vendor/VAD');
}

async function savePTM(){
  const updates = {
    openai_model: val('openai_model'),
    gen_temperature: Number(val('gen_temperature')),
    gen_top_p: Number(val('gen_top_p')),
    gen_max_sentences: Number(val('gen_max_sentences')),
    gen_target_verbosity: val('gen_target_verbosity'),

    nebraska_persona_level: Number(val('nebraska_persona_level')),
    nebraska_quotes_enabled: chk('nebraska_quotes_enabled'),

    nlu_intent: chk('nlu_intent'),
    nlu_sentiment: chk('nlu_sentiment'),
    nlp_memory_blend: chk('nlp_memory_blend'),
    nlg_summary_pref: val('nlg_summary_pref'),
  };
  await apiPost('/api/v1/admin/config', { updates });
  alert('Saved PTM');
}

// ---- Diagnostics wiring (with timeout so it never hangs)
async function runDiagnostics(){
  const statusEl = document.getElementById('diag-status');
  const bodyEl   = document.getElementById('diag-body');
  statusEl.textContent = 'running...';
  bodyEl.innerHTML = '';

  try {
    await apiGet('/api/v1/admin/diagnostics'); // quick presence check
    const ac = new AbortController();
    const tm = setTimeout(()=>ac.abort(), 10000);

    const r = await fetch('/api/v1/admin/diagnostics/run', {method:'POST', credentials:'include', signal: ac.signal});
    clearTimeout(tm);
    if(!r.ok) throw new Error(await r.text());
    const j = await r.json();
    (j.results||[]).forEach(row=>{
      const tr=document.createElement('tr');
      tr.innerHTML = `<td>${row.name}</td><td>${row.ok ? '✅' : '❌'}</td><td>${row.details||''}</td>`;
      bodyEl.appendChild(tr);
    });
    statusEl.textContent = 'done';
  } catch(e) {
    statusEl.textContent = 'error';
    const tr=document.createElement('tr');
    tr.innerHTML = `<td>diagnostics</td><td>❌</td><td>${(e && e.message) || e}</td>`;
    document.getElementById('diag-body').appendChild(tr);
  }
}

// ---- Wire buttons
document.getElementById('save-ux').addEventListener('click', ()=>saveUX().catch(e=>alert(e)));
document.getElementById('save-flow').addEventListener('click', ()=>saveFlow().catch(e=>alert(e)));
document.getElementById('save-vendor').addEventListener('click', ()=>saveVendor().catch(e=>alert(e)));
document.getElementById('save-ptm').addEventListener('click', ()=>savePTM().catch(e=>alert(e)));
document.getElementById('run-diag').addEventListener('click', ()=>runDiagnostics().catch(e=>alert(e)));

// ---- Bootstrap
loadConfig().catch(e=>alert(e));
