// static/js/ui_menu.js (robust against missing elements/pages)
(function(){
  function ready(fn){ if (document.readyState !== 'loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }
  ready(function(){
    try{
      const btn = document.getElementById('menuBtn');
      if (!btn) return;                       // page doesn't have menu
      let open = false, menu = null;
      const items = [
  { label: 'Admin',       href: '/admin' },
  { label: 'Diagnostics', href: '/diagnostics' },
  { label: 'Call Log', action: 'call_log' },
  { label: 'Profile',     href: '/profile' },
  { label: 'Logout',      action: 'logout' }
];
      
function build(){
  if (menu) return menu;
  menu = document.createElement('div');
  menu.className = 'askchip-menu';
  menu.setAttribute('role','menu');

  const header = document.createElement('div');
  header.className = 'header';
  header.innerHTML = `<img src="/static/chip/img/chip.png" alt="Chip"/><span>Ask Chip</span>`;
  menu.appendChild(header);

  const icon = (name)=>{
    const map = {
      Admin: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 2l7 4v6c0 5-3.5 9-7 10-3.5-1-7-5-7-10V6l7-4zM7 10h10v2H7v-2z"/></svg>',
      Diagnostics: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M3 3h18v2H3V3zm2 4h14v14H5V7zm4 3v8h2v-8H9zm4 4v4h2v-4h-2z"/></svg>',
      "Call Log": '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M4 4h16v2H4V4zm0 4h16v10H4V8zm2 2v6h12v-6H6z"/></svg>',
      Profile: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 12a5 5 0 100-10 5 5 0 000 10zm-7 9a7 7 0 0114 0H5z"/></svg>',
      Logout: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M16 13v-2H7V8l-5 4 5 4v-3h9zm3-9H11v2h8v14h-8v2h8a2 2 0 002-2V6a2 2 0 00-2-2z"/></svg>'
    };
    return map[name] || '<svg viewBox="0 0 24 24" width="16" height="16"><circle cx="12" cy="12" r="8" fill="currentColor"/></svg>';
  };

  items.forEach((it, i)=>{
    // Divider between Diagnostics and Call Log for legibility
    if (i===2){ const d=document.createElement('div'); d.className='divider'; menu.appendChild(d); }
    const b = document.createElement('button');
    b.className = 'menu-item';
    b.setAttribute('role','menuitem');
    b.dataset.idx = String(i);
    b.innerHTML = `<span class="icon">${icon(it.label)}</span><span class="label">${it.label}</span>`;
    menu.appendChild(b);
  });

  document.body.appendChild(menu);
  return menu;
}

function position(){
  if (!menu) return;
  const r = btn.getBoundingClientRect();
  menu.style.position = 'absolute';
  const x = (r.right + window.scrollX) - (menu.offsetWidth || 240);
  menu.style.top  = (r.bottom + window.scrollY + 10) + 'px';
  menu.style.left = Math.max(8, x) + 'px';
}
function toggle(){
        build();
        open = !open;
        menu.style.display = open ? 'block' : 'none';
        if (open) position();
      }
      function close(){ if (menu) menu.style.display = 'none'; open = false; }
      document.addEventListener('click', (e) => {
        if (e.target === btn){ toggle(); return; }
        if (!menu) return;
        if (menu.contains(e.target)){
          const idx = Number(e.target.closest('.menu-item')?.dataset.idx || -1);
          const it  = items[idx];
          if (it?.action === 'logout'){
            fetch('/api/v1/auth/logout', { method:'POST', credentials:'include' }).finally(()=>location.reload());
          } else if (it?.href){
            location.href = it.href;
          }
          close();
        } else {
          close();
        }
      });
      window.addEventListener('resize', ()=>{ if (open) position(); });
    }catch(e){ /* swallow menu errors to avoid breaking page */ }
  });
})();
(function(){document.addEventListener('DOMContentLoaded',()=>{try{const b=document.getElementById('menuBtn'); if(b) b.textContent='AskChip';}catch(e){}});})();
