function $id(id){return document.getElementById(id);} function $qs(s){return document.querySelector(s);}
const tbody = (function(){ const t=document.getElementById('results'); return t.querySelector('tbody'); })();
function addRow(i,name,status,details){ const tr=document.createElement('tr'); tr.innerHTML=`<td>${i}</td><td>${name}</td><td class="status ${status}">${status.toUpperCase()}</td><td>${details||''}</td>`; tbody.appendChild(tr); }
async function run(){ let i=0; $id('overall').textContent='Running checks…'; try{
  // CSRF warmup
  try{ await fetch('/api/v1/csrf', { credentials:'include' }); }catch{}
  // Checks
  const checks=[
    ['Greet endpoint', async()=>{ const r=await fetch('/api/v1/greet'); return [r.ok?'pass':'fail','HTTP '+r.status]; }],
    ['Admin SSE', async()=>{ const r=await fetch('/api/v1/admin/logs',{credentials:'include'}); return [r.ok?'pass':'warn','HTTP '+r.status]; }],
    ['Chat POST (CSRF)', async()=>{ const t=await fetch('/api/v1/csrf',{credentials:'include'}).then(r=>r.headers.get('X-CSRF-Token')); const r=await fetch('/api/v1/chat',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':t},body:JSON.stringify({text:'diag chat','session_id':localStorage.getItem('chip.sid')||''})}); return [r.ok?'pass':'fail','HTTP '+r.status]; }],
    ['STT POST (probe)', async()=>{ const t=await fetch('/api/v1/csrf',{credentials:'include'}).then(r=>r.headers.get('X-CSRF-Token')); const fd=new FormData(); fd.append('file', new Blob(['dummy'],{type:'audio/webm'}),'a.webm'); fd.append('meta', JSON.stringify({session_id:localStorage.getItem('chip.sid')||'',language:'en'})); const r=await fetch('/api/v1/voice/stt',{method:'POST',credentials:'include',headers:{'X-CSRF-Token':t},body:fd}); return [r.status===403?'warn':(r.ok?'pass':'warn'),'HTTP '+r.status]; }],
  ];
  for(const c of checks){ const [name,fn]=c; try{ const [s,d]=await fn(); addRow(++i,name,s,d);}catch(e){ addRow(++i,name,'fail',String(e)); } }
  $id('overall').textContent='Done.';
}catch(e){ $id('overall').textContent='Diagnostic failed: '+e.message; }}
document.addEventListener('DOMContentLoaded', ()=>{ document.getElementById('runBtn').onclick=run; });
