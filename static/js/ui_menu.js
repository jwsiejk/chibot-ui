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
  { label: 'Real Time Log', action: 'realtime_log' },
  { label: 'Profile',     href: '/profile' },
  { label: 'Logout',      action: 'logout' }
];
      function build(){
        if (menu) return menu;
        menu = document.createElement('div');
        menu.className = 'askchip-menu';
        menu.setAttribute('role','menu');
        // Build buttons
        for (let i=0;i<items.length;i++){
          const b = document.createElement('button');
          b.className = 'menu-item';
          b.setAttribute('role','menuitem');
          b.dataset.idx = String(i);
          b.textContent = items[i].label;
          menu.appendChild(b);
        }
        document.body.appendChild(menu);
        return menu;
      }
      function position(){
        if (!menu) return;
        const r = btn.getBoundingClientRect();
        menu.style.position = 'absolute';
        menu.style.top  = (r.bottom + window.scrollY + 6) + 'px';
        menu.style.left = (r.left   + window.scrollX) + 'px';
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
