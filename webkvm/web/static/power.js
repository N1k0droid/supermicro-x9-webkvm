/* Power menu shared by the dashboard and the KVM console.
 * Needs in the page: #power-menu (with .menu-list and [data-action] buttons, hidden by default),
 * #power-btn, the #confirm dialog (#confirm-title, #confirm-text, #confirm-yes, #confirm-no)
 * and the #toast element. Call setupPower(session, onDone) after loading /api/v1/session. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const t = (k, v) => window.I18N.t(k, v);
  const ACTIONS = ['on', 'soft', 'reset', 'cycle', 'off'];

  function toast(msg) {
    const el = $('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 4000);
  }

  async function send(session, action) {
    const r = await fetch('/api/v1/power', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf },
      body: JSON.stringify({ action }),
    });
    if (r.status === 401) { location.href = '/login'; throw new Error('login required'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function confirmPower(session, action, onDone) {
    if (!ACTIONS.includes(action)) return;
    const dlg = $('confirm');
    $('confirm-title').textContent = t(`power.confirm.${action}`);
    $('confirm-text').textContent = t(`power.confirm.${action}.text`);
    const yes = $('confirm-yes');
    yes.className = action === 'on' ? 'btn-primary' : 'btn-danger solid';
    yes.onclick = async () => {
      dlg.close();
      try {
        const r = await send(session, action);
        toast(t('power.sent', { r: r.result || action }));
        if (onDone) onDone(action);
      } catch (e) { toast(t('power.failed', { e: e.message })); }
    };
    $('confirm-no').onclick = () => dlg.close();
    dlg.showModal();
  }

  // keep an open drop-down inside the window (right-aligned by default, left-aligned
  // when the button sits near the left edge, e.g. on narrow screens)
  function fitMenu(menu) {
    const list = menu.querySelector('.menu-list');
    if (!list) return;
    list.style.left = ''; list.style.right = '';
    if (!menu.classList.contains('open')) return;
    const r = list.getBoundingClientRect();
    if (r.left < 8) { list.style.left = '0'; list.style.right = 'auto'; }
  }
  window.fitMenu = fitMenu;

  window.setupPower = function (session, onDone) {
    if (!session || !session.power_control) return;
    const menu = $('power-menu');
    const btn = $('power-btn');
    menu.hidden = false;
    btn.addEventListener('click', e => {
      e.stopPropagation();
      menu.classList.toggle('open');
      fitMenu(menu);
      btn.setAttribute('aria-expanded', menu.classList.contains('open'));
    });
    document.addEventListener('click', () => { menu.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); });
    menu.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => {
      menu.classList.remove('open');
      confirmPower(session, b.dataset.action, onDone);
    }));
  };
  window.showToast = toast;
})();
