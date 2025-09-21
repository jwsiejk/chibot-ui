// static/js/ui_menu.js — Production-ready AskChip menu controller
// - Idempotent wiring (safe on hot reloads / duplicate imports)
// - Accessible (aria-expanded, Escape/outside click, arrow keys)
// - Admin gating via /api/v1/auth/me + /api/v1/admin/config (200 => show Admin)
// - Profile opens modal if present; Logout best-effort call then reload

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

if (!window.__askchip_menu_wired) {
  window.__askchip_menu_wired = true;

  const btn      = $('#menuAskChip');     // toggle button
  const menu     = $('#menuDropdown');    // container
  const adminItm = $('#menuAdmin');
  const profItm  = $('#menuProfile');
  const outItm   = $('#menuLogout');

  // If markup not present, bail quietly (no console spam in prod)
  if (!btn || !menu) {
    // Flag for debugging if needed
    window.__askchip_menu_missing = true;
  } else {
    // ---- State helpers ----
    const openMenu = () => {
      menu.classList.remove('hidden');
      btn.setAttribute('aria-expanded', 'true');
      // focus first visible item for keyboard users
      const itms = $$('#menuDropdown [role="menuitem"]')
        .filter(n => n.offsetParent !== null);
      if (itms.length) itms[0].focus();
    };
    const closeMenu = () => {
      if (menu.classList.contains('hidden')) return;
      menu.classList.add('hidden');
      btn.setAttribute('aria-expanded', 'false');
      // return focus to the button for a11y
      btn.focus({ preventScroll: true });
    };
    const toggleMenu = () => {
      if (menu.classList.contains('hidden')) openMenu(); else closeMenu();
    };

    // ---- Click binding (idempotent) ----
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleMenu();
    }, { passive: true });

    // Close on outside click
    document.addEventListener('click', (e) => {
      if (menu.classList.contains('hidden')) return;
      const inside = e.target === btn || menu.contains(e.target);
      if (!inside) closeMenu();
    });

    // Keyboard navigation
    btn.addEventListener('keydown', (e) => {
      const k = e.key;
      if (k === ' ' || k === 'Enter' || k === 'ArrowDown') {
        e.preventDefault();
        openMenu();
      } else if (k === 'Escape') {
        e.preventDefault();
        closeMenu();
      }
    });
    menu.addEventListener('keydown', (e) => {
      const items = $$('#menuDropdown [role="menuitem"]')
        .filter(n => n.offsetParent !== null);
      if (!items.length) return;
      const idx = items.indexOf(document.activeElement);
      if (e.key === 'Escape') {
        e.preventDefault();
        closeMenu();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        const next = items[(idx + 1 + items.length) % items.length];
        next?.focus();
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        const prev = items[(idx - 1 + items.length) % items.length];
        prev?.focus();
        return;
      }
      if (e.key === 'Home') {
        e.preventDefault();
        items[0]?.focus();
        return;
      }
      if (e.key === 'End') {
        e.preventDefault();
        items[items.length - 1]?.focus();
        return;
      }
    });

    // ---- Menu item actions (safe, best effort) ----
    if (profItm) {
      profItm.addEventListener('click', (e) => {
        e.preventDefault();
        closeMenu();
        // Show profile modal if present
        const modal = $('#profileModal');
        if (modal) modal.classList.remove('hidden');
        // Inform others
        window.dispatchEvent(new CustomEvent('askchip-open-profile'));
      });
    }

    if (outItm) {
      outItm.addEventListener('click', async (e) => {
        e.preventDefault();
        closeMenu();
        // Try logout endpoint; fall back to window.location
        try {
          const r = await fetch('/api/v1/auth/logout', {
            method: 'POST', credentials: 'include',
            headers: {'Content-Type': 'application/json'}
          });
          if (r.ok) {
            window.location.reload();
          } else {
            // fallback route if present
            window.location.href = '/logout';
          }
        } catch {
          window.location.reload();
        }
      });
    }

    // ---- Admin gating (only show Admin if really allowed) ----
    const hideAdmin = () => { if (adminItm) adminItm.style.display = 'none'; };
    const showAdmin = () => { if (adminItm) adminItm.style.display = ''; };

    // Start hidden until proven allowed
    hideAdmin();

    (async () => {
      try {
        // 1) Confirm logged-in identity (not strictly needed for gating, but useful for future)
        await fetch('/api/v1/auth/me', { credentials: 'include' }).then(()=>{});
      } catch {
        // Not logged in → keep Admin hidden
      }
      try {
        // 2) Probe admin config; 200 => user is admin & feature flag present
        const r = await fetch('/api/v1/admin/config', { credentials: 'include' });
        if (r.ok) {
          const j = await r.json().catch(()=> ({}));
          const enabled = !!(j && j.settings && (
            j.settings.FEATURE_ADMIN_UI === true ||
            j.settings.feature_admin_ui === true ||
            j.settings.feature_audio !== undefined // presence of settings implies admin page available
          ));
          if (enabled) showAdmin(); else hideAdmin();
        } else {
          // 403/404 -> not admin or feature disabled
          hideAdmin();
        }
      } catch {
        hideAdmin();
      }
    })();

    // Expose small debug flag
    window.__askchip_menu_loaded = true;
  }
}
