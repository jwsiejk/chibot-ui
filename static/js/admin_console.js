
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
    'feature_audio','tts_voice_id','tts_output_format','tts_model_id',
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
    'feature_audio','tts_voice_id','tts_output_format','tts_model_id',
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


async function startTest(mode){
  const r = await fetch('/api/v1/admin/test-runs', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'include', body: JSON.stringify({mode})});
  const j = await r.json();
  if(!j.ok){ toast('Failed to start test'); return; }
  const id = j.id;
  const panel = document.getElementById('test_log_panel');
  const pre = document.getElementById('test_log');
  const idSpan = document.getElementById('test_id');
  const st = document.getElementById('test_status');
  const link = document.getElementById('test_json_link');
  pre.textContent = ''; idSpan.textContent = id; st.textContent = 'running'; link.href = '/api/v1/admin/test-runs/'+id+'/json';
  panel.style.display = 'block';
  const es = new EventSource('/api/v1/admin/test-runs/'+id+'/sse');
  es.onmessage = ev => {
    try{
      const arr = JSON.parse(ev.data);
      for(const it of arr){
        const ts = new Date(it.ts*1000).toISOString();
        const step = (it.step!=null? String(it.step).padStart(4,'0') : '----');
        const label = it.label || (it.kind + (it.route ? (' – '+it.route) : ''));
        const extras = [];
        for (const k of ['n','audio_chunks','viseme_sets','chars','turn_id','text','msg','error']){
          if (it[k] != null){
            let v = String(it[k]);
            if (k === 'turn_id') v = v.slice(0,8)+'…';
            if (k === 'text') v = v.slice(0,140);
            extras.push(k + '=' + v);
          }
        }
        pre.textContent += `[${ts}] [${step}] ${label}` + (extras.length ? '  —  ' + extras.join('  ') : '') + '\n';
      }
      panel.scrollTop = panel.scrollHeight;
    }catch(e){}
  };
  es.onerror = ()=>{ es.close(); };
  const h = setInterval(async ()=>{
    const r2 = await fetch('/api/v1/admin/test-runs/'+id);
    const j2 = await r2.json();
    if(j2.ok){ st.textContent = j2.item.status; if(j2.item.status==='ok'||j2.item.status==='fail'){clearInterval(h); es.close();} }
  }, 800);
}
document.getElementById('btn_test_voice')?.addEventListener('click', ()=>startTest('voice'));
document.getElementById('btn_test_chat')?.addEventListener('click', ()=>startTest('chat'));
