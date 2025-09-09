
const tabs = document.querySelectorAll('.tabs button');
const panels = document.querySelectorAll('.panel');
tabs.forEach(b=> b.addEventListener('click', ()=>{
  tabs.forEach(x=>x.classList.remove('active'));
  panels.forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById(b.dataset.tab).classList.add('active');
}));

function toast(msg){ const t=document.getElementById('toast'); t.textContent=msg; t.style.display='block'; setTimeout(()=>t.style.display='none', 1400); }

function mapFields(cfg){
  const ids = [
    'show_instruction_strip','show_state_dots','theme',
    'suggestions_enabled','suggestions_max_items','suggestions_max_words',
    'min_speech_ms','confirm_ms','echo_threshold_boost','language_lock',
    'nudges_enabled','nudge_delay_ms','nudge_backoff_after_ignored',
    'max_turn_seconds',
    'nebraska_persona_level','nebraska_quotes_enabled','gen_humor','gen_humor','gen_target_verbosity','gen_target_verbosity','gen_max_sentences','gen_max_sentences','gen_top_p','gen_top_p','gen_temperature','gen_temperature'
  ];
  for(const id of ids){
    const el = document.getElementById(id);
    if(!el) continue;
    const v = cfg[id];
    if(el.type === 'checkbox'){ el.checked = !!v; }
    else if(el.tagName === 'SELECT'){ el.value = v ?? el.value; }
    else { el.value = (v !== undefined && v !== null) ? v : el.value; }
  }
}

function collectUpdates(){
  const out = {};
  const ids = [
    'show_instruction_strip','show_state_dots','theme',
    'suggestions_enabled','suggestions_max_items','suggestions_max_words',
    'min_speech_ms','confirm_ms','echo_threshold_boost','language_lock',
    'nudges_enabled','nudge_delay_ms','nudge_backoff_after_ignored',
    'max_turn_seconds',
    'nebraska_persona_level','nebraska_quotes_enabled','gen_humor','gen_humor','gen_target_verbosity','gen_target_verbosity','gen_max_sentences','gen_max_sentences','gen_top_p','gen_top_p','gen_temperature','gen_temperature'
  ];
  for(const id of ids){
    const el = document.getElementById(id);
    if(!el) continue;
    if(el.type === 'checkbox') out[id] = !!el.checked;
    else if(el.tagName === 'SELECT') out[id] = el.value;
    else out[id] = Number.isFinite(+el.value) ? +el.value : el.value;
  }
  return out;
}

async function loadCfg(){
  const cfg = await fetch('/api/v1/admin/config', {credentials:'include'}).then(r=>r.json()).catch(()=>null);
  if(cfg && cfg.ok) mapFields(cfg.config || {});
}

async function saveCfg(){
  const updates = collectUpdates();
  const r = await fetch('/api/v1/admin/config', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    credentials:'include',
    body: JSON.stringify({ updates })
  });
  if(r.ok){ toast('Saved'); } else { toast('Save failed'); }
}

document.getElementById('save')?.addEventListener('click', saveCfg);
document.getElementById('reload')?.addEventListener('click', loadCfg);
document.getElementById('openLog')?.addEventListener('click', ()=>window.dispatchEvent(new CustomEvent('ac:open-admin-log')));
document.getElementById('openLog2')?.addEventListener('click', ()=>window.dispatchEvent(new CustomEvent('ac:open-admin-log')));

loadCfg();
