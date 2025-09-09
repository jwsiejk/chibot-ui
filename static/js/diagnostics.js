
function $id(id){ return document.getElementById(id); }
function $qs(sel){ return document.querySelector(sel); }
const overall = $id('overall'); let tbody = $qs('#results tbody');

function ensureTableSkeleton(){
  if(!tbody){
    const t = document.createElement('table'); t.id='results'; t.innerHTML='<thead><tr><th>#</th><th>Check</th><th>Status</th><th>Details</th></tr></thead><tbody></tbody>';
    document.body.appendChild(t); tbody = t.querySelector('tbody');
  }
}
function addRow(i,name,status,details){
  ensureTableSkeleton();
  const tr=document.createElement('tr');
  tr.innerHTML = `<td>${i}</td><td>${name}</td><td class="status ${status}">${(status||'').toUpperCase()}</td><td>${details||''}</td>`;
  tbody.appendChild(tr);
}

async function run(){
  try{
    overall && (overall.textContent='Running checks…');
    // Warm up CSRF
    try{ await fetch('/api/v1/auth/csrf', { credentials:'include' }); }catch{}

    const checks = [
      ['Greet endpoint', async()=>{ const r=await fetch('/api/v1/greet'); return [r.ok?'pass':'fail', 'HTTP '+r.status]; }],
      ['Admin SSE', async()=>{ const r=await fetch('/api/v1/admin/logs', { credentials:'include' }); return [r.ok?'pass':'warn', 'HTTP '+r.status]; }],
      ['TTS POST (CSRF)', async()=>{
        const tok = await fetch('/api/v1/csrf', { credentials:'include' }).then(r=>r.headers.get('X-CSRF-Token')).catch(()=>null);
        const r = await fetch('/api/v1/voice/tts-with-visemes', { method:'POST', credentials:'include', headers:{'Content-Type':'application/json', ...(tok?{'X-CSRF-Token':tok}:{})}, body: JSON.stringify({ text:'ping' }) });
        return [r.ok?'pass':'fail','HTTP '+r.status];
      }],
      ['WS /ws/v1/chat', async()=>{
        try{
          const proto = location.protocol === 'https:' ? 'wss' : 'ws';
          const ws = new WebSocket(`${proto}://${location.host}/ws/v1/chat?session_id=diag-ui`);
          const p = new Promise((resolve,reject)=>{
            let done=false;
            ws.onopen = ()=>{ if(!done){ done=true; resolve(['pass','ready']); ws.close(); } };
            ws.onerror = ()=>{ if(!done){ done=true; resolve(['fail','ws error']); } };
          });
          const res = await p; return res;
        }catch(e){ return ['fail', e.message||String(e)]; }
      }],
    ];

    let pass=0, fail=0;
    for(let i=0;i<checks.length;i++){
      const [name,fn] = checks[i];
      let status='fail', details='';
      try{ [status,details] = await fn(); }catch(e){ status='fail'; details = e && e.message || String(e); }
      addRow(i+1, name, status, details);
      if(status==='pass') pass++;
      if(status==='fail') fail++;
    }
    overall && (overall.textContent = `Pass: ${pass} • Fail: ${fail} • Warn/Skip: ${checks.length - pass - fail}`);
  }catch(e){
    try{ overall && (overall.textContent='Diagnostics error: '+(e && e.message || String(e))); }catch{}
  }
}
run();
