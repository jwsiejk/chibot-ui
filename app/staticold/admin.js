async function loadAdmin() {
  const r = await fetch('/api/v1/admin/config');
  const j = await r.json();
  if (!j.ok) return alert('Failed to load config');
  const s = j.settings, v = j.vendors;
  const g = (id)=>document.getElementById(id);
  if (g('feature_audio')) g('feature_audio').checked = !!s.feature_audio;
  if (g('tts_voice_id')) g('tts_voice_id').value = s.tts_voice_id || '';
  if (g('tts_output_format')) g('tts_output_format').value = s.tts_output_format || 'mp3_44100_128';
  if (g('tts_model_id')) g('tts_model_id').value = s.tts_model_id || 'eleven_multilingual_v2';
  if (g('vendor_llm')) g('vendor_llm').textContent = v.llm;
  if (g('vendor_stt')) g('vendor_stt').textContent = v.stt;
  if (g('vendor_tts')) g('vendor_tts').textContent = v.tts.provider;
  if (g('vendor_tts_key')) g('vendor_tts_key').textContent = v.tts.key_present ? 'Key: present' : 'Key: missing';
}
async function saveAdmin() {
  const b = {
    feature_audio: document.getElementById('feature_audio')?.checked || false,
    tts_voice_id: document.getElementById('tts_voice_id')?.value || '',
    tts_output_format: document.getElementById('tts_output_format')?.value || 'mp3_44100_128',
    tts_model_id: document.getElementById('tts_model_id')?.value || 'eleven_multilingual_v2'
  };
  const r = await fetch('/api/v1/admin/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)});
  const j = await r.json();
  if (!j.ok) return alert('Failed to save config');
  alert('Saved');
}
window.addEventListener('DOMContentLoaded', loadAdmin);


async function startTest(mode) {
  const r = await fetch('/api/v1/admin/test-runs', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode})});
  const j = await r.json();
  if (!j.ok) return alert('Failed to start test');
  const id = j.id;
  const logPanel = document.getElementById('test_log_panel');
  const pre = document.getElementById('test_log');
  const idSpan = document.getElementById('test_id');
  const statusSpan = document.getElementById('test_status');
  const jsonLink = document.getElementById('test_json_link');
  pre.textContent = '';
  idSpan.textContent = id;
  statusSpan.textContent = 'running';
  jsonLink.href = '/api/v1/admin/test-runs/' + id + '/json';
  logPanel.style.display = 'block';

  const es = new EventSource('/api/v1/admin/test-runs/' + id + '/sse');
  es.onmessage = (ev) => {
    try {
      const items = JSON.parse(ev.data);
      for (const it of items) {
        const ts = new Date(it.ts * 1000).toISOString();
        pre.textContent += `[${ts}] ${it.kind}: ${it.msg}` + (Object.keys(it).length>2 ? ' ' + JSON.stringify(it) : '') + '\n';
      }
      pre.parentElement.scrollTop = pre.parentElement.scrollHeight;
    } catch (e) {}
  };
  es.onerror = () => { es.close(); }
  // Poll status finish
  const poll = setInterval(async () => {
    const r2 = await fetch('/api/v1/admin/test-runs/' + id);
    const j2 = await r2.json();
    if (j2.ok) {
      statusSpan.textContent = j2.item.status;
      if (j2.item.status === 'ok' || j2.item.status === 'fail') {
        clearInterval(poll); es.close();
      }
    }
  }, 600);
}

window.addEventListener('DOMContentLoaded', () => {
  const b1 = document.getElementById('btn_test_voice');
  const b2 = document.getElementById('btn_test_chat');
  if (b1) b1.addEventListener('click', () => startTest('voice'));
  if (b2) b2.addEventListener('click', () => startTest('chat'));
});
