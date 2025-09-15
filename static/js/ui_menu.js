// AskChip menu (Admin, Call Log, Profile, Logout)
(function(){
  function ready(fn){ if(document.readyState!=='loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }
  ready(function(){
    const btn=document.getElementById('menuBtn'); if(!btn) return;
    let open=false, menu=null;

    // Removed the legacy top-level "Diagnostics" item.
    const items=[
      { label:'Admin', href:'/admin' },
      { label:'Call Log', action:()=>window.open('/api/v1/admin/logs-ui','_blank') },
      { label:'Profile', href:'/profile' },
      { label:'Logout', action:()=>{ fetch('/api/v1/auth/logout',{method:'POST',credentials:'include'}).finally(()=>location.href='/'); } }
    ];

    btn.addEventListener('click', ()=>{
      if(!menu){
        menu=document.createElement('div');
        menu.className='askchip-menu';
        menu.innerHTML=items
          .map(it=>`<button class="menu-item" data-href="${it.href||''}" data-idx="${items.indexOf(it)}">${it.label}</button>`)
          .join('');
        document.body.appendChild(menu);
        menu.addEventListener('click', (e)=>{
          const b=e.target.closest('.menu-item'); if(!b) return;
          const href=b.getAttribute('data-href');
          const idx=+b.getAttribute('data-idx');
          if(href) location.href=href; else items[idx].action?.();
          hide();
        });
      }
      open? hide() : show();
    });

    function show(){
      const r=btn.getBoundingClientRect();
      menu.style.position='absolute';
      menu.style.top=(r.bottom+6)+'px';
      menu.style.right=(window.innerWidth-r.right+6)+'px';
      menu.style.background='#0f141d';
      menu.style.border='1px solid #202533';
      menu.style.borderRadius='10px';
      menu.style.padding='6px';
      menu.style.zIndex='1500';
      menu.style.boxShadow='0 6px 20px rgba(0,0,0,.4)';
      menu.style.minWidth='160px';
      menu.querySelectorAll('.menu-item').forEach(b=>{
        b.style.display='block';
        b.style.width='100%';
        b.style.background='transparent';
        b.style.border='1px solid transparent';
        b.style.color='#e6e9ef';
        b.style.textAlign='left';
        b.style.padding='8px 10px';
        b.style.borderRadius='8px';
        b.onmouseenter=()=>b.style.background='#111823';
        b.onmouseleave=()=>b.style.background='transparent';
      });
      open=true;
    }

    function hide(){ if(menu) menu.style.top='-9999px'; open=false; }
    document.addEventListener('click', (e)=>{ if(open && !e.target.closest('#menuBtn') && !e.target.closest('.askchip-menu')) hide(); });
  });
})();
