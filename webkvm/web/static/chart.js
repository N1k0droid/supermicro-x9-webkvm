/* Minimal SVG line chart: average + min/max band + threshold lines + cursor. */
(function () {
  const NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function niceStep(span, target) {
    const raw = span / target;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  }

  function fmtTime(ts, span) {
    const d = new Date(ts * 1000);
    const hm = d.toLocaleTimeString(window.I18N.locale, { hour: '2-digit', minute: '2-digit' });
    if (span <= 86400) return hm;
    return d.toLocaleDateString(window.I18N.locale, { day: '2-digit', month: '2-digit' }) + ' ' + hm;
  }

  function fmtVal(v) {
    if (v == null) return '–';
    const a = Math.abs(v);
    return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
  }

  /* opts: {from, to, unit, thresholds:{unc,ucr,lnc,lcr}, tip: element showing the value under the cursor} */
  window.renderChart = function (container, points, opts) {
    container.innerHTML = '';
    if (!points.length) {
      const div = document.createElement('div');
      div.className = 'chart-empty';
      div.textContent = window.I18N.t('chart.empty');
      container.appendChild(div);
      if (opts.tip) opts.tip.textContent = '';
      return;
    }
    const W = Math.max(container.clientWidth, 300);
    const H = container.clientWidth < 600 ? 220 : 280;
    const m = { l: 48, r: 12, t: 10, b: 26 };
    const svg = el('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img',
                            'aria-label': window.I18N.t('chart.aria') }, container);

    const thr = opts.thresholds || {};
    const thrLines = [];
    if (thr.unc != null) thrLines.push([thr.unc, 'thr-warn']);
    if (thr.ucr != null) thrLines.push([thr.ucr, 'thr-crit']);
    if (thr.lnc != null) thrLines.push([thr.lnc, 'thr-warn']);
    if (thr.lcr != null) thrLines.push([thr.lcr, 'thr-crit']);

    let lo = Infinity, hi = -Infinity;
    for (const p of points) { lo = Math.min(lo, p.min); hi = Math.max(hi, p.max); }
    // show thresholds only when close to the data, so the curve is not flattened
    const span0 = Math.max(hi - lo, Math.abs(hi) * 0.05, 1);
    for (const [v] of thrLines) {
      if (v >= lo - span0 * 1.5 && v <= hi + span0 * 1.5) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    }
    const pad = Math.max((hi - lo) * 0.08, 0.5);
    lo -= pad; hi += pad;
    const step = niceStep(hi - lo, 5);
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;

    const x0 = opts.from, x1 = opts.to;
    const X = t => m.l + (t - x0) / (x1 - x0) * (W - m.l - m.r);
    const Y = v => m.t + (hi - v) / (hi - lo) * (H - m.t - m.b);

    for (let v = lo; v <= hi + step / 2; v += step) {
      el('line', { class: 'grid-line', x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, svg);
      const t = el('text', { x: m.l - 6, y: Y(v) + 4, 'text-anchor': 'end' }, svg);
      t.textContent = fmtVal(v);
    }
    const nTicks = Math.max(2, Math.floor((W - m.l - m.r) / 110));
    for (let i = 0; i <= nTicks; i++) {
      const ts = x0 + (x1 - x0) * i / nTicks;
      const t = el('text', { x: X(ts), y: H - 8,
                             'text-anchor': i === 0 ? 'start' : i === nTicks ? 'end' : 'middle' }, svg);
      t.textContent = fmtTime(ts, x1 - x0);
    }
    for (const [v, cls] of thrLines) {
      if (v >= lo && v <= hi) el('line', { class: cls, x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, svg);
    }

    // break the series where samples are missing (gaps > 3 intervals)
    const gap = points.length > 1 ? (points[points.length - 1].ts - points[0].ts) / points.length * 3 : 0;
    const segments = [];
    let cur = [];
    points.forEach((p, i) => {
      if (i && gap && p.ts - points[i - 1].ts > Math.max(gap, 120)) { segments.push(cur); cur = []; }
      cur.push(p);
    });
    segments.push(cur);
    for (const seg of segments) {
      if (seg.length > 1) {
        const top = seg.map(p => `${X(p.ts).toFixed(1)},${Y(p.max).toFixed(1)}`);
        const bot = seg.slice().reverse().map(p => `${X(p.ts).toFixed(1)},${Y(p.min).toFixed(1)}`);
        el('polygon', { class: 'band', points: top.concat(bot).join(' ') }, svg);
      }
      el('polyline', { class: 'series',
                       points: seg.map(p => `${X(p.ts).toFixed(1)},${Y(p.avg).toFixed(1)}`).join(' ') }, svg);
    }

    const cursor = el('line', { class: 'cursor', y1: m.t, y2: H - m.b, visibility: 'hidden' }, svg);
    const dot = el('circle', { class: 'dot', r: 3.5, visibility: 'hidden' }, svg);
    const unit = opts.unit ? ' ' + opts.unit : '';
    const last = points[points.length - 1];
    const idle = window.I18N.t('chart.last', { v: fmtVal(last.avg) + unit, t: fmtTime(last.ts, 1e9) });
    if (opts.tip) opts.tip.textContent = idle;

    function move(evt) {
      const r = svg.getBoundingClientRect();
      const px = (evt.clientX - r.left) * W / r.width;
      const ts = x0 + (px - m.l) / (W - m.l - m.r) * (x1 - x0);
      let best = points[0];
      for (const p of points) if (Math.abs(p.ts - ts) < Math.abs(best.ts - ts)) best = p;
      cursor.setAttribute('x1', X(best.ts)); cursor.setAttribute('x2', X(best.ts));
      dot.setAttribute('cx', X(best.ts)); dot.setAttribute('cy', Y(best.avg));
      cursor.setAttribute('visibility', 'visible'); dot.setAttribute('visibility', 'visible');
      if (opts.tip) {
        opts.tip.textContent = window.I18N.t('chart.point', { t: fmtTime(best.ts, 1e9), v: fmtVal(best.avg) + unit }) +
          (best.min !== best.max ? window.I18N.t('chart.minmax', { a: fmtVal(best.min), b: fmtVal(best.max) }) : '');
      }
    }
    svg.addEventListener('pointermove', move);
    svg.addEventListener('pointerleave', () => {
      cursor.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden');
      if (opts.tip) opts.tip.textContent = idle;
    });
  };
  window.fmtVal = fmtVal;
})();
