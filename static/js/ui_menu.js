// static/js/ui_menu.js
(function(){
  const btn = document.getElementById("menuBtn");
  if (!btn) return;
  let open = false, menu;
  const items = [
    { label: "Admin",       href: "/admin" },
    { label: "Diagnostics", href: "/diagnostics" },
    { label: "Profile",     href: "/profile" },
    { label: "Logout",      action: "logout" }
  ];
  function build(){
    menu = document.createElement("div");
    menu.className = "askchip-menu";
    menu.setAttribute("role","menu");
    menu.innerHTML = items.map((it,i)=>`<button class="menu-item" data-idx="${i}" role="menuitem">${it.label}</button>`).join("");
    document.body.appendChild(menu);
  }
  function position(){
    const r = btn.getBoundingClientRect();
    menu.style.position = "absolute";
    menu.style.top = `${r.bottom + window.scrollY + 6}px`;
    menu.style.left= `${r.left   + window.scrollX}px`;
  }
  function toggle(){ if(!menu) build(); open=!open; menu.style.display=open?'block':'none'; if(open) position(); }
  function close(){ if(menu) menu.style.display='none'; open=false; }
  document.addEventListener("click",(e)=>{
    if(e.target===btn){ toggle(); return; }
    if(!menu) return;
    if(menu.contains(e.target)){
      const idx=Number(e.target.getAttribute("data-idx"));
      const it=items[idx];
      if(it?.action==='logout'){
        fetch('/api/v1/auth/logout',{method:'POST',credentials:'include'}).finally(()=>location.reload());
      } else if(it?.href){ location.href = it.href; }
      close();
    } else { close(); }
  });
  window.addEventListener("resize", ()=>{ if(open) position(); });
})();