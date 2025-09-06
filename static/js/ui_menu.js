
(() => {
  const menu = document.getElementById('askChipMenu');
  const btn  = document.getElementById('menuButton');
  const dd   = document.getElementById('menuDropdown');
  const adminItems = dd.querySelectorAll('[data-admin-only]');

  const close = (e) => { if (!menu.contains(e.target)) menu.classList.remove('open'); };
  btn?.addEventListener('click', () => menu.classList.toggle('open'));
  document.addEventListener('click', close);

  fetch('/api/v1/admin/logs', {credentials:'include'}).then(r => {
    if (r.ok) adminItems.forEach(el => el.style.display='block');
    else adminItems.forEach(el => el.style.display='none');
  }).catch(() => adminItems.forEach(el => el.style.display='none'));

  document.getElementById('menuProfile')?.addEventListener('click', () => { window.location.href = '/profile'; });
  document.getElementById('menuAdmin')?.addEventListener('click', () => { window.location.href = '/admin'; });
  document.getElementById('menuDiagnostics')?.addEventListener('click', () => { window.location.href = '/diagnostics'; });
  document.getElementById('menuLogout')?.addEventListener('click', async () => {
    try{ await fetch('/api/v1/auth/logout', {method:'POST', credentials:'include'});}catch(e){}
    window.location.href = '/';
  });
})();
