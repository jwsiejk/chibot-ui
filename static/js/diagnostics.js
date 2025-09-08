async function __whoami(){ try{ const r=await fetch('/api/v1/auth/me',{credentials:'include'}); if(!r.ok) return {}; return await r.json(); }catch{ return {}; } }

window.addEventListener('error', (e)=>{ try{ document.getElementById('overall').textContent = 'Diagnostics error: ' + (e?.message||e); }catch{} });
function $id(id){ return document.getElementById(id); } function $qs(sel){ return document.querySelector(sel); }
const tpl = $id('row-tpl'); let tbody = $qs('#results tbody'); const overall = $id('overall');
function ensureTableSkeleton(){ if(!tbody){ const table=document.createElement('table'); table.id='results';
  table.innerHTML='<thead><tr><th>#</th><th>Check</th><th>Status</th><th>Details</th></tr></thead><tbody></tbody>'; document.body.appendChild(table); tbody=table.querySelector('tbody'); } }
function pick(node,a,b){ return node.querySelector(a)||node.querySelector(b); }
function addRow(i,name,status,details){ ensureTableSkeleton();
  if(!tpl||!('content'in tpl)){ const tr=document.createElement('tr'); tr.innerHTML='<td></td><td></td><td></td><td></td>';
    tr.children[0].textContent=i; tr.children[1].textContent=name; tr.children[2].textContent=(status||'').toUpperCase();
    tr.children[2].classList.add('status',status); tr.children[3].textContent=details||''; tbody.appendChild(tr); return; }
  const node=tpl.content.cloneNode(true); const elI=pick(node,'.idx','.i'); const elN=pick(node,'.name','.n'); const elS=pick(node,'.status','.s'); const elD=pick(node,'.details','.d');
  if(elI) elI.textContent=i; if(elN) elN.textContent=name; if(elS){ elS.textContent=(status||'').toUpperCase(); elS.classList.add('status',status);} if(elD) elD.textContent=details||''; tbody.appendChild(node); }
function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
async function run(){
  try{
    overall && (overall.textContent='Running checks…');
    const t=await fetch('/api/v1/auth/csrf',{credentials:'include'}).then(r=>r.json()).catch(()=>({})); const csrf=t.csrf;
    try{ await fetch('/api/v1/admin/diag/run',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},credentials:'include',body:JSON.stringify({source:'ui'})}); }catch{}
    const checks=[
      ['Greet endpoint', async()=>{ const res=await fetch('/api/v1/greet'); return [res.ok?'pass':'fail','HTTP '+res.status]; }],
      ['Admin SSE', async()=>{ const res=await fetch('/api/v1/admin/logs?probe=1',{credentials:'include'}); return [res.ok?'pass':'warn','HTTP '+res.status]; }],
      ['TTS POST (CSRF)', async()=>{ const res=await fetch('/api/v1/voice/tts-with-visemes',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},credentials:'include',body:JSON.stringify({text:'hello from diagnostics'})}); return [res.ok?'pass':'fail','HTTP '+res.status]; }],
      ['WS /ws/v1/chat', async()=>{ try{ const ws=new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws/v1/chat?session_id=diag-ui');
        const p=new Promise((resolve,reject)=>{ let done=false; ws.onmessage=(ev)=>{ try{ const msg=JSON.parse(ev.data); if(msg&&msg.type==='ready'){ done=true; resolve(['pass','ready']); ws.close(); } }catch{} };
          ws.onerror=()=>{ if(!done) reject(new Error('ws error')); }; ws.onclose=()=>{ if(!done) reject(new Error('ws close')); }; }); return await Promise.race([p, sleep(4000).then(()=>['fail','timeout'])]); }catch(e){ return ['fail', e.message||String(e)]; }}],
    ];
    let pass=0,fail=0; for(let i=0;i<checks.length;i++){ const [name,fn]=checks[i]; let status='fail',details='';
      try{ [status,details]=await fn(); }catch(e){ status='fail'; details=e.message||String(e); }
      addRow(i+1,name,status,details); if(status==='pass') pass++; if(status==='fail') fail++; }
    overall && (overall.textContent=`Pass: ${pass} • Fail: ${fail} • Warn/Skip: ${checks.length - pass - fail}`);
  }catch(e){ try{ overall && (overall.textContent='Diagnostics error: '+(e?.message||e)); }catch{} }
}
window.addEventListener('DOMContentLoaded', run);

async function __probeAdminSSE(){
  const me = await __whoami();
  const email = (me && me.email) ? me.email : null;
  const init = email ? { method:'GET', credentials:'include', headers:{ 'X-User-Email': email, 'Accept': 'text/event-stream' } }
                     : { method:'GET', credentials:'include', headers:{ 'Accept': 'text/event-stream' } };
  const ctrl = new AbortController();
  const t = setTimeout(()=>{ try{ ctrl.abort(); }catch(e){} }, 1500);
  try{
    const r = await fetch('/api/v1/admin/logs', { ...init, signal: ctrl.signal });
    clearTimeout(t);
    return r.status;
  }catch(e){
    clearTimeout(t);
    return 0;
  }
}

async function checkAdminSSE(){
  const max = 6;
  for (let i=0;i<max;i++){
    const s = await __probeAdminSSE();
    if (s === 200) return 200;
    if (s >= 500 || s === 0) { await new Promise(r=>setTimeout(r, Math.min(30000, 800*(2**i)))); continue; }
    return s;
  }
  return 503;
}
    return s;
  }
  return 503;
}
