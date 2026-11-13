import RFB from '/novnc/core/rfb.js';

const $ = id => document.getElementById(id);
const t = (k, v) => window.I18N.t(k, v);
const screen = $('screen');
let rfb = null;
let scaled = true;
let retries = 0;
let retryTimer = null;

// X11 keysym + XT scancode (0xE0xx = extended key, as in noVNC)
const K = {
  ctrl: [0xffe3, 0x1d], alt: [0xffe9, 0x38], del: [0xffff, 0xe053], bksp: [0xff08, 0x0e],
  f1: [0xffbe, 0x3b], f2: [0xffbf, 0x3c], f7: [0xffc4, 0x41], f11: [0xffc8, 0x57], f12: [0xffc9, 0x58],
  win: [0xffeb, 0xe05b], prtsc: [0xff61, 0xe037],
};
const CODE = {
  ctrl: 'ControlLeft', alt: 'AltLeft', del: 'Delete', bksp: 'Backspace', f1: 'F1', f2: 'F2', f7: 'F7',
  f11: 'F11', f12: 'F12', win: 'MetaLeft', prtsc: 'PrintScreen',
};

function overlay(title, text, retry) {
  const ov = $('overlay');
  if (!title) { ov.hidden = true; return; }
  ov.hidden = false;
  $('ov-title').textContent = title;
  $('ov-text').textContent = text || '';
  $('ov-retry').hidden = !retry;
}

let viewerConnected = false;

function status(text) {
  const el = $('status');
  el.textContent = text;
  el.title = '';
  el.classList.remove('status-warn');
}

// The WebSocket to the gateway can stay open while the session to the BMC has dropped:
// the picture would then just freeze. The real state comes from /api/v1/kvm.
async function pollBmc() {
  if (viewerConnected) {
    try {
      const r = await fetch('/api/v1/kvm', { credentials: 'same-origin' });
      if (r.ok) {
        const s = await r.json();
        const el = $('status');
        if (s.state === 'connected' && s.signal === false) {
          status(t('kvmp.st.no_signal'));
          el.classList.add('status-warn');
        } else if (s.state === 'connected') {
          status(s.width ? t('kvmp.st.connected_res', { w: s.width, h: s.height }) : t('kvmp.st.connected'));
        } else {
          const what = s.state === 'retrying' ? t('kvmp.st.bmc_down') : t('kvmp.st.bmc_connecting');
          el.textContent = s.last_error ? `${what} (${s.last_error})` : what;
          el.classList.add('status-warn');
          el.title = t('kvmp.st.frozen');
        }
      }
    } catch (e) { /* network briefly down: try again on the next round */ }
  }
  setTimeout(pollBmc, 3000);
}

function connect() {
  clearTimeout(retryTimer);
  if (rfb) { rfb._noReconnect = true; rfb.disconnect(); rfb = null; }
  overlay(t('kvmp.ov.connecting'), t('kvmp.ov.connecting.text'));
  status(t('kvmp.connecting'));
  const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/kvm';
  const r = new RFB(screen, url, { shared: true });
  rfb = r;
  r.scaleViewport = scaled;
  r.resizeSession = false;
  r.focusOnClick = true;
  r.background = '#000';
  r.addEventListener('connect', () => {
    retries = 0;
    viewerConnected = true;
    overlay(null);
    status(t('kvmp.st.connected'));
    r.focus();
  });
  r.addEventListener('desktopname', e => { document.title = `webkvm · ${e.detail.name}`; });
  r.addEventListener('securityfailure', e => {
    r._noReconnect = true;
    overlay(t('kvmp.ov.unavailable'), e.detail.reason || t('kvmp.ov.refused'), true);
    status(t('kvmp.st.error'));
  });
  r.addEventListener('disconnect', () => {
    if (r !== rfb || r._noReconnect) return;
    viewerConnected = false;
    status(t('kvmp.st.disconnected'));
    if (retries < 5) {
      const wait = Math.min(2000 * 2 ** retries, 20000);
      retries++;
      overlay(t('kvmp.ov.lost'), t('kvmp.ov.retry_in', { n: Math.round(wait / 1000) }), true);
      retryTimer = setTimeout(connect, wait);
    } else {
      overlay(t('kvmp.ov.lost'), t('kvmp.ov.dead'), true);
    }
  });
}

function sendCombo(names) {
  if (!rfb) return;
  for (const n of names) rfb.sendKey(K[n][0], CODE[n], true);
  for (const n of names.slice().reverse()) rfb.sendKey(K[n][0], CODE[n], false);
  rfb.focus();
}

$('cad').addEventListener('click', () => sendCombo(['ctrl', 'alt', 'del']));
$('reconnect').addEventListener('click', () => { retries = 0; connect(); });
$('ov-retry').addEventListener('click', () => { retries = 0; connect(); });
$('scale').addEventListener('click', () => {
  scaled = !scaled;
  if (rfb) rfb.scaleViewport = scaled;
  $('scale').textContent = scaled ? t('kvmp.fit') : '1:1';
});
$('full').addEventListener('click', () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
});
const menu = $('keys-menu');
$('keys-btn').addEventListener('click', e => { e.stopPropagation(); menu.classList.toggle('open'); });
document.addEventListener('click', () => menu.classList.remove('open'));
menu.querySelectorAll('[data-keys]').forEach(b => b.addEventListener('click', () => {
  sendCombo(b.dataset.keys.split('+'));
}));

connect();
pollBmc();
