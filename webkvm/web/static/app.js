(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const t = (k, v) => window.I18N.t(k, v);
  const TYPE_ORDER = ['temperature', 'fan', 'voltage', 'power', 'current', 'other', 'discrete'];

  let session = null;
  let sensors = [];
  let selected = null;
  let range = 3600;
  let selEntries = [];
  const toast = msg => window.showToast(msg);

  async function api(path, opts) {
    const r = await fetch(path, Object.assign({ credentials: 'same-origin' }, opts || {}));
    if (r.status === 401) { location.href = '/login'; throw new Error('login required'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function pill(el, cls, text) {
    el.className = 'pill' + (cls ? ' ' + cls : '');
    el.textContent = text;
  }

  function ago(ts) {
    if (!ts) return t('ago.never');
    const s = Math.round(Date.now() / 1000 - ts);
    if (s < 60) return t('ago.s', { n: s });
    if (s < 3600) return t('ago.min', { n: Math.round(s / 60) });
    return new Date(ts * 1000).toLocaleString(window.I18N.locale);
  }

  function fact(label, value) {
    const d = document.createElement('div');
    const s = document.createElement('span'); s.textContent = label;
    const b = document.createElement('b'); b.textContent = value == null || value === '' ? '–' : value;
    d.append(s, b);
    return d;
  }

  function unitLabel(unit) {
    return (unit || '').replace('degrees C', '°C').replace('Volts', 'V').replace('Watts', 'W');
  }

  // --------------------------------------------------------------- state
  async function refreshState() {
    let snap, kvm, info;
    try {
      [snap, kvm, info] = await Promise.all([api('/api/v1/sensors'), api('/api/v1/kvm'), api('/api/v1/info')]);
    } catch (e) {
      pill($('bmc-pill'), 'crit', t('state.gateway_down'));
      return;
    }
    pill($('bmc-pill'), snap.up ? 'ok' : 'crit', snap.up ? t('state.bmc_up') : t('state.bmc_down'));
    if (snap.power_on == null) pill($('power-pill'), '', t('state.power_unknown'));
    else pill($('power-pill'), snap.power_on ? 'ok' : 'warn', snap.power_on ? t('state.on') : t('state.off'));
    $('updated').textContent = t('state.last_read', { t: ago(snap.ts) }) +
      (snap.last_error ? t('state.error', { e: snap.last_error }) : '');

    const ch = snap.chassis || {};
    const b = info.bmc || {};
    const faults = ['power_overload', 'main_power_fault', 'power_control_fault', 'drive_fault', 'fan_fault']
      .filter(k => ch[k]).map(k => k.replace(/_/g, ' '));
    const kvmState = ['idle', 'connecting', 'connected', 'retrying'].includes(kvm.state)
      ? t('kvm.' + kvm.state) : kvm.state;
    $('facts').replaceChildren(
      fact(t('fact.board'), [b.board_mfg, b.product].filter(Boolean).join(' ') || null),
      fact(t('fact.board_serial'), b.board_serial),
      fact(t('fact.bmc_fw'), b.bmc_firmware ? `${b.bmc_firmware} (IPMI ${b.ipmi_version || '?'})` : null),
      fact(t('fact.restore'), ch.power_restore_policy),
      fact(t('fact.last_power'), ch.last_power_event),
      fact(t('fact.intrusion'), ch.intrusion == null ? null : ch.intrusion ? t('fact.intrusion.yes') : t('common.no')),
      fact(t('fact.faults'), faults.length ? faults.join(', ') : (ch.power_on == null ? null : t('fact.faults.none'))),
      fact(t('fact.kvm'), kvmState + (kvm.viewers ? ' · ' + t('fact.viewers', { n: kvm.viewers }) : '') +
        (kvm.width ? ` · ${kvm.width}×${kvm.height}` : '')),
      fact(t('fact.vnc'), session.vnc.enabled ? t('fact.vnc.port', { p: session.vnc.port }) : t('fact.vnc.off')),
    );
    sensors = snap.sensors || [];
    renderSensors();
  }

  function renderSensors() {
    const root = $('sensors');
    if (!sensors.length) return;
    const groups = {};
    for (const s of sensors) (groups[s.type] = groups[s.type] || []).push(s);
    const frag = document.createDocumentFragment();
    for (const type of TYPE_ORDER) {
      const list = groups[type];
      if (!list) continue;
      const g = document.createElement('div'); g.className = 'group';
      const h = document.createElement('h3');
      const label = t('type.' + type);
      h.textContent = label === 'type.' + type ? type : label;
      const grid = document.createElement('div'); grid.className = 'grid';
      for (const s of list) grid.appendChild(sensorCard(s));
      g.append(h, grid);
      frag.appendChild(g);
    }
    root.replaceChildren(frag);
    fillSelect();
  }

  function sensorCard(s) {
    const btn = document.createElement('button');
    btn.type = 'button';
    const cls = !s.present ? 'absent' : ['nc', 'cr', 'nr'].includes(s.status) ? s.status : '';
    btn.className = 'sensor ' + cls + (selected === s.name ? ' selected' : '');
    const name = document.createElement('div'); name.className = 'name'; name.textContent = s.name;
    name.title = s.name;
    const val = document.createElement('div'); val.className = 'val';
    if (s.numeric) {
      val.textContent = fmtVal(s.value);
      const u = document.createElement('small');
      u.textContent = unitLabel(s.unit);
      val.appendChild(u);
    } else {
      val.textContent = !s.present ? t('sensor.absent')
        : s.type === 'discrete' ? ((s.states || []).join(', ') || (s.status === 'ok' ? 'ok' : s.status)) : '–';
      if (s.type === 'discrete') { val.style.fontSize = '15px'; val.title = val.textContent; }
    }
    const thr = document.createElement('div'); thr.className = 'thr';
    const th = s.thresholds || {};
    const parts = [];
    if (s.type === 'fan') {                       // for fans only the lower thresholds matter
      if (th.lnc != null) parts.push(t('thr.warn_below', { v: fmtVal(th.lnc) }));
      if (th.lcr != null) parts.push(t('thr.crit_below', { v: fmtVal(th.lcr) }));
    } else if (s.type === 'temperature') {
      if (th.unc != null) parts.push(t('thr.warn', { v: fmtVal(th.unc) }));
      if (th.ucr != null) parts.push(t('thr.crit', { v: fmtVal(th.ucr) }));
    } else if (th.lcr != null || th.ucr != null) {  // voltages & co.: critical range
      parts.push(t('thr.crit_range', { a: fmtVal(th.lcr), b: fmtVal(th.ucr) }));
    }
    thr.textContent = parts.join(' · ') || ' ';
    btn.append(name, val, thr);
    if (s.numeric) btn.addEventListener('click', () => { selectSensor(s.name); });
    else btn.disabled = !s.present;
    return btn;
  }

  // --------------------------------------------------------------- chart
  function fillSelect() {
    const sel = $('chart-sensor');
    const numeric = sensors.filter(s => s.numeric).map(s => s.name);
    const names = numeric.concat(['Chassis Power']);
    if (sel.options.length === names.length) return;
    sel.replaceChildren(...names.map(n => new Option(n === 'Chassis Power' ? t('chart.power') : n, n)));
    if (!selected) {
      const firstTemp = sensors.find(s => s.type === 'temperature' && s.numeric);
      selected = firstTemp ? firstTemp.name : names[0];
      loadChart();
    }
    sel.value = selected;
  }

  function selectSensor(name) {
    selected = name;
    $('chart-sensor').value = name;
    renderSensors();
    loadChart();
    $('chart').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function loadChart() {
    if (!selected) return;
    const to = Math.floor(Date.now() / 1000);
    const from = to - range;
    let data;
    try {
      data = await api(`/api/v1/history?sensor=${encodeURIComponent(selected)}&from=${from}&to=${to}&points=300`);
    } catch (e) { return; }
    const s = sensors.find(x => x.name === selected) || {};
    renderChart($('chart'), data.points, {
      from, to, unit: unitLabel(s.unit), thresholds: s.thresholds, tip: $('chart-tip'),
    });
  }

  // ----------------------------------------------------------------- SEL
  async function refreshSel() {
    let data;
    try { data = await api('/api/v1/sel'); } catch (e) { return; }
    $('sel-count').textContent = t('sel.count', { n: data.count });
    selEntries = data.entries || [];
    fillSelSensors();
    renderSel();
  }

  function fillSelSensors() {
    const sel = $('sel-sensor');
    const current = sel.value;
    const names = [...new Set(selEntries.map(e => e.sensor).filter(Boolean))].sort();
    sel.replaceChildren(new Option(t('sel.all_sensors'), ''), ...names.map(n => new Option(n, n)));
    sel.value = names.includes(current) ? current : '';
  }

  function emptyRow(text) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6; td.className = 'empty'; td.textContent = text;
    tr.appendChild(td);
    return tr;
  }

  function renderSel() {
    const body = $('sel');
    const words = $('sel-search').value.toLowerCase().split(/\s+/).filter(Boolean);
    const state = $('sel-state').value;
    const sensor = $('sel-sensor').value;
    const rows = selEntries.filter(e => {
      if (state && e.direction !== state) return false;
      if (sensor && e.sensor !== sensor) return false;
      if (!words.length) return true;
      const hay = [e.id.toString(16), e.date, e.time, e.sensor, e.event, e.direction].join(' ').toLowerCase();
      return words.every(w => hay.includes(w));
    });
    const filtered = words.length || state || sensor;
    $('sel-shown').textContent = filtered ? t('sel.shown', { n: rows.length, m: selEntries.length }) : '';
    if (!selEntries.length) { body.replaceChildren(emptyRow(t('sel.empty'))); return; }
    if (!rows.length) { body.replaceChildren(emptyRow(t('sel.no_match'))); return; }
    body.replaceChildren(...rows.map(e => {
      const tr = document.createElement('tr');
      for (const [v, mono] of [[e.id.toString(16), true], [e.date, true], [e.time, true],
                               [e.sensor], [e.event], [e.direction]]) {
        const td = document.createElement('td');
        if (mono) td.className = 'mono';
        td.textContent = v;
        tr.appendChild(td);
      }
      return tr;
    }));
  }

  // ---------------------------------------------------------------- start
  async function init() {
    session = await api('/api/v1/session');
    $('host').textContent = session.bmc_host;
    document.title = `webkvm · ${session.bmc_host}`;
    setupPower(session, () => setTimeout(refreshState, 3000));
    let st;
    $('sel-search').addEventListener('input', () => { clearTimeout(st); st = setTimeout(renderSel, 150); });
    $('sel-state').addEventListener('change', renderSel);
    $('sel-sensor').addEventListener('change', renderSel);
    $('sel-clear').addEventListener('click', () => {
      $('sel-search').value = ''; $('sel-state').value = ''; $('sel-sensor').value = '';
      renderSel();
    });
    $('chart-sensor').addEventListener('change', e => { selected = e.target.value; renderSensors(); loadChart(); });
    $('chart-range').querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      $('chart-range').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
      range = +b.dataset.range;
      loadChart();
    }));
    let rt;
    window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(loadChart, 200); });
    await refreshState();
    refreshSel();
    setInterval(refreshState, 10000);
    setInterval(loadChart, 60000);
    setInterval(refreshSel, 60000);
  }
  init().catch(e => toast(t('error', { e: e.message })));
})();
