/* Graphcharts: self-contained SVG chart library (no dependencies). Palette via CSS variables from template.html.
   No external deps. Every function returns an SVG string.
   Palette roles come from CSS variables defined in the page. */
(function (global) {
  const NS = 'http://www.w3.org/2000/svg';
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const fmt = (n, d = 0) => (n === null || n === undefined || isNaN(n)) ? 'n/a' : Number(n).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 });
  const compact = n => {
    if (n === null || n === undefined || isNaN(n)) return 'n/a';
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 1 : 2) + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(a >= 1e5 ? 0 : 1) + 'K';
    return String(Math.round(n * 10) / 10);
  };
  const REGION = { Asia: 'var(--s-asia)', India: 'var(--s-india)', Europe: 'var(--s-europe)', Americas: 'var(--s-americas)' };
  const regionColor = r => REGION[r] || 'var(--muted)';
  const INK = 'var(--ink)', INK2 = 'var(--ink2)', MUTED = 'var(--muted)', GRID = 'var(--grid)', AXIS = 'var(--axis)', SURF = 'var(--surface)';
  const font = 'font-family:inherit;';
  const tip = (t) => t ? ` data-tip="${esc(t)}"` : '';
  const niceMax = v => { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; const n = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10; return n * p; };
  const ticks = (max, n = 4) => { const out = []; for (let i = 0; i <= n; i++) out.push(max * i / n); return out; };
  const svgOpen = (w, h, extra = '') => `<svg xmlns="${NS}" viewBox="0 0 ${w} ${h}" width="100%" style="max-width:${w}px;display:block;overflow:visible;${font}" ${extra}>`;
  const text = (x, y, s, o = {}) => `<text x="${x}" y="${y}" fill="${o.fill || INK2}" font-size="${o.size || 11}" font-weight="${o.weight || 400}" text-anchor="${o.anchor || 'start'}" dominant-baseline="${o.base || 'middle'}"${o.extra || ''}>${esc(s)}</text>`;

  /* ---------- Horizontal bars (single series or region-colored) ---------- */
  function hbar(items, opt = {}) {
    // items: [{label, value, region?, sub?, tip?}]
    const W = opt.width || 720, labelW = opt.labelW || 170, valueW = 64, rowH = opt.rowH || 26, barH = Math.min(18, rowH - 8), pad = 12;
    const H = pad + items.length * rowH + (opt.axis === false ? 6 : 28);
    const max = opt.max || niceMax(Math.max(...items.map(i => i.value || 0)));
    const x0 = labelW + 8, plotW = W - x0 - valueW - 8;
    let s = svgOpen(W, H);
    const tk = ticks(max, 4);
    if (opt.axis !== false) {
      tk.forEach(t => { const x = x0 + plotW * t / max; s += `<line x1="${x}" x2="${x}" y1="${pad}" y2="${pad + items.length * rowH}" stroke="${GRID}" stroke-width="1"/>`; s += text(x, H - 8, (opt.tickFmt || fmt)(t), { fill: MUTED, size: 10, anchor: 'middle' }); });
    }
    items.forEach((it, i) => {
      const y = pad + i * rowH + (rowH - barH) / 2;
      const v = it.value || 0; const w = Math.max(0, plotW * v / max);
      const col = it.color || (it.region ? regionColor(it.region) : 'var(--s1)');
      s += text(x0 - 8, y + barH / 2, it.label, { fill: INK, size: 11.5, anchor: 'end', weight: it.bold ? 600 : 400 });
      if (it.value === null || it.value === undefined) {
        s += text(x0 + 4, y + barH / 2, 'not disclosed', { fill: MUTED, size: 10, extra: ' font-style="italic"' });
      } else {
        s += `<path d="M${x0},${y} h${Math.max(w - 4, 0)} a4,4 0 0 1 4,4 v${barH - 8} a4,4 0 0 1 -4,4 h-${Math.max(w - 4, 0)} z" fill="${col}"${tip(it.tip)}/>`;
        s += text(x0 + w + 6, y + barH / 2, it.display !== undefined ? it.display : (opt.valFmt || fmt)(v), { fill: INK2, size: 10.5, extra: ' style="font-variant-numeric:tabular-nums"' });
      }
    });
    s += `<line x1="${x0}" x2="${x0}" y1="${pad}" y2="${pad + items.length * rowH}" stroke="${AXIS}" stroke-width="1"/>`;
    return s + '</svg>';
  }

  /* ---------- Stacked horizontal bars (part-to-whole) ---------- */
  function stackedH(rows, keys, opt = {}) {
    // rows: [{label, values:{key:num}, tip?}], keys: [{key,label,color}]
    const W = opt.width || 720, labelW = opt.labelW || 150, rowH = opt.rowH || 28, barH = Math.min(18, rowH - 4), pad = 10;
    const x0 = labelW + 8, plotW = W - x0 - 60;
    // legend layout with wrapping
    const lg = []; let lx = x0, ly = pad; keys.forEach(k => { const wdt = 14 + k.label.length * 6.2 + 18; if (lx + wdt > W - 8 && lx > x0) { lx = x0; ly += 18; } lg.push({ x: lx, y: ly, k }); lx += wdt; });
    const legendH = (ly - pad) + 26;
    const H = pad + rows.length * rowH + (opt.axis === false ? 4 : 24) + legendH;
    const totals = rows.map(r => keys.reduce((a, k) => a + (r.values[k.key] || 0), 0));
    const max = opt.percent ? 100 : (opt.max || niceMax(Math.max(...totals)));
    let s = svgOpen(W, H);
    lg.forEach(g => { s += `<rect x="${g.x}" y="${g.y}" width="10" height="10" rx="2" fill="${g.k.color}"/>`; s += text(g.x + 14, g.y + 5, g.k.label, { size: 10.5 }); });
    const top = pad + legendH;
    if (opt.axis !== false) ticks(max, 4).forEach(t => { const x = x0 + plotW * t / max; s += `<line x1="${x}" x2="${x}" y1="${top}" y2="${top + rows.length * rowH}" stroke="${GRID}"/>`; s += text(x, H - 8, (opt.tickFmt || (v => opt.percent ? v + '%' : fmt(v)))(t), { fill: MUTED, size: 10, anchor: 'middle' }); });
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2;
      s += text(x0 - 8, y + barH / 2, r.label, { fill: INK, size: 11.5, anchor: 'end' });
      let x = x0; const tot = opt.percent ? totals[i] : max;
      keys.forEach((k, ki) => {
        const v = r.values[k.key] || 0; if (!v) return;
        const w = plotW * (opt.percent ? (v / tot * 100) : v) / max;
        const isLast = ki === keys.length - 1 || keys.slice(ki + 1).every(kk => !r.values[kk.key]);
        const gap = 2;
        const ww = Math.max(0, w - (isLast ? 0 : gap));
        const d = isLast ? `M${x},${y} h${Math.max(ww - 4, 0)} a4,4 0 0 1 4,4 v${barH - 8} a4,4 0 0 1 -4,4 h-${Math.max(ww - 4, 0)} z` : `M${x},${y} h${ww} v${barH} h-${ww} z`;
        s += `<path d="${d}" fill="${k.color}"${tip(`${r.label} · ${k.label}: ${opt.percent ? (v / tot * 100).toFixed(1) + '%' : fmt(v, 1)}`)}/>`;
        if (ww > 34 && opt.inlineLabels) s += text(x + ww / 2, y + barH / 2, opt.percent ? Math.round(v / tot * 100) + '%' : compact(v), { fill: '#fff', size: 10, anchor: 'middle', weight: 600 });
        x += w;
      });
      if (opt.totalLabel) s += text(x0 + plotW * (opt.percent ? 1 : totals[i] / max) + 6, y + barH / 2, opt.totalLabel(totals[i], r), { size: 10.5, extra: ' style="font-variant-numeric:tabular-nums"' });
    });
    s += `<line x1="${x0}" x2="${x0}" y1="${top}" y2="${top + rows.length * rowH}" stroke="${AXIS}"/>`;
    return s + '</svg>';
  }

  /* ---------- Ribbon rank chart (Power BI-style) ---------- */
  function ribbonRank(periods, series, opt = {}) {
    // series: [{name, region, values:[v per period]}] ; ribbons sized by value, ordered by rank
    const W = opt.width || 760, H = opt.height || 420, padL = 130, padR = 130, padT = 34, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const colW = Math.min(46, plotW / periods.length * 0.45);
    const xs = periods.map((_, i) => padL + (periods.length === 1 ? plotW / 2 : plotW * i / (periods.length - 1)));
    const gap = 3;
    const layout = periods.map((p, pi) => {
      const items = series.map(sr => ({ sr, v: sr.values[pi] || 0 })).filter(d => d.v > 0).sort((a, b) => b.v - a.v);
      const total = items.reduce((a, d) => a + d.v, 0);
      const scale = (plotH - gap * (items.length - 1)) / total;
      let y = padT; const pos = {};
      items.forEach(d => { const h = d.v * scale; pos[d.sr.name] = { y0: y, y1: y + h, v: d.v }; y += h + gap; });
      return pos;
    });
    let s = svgOpen(W, H);
    // ribbons
    series.forEach(sr => {
      const col = sr.color || regionColor(sr.region);
      for (let i = 0; i < periods.length - 1; i++) {
        const a = layout[i][sr.name], b = layout[i + 1][sr.name]; if (!a || !b) continue;
        const x1 = xs[i] + colW / 2, x2 = xs[i + 1] - colW / 2, cx = (x1 + x2) / 2;
        s += `<path d="M${x1},${a.y0} C${cx},${a.y0} ${cx},${b.y0} ${x2},${b.y0} L${x2},${b.y1} C${cx},${b.y1} ${cx},${a.y1} ${x1},${a.y1} Z" fill="${col}" opacity="0.32"${tip(`${sr.name}: ${periods[i]} ${a.v}% → ${periods[i + 1]} ${b.v}%`)}/>`;
      }
      periods.forEach((p, pi) => {
        const a = layout[pi][sr.name]; if (!a) return;
        s += `<rect x="${xs[pi] - colW / 2}" y="${a.y0}" width="${colW}" height="${Math.max(a.y1 - a.y0, 1)}" fill="${col}" rx="2"${tip(`${sr.name} · ${p}: ${a.v}%`)}/>`;
        if (a.y1 - a.y0 > 12) s += text(xs[pi], (a.y0 + a.y1) / 2, a.v + '%', { fill: '#fff', size: 9.5, anchor: 'middle', weight: 600 });
      });
      // labels left of first & right of last
      const f = layout[0][sr.name], l = layout[periods.length - 1][sr.name];
      if (f && f.y1 - f.y0 > 9) s += text(xs[0] - colW / 2 - 8, (f.y0 + f.y1) / 2, sr.name, { fill: INK, size: 10.5, anchor: 'end' });
      if (l && l.y1 - l.y0 > 9) s += text(xs[periods.length - 1] + colW / 2 + 8, (l.y0 + l.y1) / 2, sr.name, { fill: INK, size: 10.5 });
    });
    periods.forEach((p, i) => s += text(xs[i], padT - 14, p, { fill: INK, size: 11.5, anchor: 'middle', weight: 600 }));
    return s + '</svg>';
  }

  /* ---------- Sankey ---------- */
  function sankey(nodes, links, opt = {}) {
    // nodes: [{id, label, col (0..n), color?, region?}], links: [{source, target, value, tip?}]
    const W = opt.width || 760, H = opt.height || 440, padL = 6, padR = 6, padT = 14, padB = 14, nodeW = 14, gap = 10;
    const cols = Math.max(...nodes.map(n => n.col)) + 1;
    const plotW = W - padL - padR - (opt.labelR || 150) - (opt.labelL || 150);
    const xOf = c => padL + (opt.labelL || 150) + plotW * c / (cols - 1);
    const byId = {}; nodes.forEach(n => { byId[n.id] = n; n.in = 0; n.out = 0; });
    links.forEach(l => { byId[l.source].out += l.value; byId[l.target].in += l.value; });
    nodes.forEach(n => n.val = Math.max(n.in, n.out));
    const colNodes = []; for (let c = 0; c < cols; c++) colNodes.push(nodes.filter(n => n.col === c));
    const scale = Math.min(...colNodes.map(cn => ((H - padT - padB) - gap * (cn.length - 1)) / Math.max(cn.reduce((a, n) => a + n.val, 0), 1)));
    colNodes.forEach(cn => { if (opt.sort !== false) cn.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || b.val - a.val); let y = padT; const total = cn.reduce((a, n) => a + n.val * scale, 0) + gap * (cn.length - 1); y = padT + ((H - padT - padB) - total) / 2; cn.forEach(n => { n.y0 = y; n.y1 = y + n.val * scale; n.x0 = xOf(n.col); n.x1 = n.x0 + nodeW; n.sy = n.y0; n.ty = n.y0; y = n.y1 + gap; }); });
    let s = svgOpen(W, H);
    // links sorted by source order
    const sorted = links.slice().sort((a, b) => byId[a.source].y0 - byId[b.source].y0 || byId[a.target].y0 - byId[b.target].y0);
    sorted.forEach(l => {
      const a = byId[l.source], b = byId[l.target]; const h = l.value * scale;
      const y0 = a.sy, y1 = b.ty; a.sy += h; b.ty += h;
      const x0 = a.x1, x1 = b.x0, cx = (x0 + x1) / 2;
      const col = l.color || a.color || regionColor(a.region);
      s += `<path d="M${x0},${y0} C${cx},${y0} ${cx},${y1} ${x1},${y1} L${x1},${y1 + h} C${cx},${y1 + h} ${cx},${y0 + h} ${x0},${y0 + h} Z" fill="${col}" opacity="0.3"${tip(l.tip || `${a.label} → ${b.label}: ${fmt(l.value, 1)}`)}/>`;
    });
    nodes.forEach(n => {
      const col = n.color || regionColor(n.region) || 'var(--s1)';
      s += `<rect x="${n.x0}" y="${n.y0}" width="${nodeW}" height="${Math.max(n.y1 - n.y0, 1.5)}" fill="${col}" rx="2"${tip(`${n.label}: ${fmt(n.val, 1)}`)}/>`;
      const lab = n.label + (opt.nodeValue ? ` (${opt.nodeValue(n)})` : '');
      if (n.col === 0) s += text(n.x0 - 6, (n.y0 + n.y1) / 2, lab, { fill: INK, size: 10.5, anchor: 'end' });
      else if (n.col === cols - 1) s += text(n.x1 + 6, (n.y0 + n.y1) / 2, lab, { fill: INK, size: 10.5 });
      else s += text(n.x1 + 6, (n.y0 + n.y1) / 2, lab, { fill: INK, size: 10.5 });
    });
    return s + '</svg>';
  }

  /* ---------- Heatmap / status matrix ---------- */
  function matrix(rows, cols, opt = {}) {
    // rows: [{label, region, cells:[state...]}] ; states map to opt.states {key:{color,label,glyph}}
    const W = opt.width || 760, labelW = opt.labelW || 190, cell = opt.cell || 26, headH = opt.headH || 74, pad = 6;
    const colW = Math.min(cell + 14, (W - labelW - pad) / cols.length);
    const H = headH + rows.length * cell + pad + (opt.legend ? 30 : 0);
    let s = svgOpen(W, H);
    cols.forEach((c, i) => { const x = labelW + colW * i + colW / 2; s += opt.rotateHead === false
      ? text(x, headH - 12, c, { fill: INK2, size: 10, anchor: 'middle' })
      : `<text x="${x}" y="${headH - 8}" fill="${INK2}" font-size="10" text-anchor="start" transform="rotate(-45 ${x} ${headH - 8})">${esc(c)}</text>`; });
    rows.forEach((r, ri) => {
      const y = headH + ri * cell;
      if (ri % 2 === 0) s += `<rect x="0" y="${y}" width="${W}" height="${cell}" fill="var(--zebra)"/>`;
      if (r.region) s += `<circle cx="8" cy="${y + cell / 2}" r="4" fill="${regionColor(r.region)}"/>`;
      s += text(r.region ? 18 : 6, y + cell / 2, r.label, { fill: INK, size: 11 });
      r.cells.forEach((st, ci) => {
        const x = labelW + colW * ci + (colW - cell) / 2 + 2; const def = opt.states[st] || opt.states.na;
        s += `<rect x="${x}" y="${y + 3}" width="${cell - 4}" height="${cell - 6}" rx="4" fill="${def.color}"${tip(`${r.label} · ${cols[ci]}: ${def.label}${r.tips && r.tips[ci] ? ' · ' + r.tips[ci] : ''}`)}/>`;
        if (def.glyph) s += text(x + (cell - 4) / 2, y + cell / 2, def.glyph, { fill: def.ink || '#fff', size: 11, anchor: 'middle', weight: 700 });
      });
    });
    if (opt.legend) { let lx = 18; const ly = H - 12; Object.values(opt.states).forEach(d => { s += `<rect x="${lx}" y="${ly - 7}" width="14" height="14" rx="3" fill="${d.color}"/>`; if (d.glyph) s += text(lx + 7, ly, d.glyph, { fill: d.ink || '#fff', size: 9, anchor: 'middle', weight: 700 }); s += text(lx + 18, ly, d.label, { size: 10.5 }); lx += 22 + d.label.length * 6.3 + 14; }); }
    return s + '</svg>';
  }


  /* ---------- Move cards (before -> after per person, with flat avatars, status, optional second move) ---------- */
  function avatar(x, y, w, opt = {}) {
    // flat vector person inside an arch frame: {fill, gender:'f'|'m'|'n', dashed, stroke}
    const h = w * 1.18, r = w / 2, g = opt.gender || 'n', fill = opt.fill || 'var(--s1)';
    let o = `<path d="M${x} ${y + h} V${y + r} A${r} ${r} 0 0 1 ${x + w} ${y + r} V${y + h} Z" fill="${fill}"/>`;
    o += `<clipPath id="av${Math.round(x)}_${Math.round(y)}"><path d="M${x} ${y + h} V${y + r} A${r} ${r} 0 0 1 ${x + w} ${y + r} V${y + h} Z"/></clipPath><g clip-path="url(#av${Math.round(x)}_${Math.round(y)})">`;
    const cx = x + w / 2, skin = '#f1c9a5', hair = '#2a2320', shirt = g === 'f' ? '#14213d' : '#1b3a2d';
    o += `<path d="M${cx - w * 0.42} ${y + h} v-${w * 0.22} q${w * 0.42} -${w * 0.30} ${w * 0.84} 0 v${w * 0.22} Z" fill="${shirt}"/>`;
    o += `<path d="M${cx - w * 0.09} ${y + h - w * 0.36} h${w * 0.18} v${w * 0.16} l-${w * 0.09} ${w * 0.08} l-${w * 0.09} -${w * 0.08} Z" fill="#fff"/>`;
    o += `<rect x="${cx - w * 0.07}" y="${y + h * 0.58}" width="${w * 0.14}" height="${w * 0.12}" fill="${skin}"/>`;
    o += `<ellipse cx="${cx}" cy="${y + h * 0.47}" rx="${w * 0.19}" ry="${w * 0.23}" fill="${skin}"/>`;
    if (g === 'f') o += `<path d="M${cx - w * 0.24} ${y + h * 0.62} V${y + h * 0.42} a${w * 0.24} ${w * 0.26} 0 0 1 ${w * 0.48} 0 V${y + h * 0.62} h-${w * 0.06} V${y + h * 0.46} a${w * 0.18} ${w * 0.16} 0 0 0 -${w * 0.36} 0 V${y + h * 0.62} Z" fill="${hair}"/>`;
    else o += `<path d="M${cx - w * 0.21} ${y + h * 0.42} a${w * 0.21} ${w * 0.2} 0 0 1 ${w * 0.42} 0 q-${w * 0.1} -${w * 0.06} -${w * 0.2} ${w * 0.02} q-${w * 0.12} -${w * 0.08} -${w * 0.22} -${w * 0.02} Z" fill="${hair}"/>`;
    o += `</g><path d="M${x} ${y + h} V${y + r} A${r} ${r} 0 0 1 ${x + w} ${y + r} V${y + h} Z" fill="none" stroke="${opt.stroke || fill}" stroke-width="${opt.dashed ? 1.5 : 0}"${opt.dashed ? ' stroke-dasharray="4 3"' : ''}/>`;
    return o;
  }
  function moveCards(items, opt = {}) {
    // items: [{name, id, gender, status:'good'|'warning'|'serious', from:{title, sub, who, gender, chips:[]}, to:{...}, extra?:{label, from:{title, who, gender}, to:{title, who, gender}, flag?}}]
    const W = opt.width || 1100, cardW = opt.cardW || 330, cardH = 118, avW = opt.avatarW || 200, gap = 22, padY = 16;
    const STC = { good: 'var(--st-good)', warning: 'var(--st-warning)', serious: 'var(--st-serious)', critical: 'var(--st-critical)' };
    const STL = Object.assign({ good: 'Easy: one change', warning: 'Needs one more group move', serious: 'Teacher changes', critical: 'Not possible' }, opt.statusLabels || {});
    const stripH = it => padY * 2 + cardH + (it.extra ? 70 : 0);
    const H = items.reduce((a, it) => a + stripH(it) + 12, 0) + (opt.legend === false ? 0 : 32);
    let s = svgOpen(W, H);
    const card = (x, y, c, kind, col) => {
      let o = `<rect x="${x}" y="${y}" width="${cardW}" height="${cardH}" rx="12" fill="${SURF}" stroke="${col}" stroke-width="1.5" stroke-dasharray="5 4"/>`;
      o += `<rect x="${x + 12}" y="${y - 9}" width="${kind.length * 7.5 + 14}" height="18" rx="9" fill="${col}"/>` + text(x + 12 + (kind.length * 7.5 + 14) / 2, y, kind, { fill: kind === 'JOINS' || kind === 'LEAVES' ? '#fff' : INK, size: 10, weight: 700, anchor: 'middle' });
      o += avatar(x + 14, y + 20, 64, { fill: col, gender: c.gender, dashed: false });
      o += text(x + 92, y + 40, c.title, { fill: INK, size: 22, weight: 700 });
      if (c.sub) o += text(x + 92, y + 60, c.sub, { fill: INK2, size: 10.5 });
      o += text(x + 92, y + 82, c.who || 'not disclosed', { fill: INK, size: 12.5, weight: 600, extra: c.who ? '' : ' font-style="italic"' });
      let cx = x + 92; (c.chips || []).forEach(ch => { const w = ch.length * 6.4 + 14; o += `<rect x="${cx}" y="${y + 94}" width="${w}" height="18" rx="9" fill="var(--zebra)" stroke="${GRID}"/>` + text(cx + w / 2, y + 103, ch, { fill: INK2, size: 9.5, anchor: 'middle', weight: 600 }); cx += w + 5; });
      return o;
    };
    const arrow = (x, y, col, len = gap + 18) => `<line x1="${x}" x2="${x + len - 8}" y1="${y}" y2="${y}" stroke="${col}" stroke-width="4" stroke-linecap="round"/><path d="M${x + len - 14} ${y - 9} L${x + len} ${y} L${x + len - 14} ${y + 9}" fill="none" stroke="${col}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`;
    let y = 0;
    items.forEach((it, i) => {
      const h = stripH(it), col = STC[it.status] || STC.good;
      s += `<rect x="0" y="${y}" width="${W}" height="${h}" rx="14" fill="${i % 2 ? 'var(--zebra)' : SURF}" stroke="${GRID}"/>`;
      const ax = 22, ay = y + padY;
      s += avatar(ax, ay, 78, { fill: col, gender: it.gender, dashed: true, stroke: col });
      s += text(ax + 92, ay + 26, it.name, { fill: INK, size: 15, weight: 700 });
      if (it.id) s += text(ax + 92, ay + 44, it.id, { fill: MUTED, size: 10.5 });
      const lbl = it.statusLabel || STL[it.status]; const bw = lbl.length * 6.3 + 18; s += `<rect x="${ax + 92}" y="${ay + 58}" width="${bw}" height="22" rx="11" fill="${col}"/>` + text(ax + 92 + bw / 2, ay + 69, lbl, { fill: it.status === 'warning' ? '#0b0b0b' : '#fff', size: 10.5, weight: 700, anchor: 'middle' });
      const x1 = avW + 60, x2 = x1 + cardW + gap + 40;
      s += card(x1, y + padY, it.from, 'LEAVES', 'var(--muted)') + arrow(x1 + cardW + 8, y + padY + cardH / 2, col) + card(x2, y + padY, it.to, 'JOINS', 'var(--s1)');
      if (it.extra) {
        const ey = y + padY + cardH + 14, fl = it.extra.flag, ec = fl ? 'var(--st-serious)' : 'var(--st-warning)';
        s += `<rect x="${x1}" y="${ey}" width="${x2 + cardW - x1}" height="52" rx="10" fill="${ec}" opacity="0.14"/><rect x="${x1}" y="${ey}" width="${x2 + cardW - x1}" height="52" rx="10" fill="none" stroke="${ec}" stroke-width="1.5" stroke-dasharray="5 4"/>`;
        s += text(x1 + 14, ey + 26, it.extra.label, { fill: INK, size: 12, weight: 700 });
        const fx = x1 + 14 + it.extra.label.length * 6.8 + 16; s += avatar(fx, ey + 6, 36, { fill: 'var(--muted)', gender: it.extra.from.gender });
        s += text(fx + 44, ey + 19, it.extra.from.title, { fill: INK, size: 13, weight: 700 }) + text(fx + 44, ey + 36, it.extra.from.who, { fill: INK2, size: 11 });
        const mx = fx + 230; s += arrow(mx, ey + 26, ec, 44);
        const tx = mx + 56; s += avatar(tx, ey + 6, 36, { fill: fl ? ec : 'var(--s1)', gender: it.extra.to.gender });
        s += text(tx + 44, ey + 19, it.extra.to.title, { fill: INK, size: 13, weight: 700 }) + text(tx + 44, ey + 36, it.extra.to.who, { fill: fl ? ec : INK2, size: 11, weight: fl ? 700 : 400 });
        if (fl) { const w = fl.length * 6.5 + 16; s += `<rect x="${x2 + cardW - w - 12}" y="${ey + 15}" width="${w}" height="22" rx="11" fill="${ec}"/>` + text(x2 + cardW - 12 - w / 2, ey + 26, fl, { fill: '#fff', size: 10.5, weight: 700, anchor: 'middle' }); }
      }
      y += h + 12;
    });
    if (opt.legend !== false) { let lx = 8; const ly = H - 12; ['good', 'warning', 'serious'].forEach(k => { s += `<rect x="${lx}" y="${ly - 7}" width="14" height="14" rx="4" fill="${STC[k]}"/>` + text(lx + 18, ly, STL[k], { size: 10.5 }); lx += 22 + STL[k].length * 6.3 + 16; }); }
    return s + '</svg>';
  }

  /* ---------- Dot timeline (target years) ---------- */
  function dotTimeline(items, opt = {}) {
    // items: [{label, year, region, tip?, marker?}] ; one row each, x = year
    const W = opt.width || 760, labelW = opt.labelW || 190, rowH = 22, padT = 26, padB = 8;
    const minY = opt.min || Math.min(...items.map(i => i.year).filter(Boolean)) - 1, maxY = opt.max || Math.max(...items.map(i => i.year).filter(Boolean)) + 1;
    const x0 = labelW + 8, plotW = W - x0 - 20; const xOf = y => x0 + plotW * (y - minY) / (maxY - minY);
    const H = padT + items.length * rowH + padB;
    let s = svgOpen(W, H);
    for (let y = Math.ceil(minY / 5) * 5; y <= maxY; y += 5) { s += `<line x1="${xOf(y)}" x2="${xOf(y)}" y1="${padT - 4}" y2="${H - padB}" stroke="${GRID}"/>`; s += text(xOf(y), padT - 14, y, { fill: MUTED, size: 10, anchor: 'middle' }); }
    items.forEach((it, i) => {
      const y = padT + i * rowH + rowH / 2;
      s += text(x0 - 8, y, it.label, { fill: INK, size: 11, anchor: 'end' });
      if (!it.year) { s += text(x0 + 4, y, it.note || 'no target', { fill: MUTED, size: 10, extra: ' font-style="italic"' }); return; }
      s += `<line x1="${x0}" x2="${xOf(it.year)}" y1="${y}" y2="${y}" stroke="${regionColor(it.region)}" stroke-width="2" opacity="0.35"/>`;
      if (it.interim) { s += `<circle cx="${xOf(it.interim)}" cy="${y}" r="4" fill="${SURF}" stroke="${regionColor(it.region)}" stroke-width="2"${tip(it.interimTip)}/>`; }
      s += `<circle cx="${xOf(it.year)}" cy="${y}" r="5.5" fill="${regionColor(it.region)}" stroke="${SURF}" stroke-width="2"${tip(it.tip)}/>`;
      s += text(xOf(it.year) + 9, y, it.year + (it.note ? ' · ' + it.note : ''), { fill: INK2, size: 10 });
    });
    return s + '</svg>';
  }

  /* ---------- Dumbbell (before→after or a vs benchmark) ---------- */
  function dumbbell(items, opt = {}) {
    // items: [{label, a, b, region}]
    const W = opt.width || 720, labelW = opt.labelW || 190, rowH = opt.rowH || 24, padT = 30, padB = 26;
    const vals = items.flatMap(i => [i.a, i.b]).filter(v => v !== null && v !== undefined);
    const max = opt.max || niceMax(Math.max(...vals)), min = opt.min || 0;
    const x0 = labelW + 8, plotW = W - x0 - 60; const xOf = v => x0 + plotW * (v - min) / (max - min);
    const H = padT + items.length * rowH + padB;
    let s = svgOpen(W, H);
    ticks(max - min, 4).forEach(t => { const x = xOf(min + t); s += `<line x1="${x}" x2="${x}" y1="${padT - 6}" y2="${H - padB}" stroke="${GRID}"/>`; s += text(x, H - 8, fmt(min + t), { fill: MUTED, size: 10, anchor: 'middle' }); });
    s += `<circle cx="${x0}" cy="${padT - 16}" r="4" fill="${SURF}" stroke="${INK2}" stroke-width="2"/>` + text(x0 + 8, padT - 16, opt.aLabel || 'A', { size: 10.5 });
    s += `<circle cx="${x0 + 90}" cy="${padT - 16}" r="5" fill="${INK2}"/>` + text(x0 + 98, padT - 16, opt.bLabel || 'B', { size: 10.5 });
    items.forEach((it, i) => {
      const y = padT + i * rowH + rowH / 2; const col = it.color || regionColor(it.region);
      s += text(x0 - 8, y, it.label, { fill: INK, size: 11, anchor: 'end' });
      if (it.a != null && it.b != null) {
        s += `<line x1="${xOf(it.a)}" x2="${xOf(it.b)}" y1="${y}" y2="${y}" stroke="${col}" stroke-width="2" opacity="0.5"/>`;
        if (opt.arrow) { const dir = it.b >= it.a ? 1 : -1, bx = xOf(it.b) - dir * 9;
          s += `<path d="M${bx},${y - 4} L${bx + dir * 6},${y} L${bx},${y + 4} z" fill="${col}" opacity="0.85"/>`; }
      }
      if (it.a != null) s += `<circle cx="${xOf(it.a)}" cy="${y}" r="4.5" fill="${SURF}" stroke="${col}" stroke-width="2"${tip(`${it.label} · ${opt.aLabel}: ${fmt(it.a, 1)}`)}/>`;
      if (it.b != null) s += `<circle cx="${xOf(it.b)}" cy="${y}" r="5.5" fill="${col}" stroke="${SURF}" stroke-width="2"${tip(`${it.label} · ${opt.bLabel}: ${fmt(it.b, 1)}`)}/>`;
      const right = Math.max(it.a ?? 0, it.b ?? 0);
      if (it.a == null && it.b == null) s += text(x0 + 4, y, 'not disclosed', { fill: MUTED, size: 10, extra: ' font-style="italic"' });
      else s += text(xOf(right) + 9, y, it.display || (it.b != null ? fmt(it.b, 1) : fmt(it.a, 1)), { fill: INK2, size: 10, extra: ' style="font-variant-numeric:tabular-nums"' });
    });
    return s + '</svg>';
  }

  /* ---------- Diverging bar (deviation from a baseline: above/below a benchmark) ---------- */
  function divergingBar(items, opt = {}) {
    // items: [{label, value (signed deviation) | null, display?, sub?, color?, tip?}]
    const W = opt.width || 720, labelW = opt.labelW || 190, rowH = opt.rowH || 22, padT = opt.padT || 24, padB = 22, subW = opt.subW || 0, valueW = 62;
    const vals = items.map(i => i.value).filter(v => v !== null && v !== undefined);
    const maxAbs = opt.max || niceMax(Math.max(...vals.map(Math.abs), 1));
    const x0 = labelW + 8, plotW = W - x0 - valueW * 2 - subW - 8, xc = x0 + valueW + plotW / 2;
    const xOf = v => xc + (plotW / 2) * v / maxAbs; const H = padT + items.length * rowH + padB;
    let s = svgOpen(W, H);
    [-maxAbs, -maxAbs / 2, maxAbs / 2, maxAbs].forEach(t => { const x = xOf(t); s += `<line x1="${x}" x2="${x}" y1="${padT - 4}" y2="${H - padB}" stroke="${GRID}"/>`; if (Math.abs(t) === maxAbs) s += text(x, H - 8, (opt.tickFmt || (v => (v > 0 ? '+' : '') + fmt(v, 1)))(t), { fill: MUTED, size: 10, anchor: t < 0 ? 'end' : 'start' }); });
    s += `<line x1="${xc}" x2="${xc}" y1="${padT - 4}" y2="${H - padB}" stroke="${INK}" stroke-width="1.2"/>`;
    s += text(xc, H - 8, opt.zeroLabel || '0', { fill: INK2, size: 10, anchor: 'middle', weight: 600 });
    if (opt.negLabel) s += text(xc - 10, padT - 12, opt.negLabel, { fill: opt.negColor || 'var(--s2)', size: 10.5, anchor: 'end', weight: 600 });
    if (opt.posLabel) s += text(xc + 10, padT - 12, opt.posLabel, { fill: opt.posColor || 'var(--s3)', size: 10.5, weight: 600 });
    items.forEach((it, i) => {
      const y = padT + i * rowH + rowH / 2, barH = Math.min(14, rowH - 8);
      s += text(x0 - 8, y, it.label, { fill: INK, size: 11, anchor: 'end' });
      if (it.value == null) { s += text(xc + 6, y, 'not disclosed', { fill: MUTED, size: 10, extra: ' font-style="italic"' }); }
      else {
        const col = it.color || (it.value >= 0 ? (opt.posColor || 'var(--s3)') : (opt.negColor || 'var(--s2)'));
        const xa = Math.min(xc, xOf(it.value)), w = Math.max(2, Math.abs(xOf(it.value) - xc));
        s += `<rect x="${xa}" y="${y - barH / 2}" width="${w}" height="${barH}" rx="4" fill="${col}"${tip(it.tip || `${it.label}: ${(it.value > 0 ? '+' : '') + fmt(it.value, 1)}`)}/>`;
        const lbl = it.display || ((it.value > 0 ? '+' : '') + fmt(it.value, 1));
        if (it.value >= 0) s += text(xOf(it.value) + 6, y, lbl, { fill: INK2, size: 10, weight: 600, extra: ' style="font-variant-numeric:tabular-nums"' });
        else s += text(xOf(it.value) - 6, y, lbl, { fill: INK2, size: 10, weight: 600, anchor: 'end', extra: ' style="font-variant-numeric:tabular-nums"' });
      }
      if (it.sub && subW) s += text(W - subW + 6, y, it.sub, { fill: MUTED, size: 9.5, extra: ' style="font-variant-numeric:tabular-nums"' });
    });
    return s + '</svg>';
  }

  /* ---------- Gantt / regulatory timeline ---------- */
  function gantt(items, opt = {}) {
    // items: [{label, start (decimal year), end?, milestone?, status:'in-force'|'delayed'|'pending'|'proposed', note}]
    const W = opt.width || 760, labelW = opt.labelW || 250, rowH = 24, padT = 28, padB = 8;
    const minY = opt.min, maxY = opt.max; const x0 = labelW + 8, plotW = W - x0 - 12; const xOf = y => x0 + plotW * (y - minY) / (maxY - minY);
    const H = padT + items.length * rowH + padB;
    const col = { 'in-force': 'var(--s1)', 'delayed': 'var(--st-warning)', 'pending': 'var(--s7)', 'proposed': 'var(--muted)', 'repealed': 'var(--st-critical)' };
    let s = svgOpen(W, H);
    const tks = opt.ticks || Array.from({ length: Math.floor(maxY) - Math.ceil(minY) + 1 }, (_, n) => ({ v: Math.ceil(minY) + n, label: String(Math.ceil(minY) + n) }));
    for (const tk of tks) { const y = tk.v; s += `<line x1="${xOf(y)}" x2="${xOf(y)}" y1="${padT - 6}" y2="${H - padB}" stroke="${GRID}"/>`; s += text(xOf(y), padT - 16, tk.label, { fill: MUTED, size: 10, anchor: 'middle' }); }
    const now = opt.now; if (now) { s += `<line x1="${xOf(now)}" x2="${xOf(now)}" y1="${padT - 6}" y2="${H - padB}" stroke="${INK2}" stroke-width="1"/>`; s += text(xOf(now) + 3, padT - 4, (opt.nowLabel || 'today'), { fill: INK2, size: 9 }); }
    items.forEach((it, i) => {
      const y = padT + i * rowH + rowH / 2; const c = col[it.status] || MUTED;
      s += text(x0 - 8, y, it.label, { fill: INK, size: 10.5, anchor: 'end' });
      if (it.milestone) {
        const x = xOf(it.start); s += `<path d="M${x},${y - 7} l7,7 l-7,7 l-7,-7 z" fill="${c}"${tip(it.note)}/>`;
        if (it.moved) { s += `<line x1="${xOf(it.moved)}" x2="${x - 8}" y1="${y}" y2="${y}" stroke="${c}" stroke-width="1.5" stroke-dasharray="3 3"/>`; s += `<path d="M${xOf(it.moved)},${y - 5} l5,5 l-5,5 l-5,-5 z" fill="${SURF}" stroke="${c}" stroke-width="1.5"/>`; }
        s += text(x + 11, y, it.note || '', { fill: INK2, size: 9.5 });
      } else {
        const x1 = xOf(it.start), x2 = xOf(it.end || maxY);
        s += `<rect x="${x1}" y="${y - 7}" width="${Math.max(x2 - x1, 2)}" height="14" rx="4" fill="${c}" opacity="0.85"${tip(it.note)}/>`;
        if (it.note && (x2 - x1) > it.note.length * 5.6 + 8) s += text(x1 + 6, y, it.note, { fill: '#fff', size: 9.5, weight: 600 });
        else if (it.note) s += text(x2 + 6, y, it.note, { fill: INK2, size: 9.5 });
      }
    });
    return s + '</svg>';
  }

  /* ---------- Grouped column (small) ---------- */
  function columns(cats, series, opt = {}) {
    // cats: ['2024','2030'], series: [{name, color, values:[]}]
    const W = opt.width || 720, H = opt.height || 260, padL = 54, padR = 12, padT = 30, padB = 34;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const max = opt.max || niceMax(Math.max(...series.flatMap(s => s.values)));
    const gw = plotW / cats.length, bw = Math.min(24, (gw - 16) / series.length - 2);
    let s = svgOpen(W, H);
    ticks(max, 4).forEach(t => { const y = padT + plotH - plotH * t / max; s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" stroke="${GRID}"/>`; s += text(padL - 6, y, (opt.tickFmt || compact)(t), { fill: MUTED, size: 10, anchor: 'end' }); });
    let lx = padL; if (opt.legend !== false) series.forEach(sr => { s += `<rect x="${lx}" y="${8}" width="10" height="10" rx="2" fill="${sr.color}"/>`; s += text(lx + 14, 13, sr.name, { size: 10.5 }); lx += 14 + sr.name.length * 6.3 + 16; });
    cats.forEach((c, ci) => {
      const gx = padL + gw * ci + (gw - (bw + 2) * series.length) / 2;
      series.forEach((sr, si) => { const v = sr.values[ci] || 0; const h = plotH * v / max; const x = gx + si * (bw + 2), y = padT + plotH - h; s += `<path d="M${x},${y + 4} a4,4 0 0 1 4,-4 h${bw - 8} a4,4 0 0 1 4,4 v${Math.max(h - 4, 0)} h-${bw} z" fill="${sr.color}"${tip(`${sr.name} · ${c}: ${fmt(v)}`)}/>`; if (opt.labels) s += text(x + bw / 2, y - 8, (opt.labelFmt || compact)(v), { size: 10.5, anchor: 'middle' }); });
      s += text(padL + gw * ci + gw / 2, H - 12, c, { fill: INK, size: 11, anchor: 'middle' });
    });
    s += `<line x1="${padL}" x2="${W - padR}" y1="${padT + plotH}" y2="${padT + plotH}" stroke="${AXIS}"/>`;
    return s + '</svg>';
  }


  /* ---------- Signed columns (cash in above the line, cash out below; one series) ---------- */
  function signedColumns(cats, values, opt = {}) {
    // cats: ['Yr 0','Yr 1',...], values: [-15, 6, 7, ...] (null = not disclosed); opt: {width,height,up,down,labels,unit}
    const W = opt.width || 360, H = opt.height || 200, padL = 40, padR = 10, padT = 22, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const real = values.filter(v => v != null);
    const pos = Math.max(0, ...real), neg = Math.max(0, ...real.map(v => -v));
    const step = (() => { const x = Math.max((pos + neg) / 4, 0.0001), mag = Math.pow(10, Math.floor(Math.log10(x))), m = x / mag; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * mag; })();
    const hi = Math.max(step, Math.ceil(pos / step) * step), lo = -Math.ceil(neg / step) * step;
    const yOf = v => padT + plotH * (hi - v) / (hi - lo);
    const up = opt.up || 'var(--s1)', down = opt.down || 'var(--s2)';
    let s = svgOpen(W, H);
    Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, i) => lo + i * step).forEach(t => { const y = yOf(t); s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" stroke="${t === 0 ? AXIS : GRID}"/>`; s += text(padL - 6, y, (opt.tickFmt || compact)(t), { fill: MUTED, size: 10, anchor: 'end' }); });
    const gw = plotW / cats.length, bw = Math.min(30, gw - 8);
    cats.forEach((c, i) => {
      const x = padL + gw * i + (gw - bw) / 2, v = values[i];
      if (v == null) { s += text(padL + gw * i + gw / 2, yOf(0) - 8, 'not disclosed', { fill: MUTED, size: 9.5, anchor: 'middle' }); }
      else if (v !== 0) {
        const y0 = yOf(0), y1 = yOf(v), top = Math.min(y0, y1), h = Math.abs(y1 - y0), r = Math.min(4, h);
        const d = v > 0 ? `M${x},${top + h} v${-(h - r)} a${r},${r} 0 0 1 ${r},${-r} h${bw - 2 * r} a${r},${r} 0 0 1 ${r},${r} v${h - r} z`
                        : `M${x},${top} v${h - r} a${r},${r} 0 0 0 ${r},${r} h${bw - 2 * r} a${r},${r} 0 0 0 ${r},${-r} v${-(h - r)} z`;
        s += `<path d="${d}" fill="${v > 0 ? up : down}"${tip(`${c}: ${v > 0 ? '+' : ''}${fmt(v, 1)}${opt.unit || ''}`)}/>`;
        if (opt.labels !== false) s += text(x + bw / 2, v > 0 ? y1 - 8 : y1 + 9, (v > 0 ? '+' : '') + fmt(v, 1), { fill: INK, size: 10.5, anchor: 'middle', weight: 600 });
      }
      s += text(padL + gw * i + gw / 2, H - 12, c, { fill: INK, size: 11, anchor: 'middle' });
    });
    return s + '</svg>';
  }

  /* ---------- Scorecard bars (0-5 composite) ---------- */
  function scoreBars(items, dims, opt = {}) {
    // items: [{label, region, scores:[..per dim 0-5]}] rendered as stacked score per dim, sorted
    const keys = dims.map((d, i) => ({ key: i, label: d.label, color: d.color }));
    const rows = items.map(it => ({ label: it.label, values: Object.fromEntries(it.scores.map((v, i) => [i, v])) }));
    return stackedH(rows, keys, { width: opt.width || 760, labelW: opt.labelW || 250, max: dims.length * 5, tickFmt: v => v, totalLabel: (t) => t.toFixed(1) + ' / ' + dims.length * 5, rowH: opt.rowH || 24 });
  }

  /* ---------- Tooltip wiring ---------- */
  function wireTooltips(root) {
    const tipEl = document.createElement('div'); tipEl.className = 'tip'; document.body.appendChild(tipEl);
    root.addEventListener('mousemove', e => {
      const t = e.target.closest('[data-tip]'); if (!t) { tipEl.style.opacity = 0; return; }
      tipEl.textContent = t.getAttribute('data-tip'); tipEl.style.opacity = 1; tipEl.style.left = (e.pageX + 12) + 'px'; tipEl.style.top = (e.pageY + 12) + 'px';
    });
    root.addEventListener('mouseleave', () => tipEl.style.opacity = 0);
  }

  /* ---------- Line / area (time series, ≤6 series) ---------- */
  function line(xLabels, series, opt = {}) {
    // xLabels: ['2021','2022',...]; series: [{name, color?, region?, values:[..|null]}]; opt: {area:bool, yFmt, max, height, emphasis:'name', endLabels:false to rely on the legend when end values would collide}
    const W = opt.width || 720, H = opt.height || 300, padL = 56, padR = opt.padR || 120, padT = 30, padB = 34;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const vals = series.flatMap(s => s.values).filter(v => v != null);
    const max = opt.max || niceMax(Math.max(...vals)), min = opt.min || 0;
    const xOf = i => padL + (xLabels.length === 1 ? plotW / 2 : plotW * i / (xLabels.length - 1));
    const yOf = v => padT + plotH - plotH * (v - min) / (max - min);
    let s = svgOpen(W, H);
    ticks(max - min, 4).forEach(t => { const y = yOf(min + t); s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" stroke="${GRID}"/>`; s += text(padL - 8, y, (opt.yFmt || compact)(min + t), { fill: MUTED, size: 10, anchor: 'end' }); });
    xLabels.forEach((l, i) => s += text(xOf(i), H - 10, l, { fill: MUTED, size: 10, anchor: 'middle' }));
    s += `<line x1="${padL}" x2="${W - padR}" y1="${padT + plotH}" y2="${padT + plotH}" stroke="${AXIS}"/>`;
    // legend
    let lx = padL; series.forEach((sr, i) => { const col = sr.color || (sr.region ? regionColor(sr.region) : `var(--s${i + 1})`); s += `<line x1="${lx}" x2="${lx + 14}" y1="${10}" y2="${10}" stroke="${col}" stroke-width="2"/>`; s += text(lx + 18, 10, sr.name, { size: 10.5 }); lx += 18 + sr.name.length * 6.2 + 16; });
    series.forEach((sr, si) => {
      const emph = !opt.emphasis || opt.emphasis === sr.name;
      const col = emph ? (sr.color || (sr.region ? regionColor(sr.region) : `var(--s${si + 1})`)) : 'var(--axis)';
      const pts = sr.values.map((v, i) => v == null ? null : [xOf(i), yOf(v)]);
      let d = '', started = false;
      pts.forEach(p => { if (!p) { started = false; return; } d += (started ? 'L' : 'M') + p[0] + ',' + p[1] + ' '; started = true; });
      if (opt.area && emph) { const first = pts.find(Boolean), last = [...pts].reverse().find(Boolean); s += `<path d="${d}L${last[0]},${padT + plotH} L${first[0]},${padT + plotH} Z" fill="${col}" opacity="0.10"/>`; }
      s += `<path d="${d}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      pts.forEach((p, i) => { if (!p) return; s += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${col}" stroke="${SURF}" stroke-width="2"${tip(`${sr.name} · ${xLabels[i]}: ${fmt(sr.values[i], 1)}`)}/>`; });
      const last = [...pts].reverse().find(Boolean); const li = pts.lastIndexOf(last);
      if (last && emph && opt.endLabels !== false) s += text(last[0] + 9, last[1], `${sr.name} ${(opt.yFmt || compact)(sr.values[li])}`, { fill: INK, size: 10.5 });
    });
    return s + '</svg>';
  }

  /* ---------- Lollipop (ranked magnitude, many rows) ---------- */
  function lollipop(items, opt = {}) {
    const W = opt.width || 720, labelW = opt.labelW || 170, rowH = opt.rowH || 22, padT = 10, padB = 26, valueW = 60;
    const max = opt.max || niceMax(Math.max(...items.map(i => i.value || 0)));
    const x0 = labelW + 8, plotW = W - x0 - valueW - 8; const H = padT + items.length * rowH + padB;
    let s = svgOpen(W, H);
    ticks(max, 4).forEach(t => { const x = x0 + plotW * t / max; s += `<line x1="${x}" x2="${x}" y1="${padT}" y2="${padT + items.length * rowH}" stroke="${GRID}"/>`; s += text(x, H - 8, (opt.tickFmt || fmt)(t), { fill: MUTED, size: 10, anchor: 'middle' }); });
    items.forEach((it, i) => {
      const y = padT + i * rowH + rowH / 2; const col = it.color || (it.region ? regionColor(it.region) : 'var(--s1)');
      s += text(x0 - 8, y, it.label, { fill: INK, size: 11, anchor: 'end' });
      if (it.value == null) { s += text(x0 + 4, y, 'not disclosed', { fill: MUTED, size: 10, extra: ' font-style="italic"' }); return; }
      const x = x0 + plotW * it.value / max;
      s += `<line x1="${x0}" x2="${x}" y1="${y}" y2="${y}" stroke="${col}" stroke-width="2" opacity="0.5"/>`;
      s += `<circle cx="${x}" cy="${y}" r="5" fill="${col}" stroke="${SURF}" stroke-width="2"${tip(`${it.label}: ${fmt(it.value, 1)}`)}/>`;
      s += text(x + 10, y, it.display !== undefined ? it.display : (opt.valFmt || fmt)(it.value), { size: 10.5, extra: ' style="font-variant-numeric:tabular-nums"' });
    });
    return s + '</svg>';
  }

  /* ---------- Slope chart (two periods, direction of change per entity) ---------- */
  function slope(items, opt = {}) {
    // items: [{label, a, b, region?}] ; opt: {aLabel, bLabel, height, fmt}
    const W = opt.width || 520, H = opt.height || 360, padL = 170, padR = 170, padT = 30, padB = 16;
    const x1 = padL, x2 = W - padR; const vals = items.flatMap(i => [i.a, i.b]).filter(v => v != null);
    const max = opt.max || niceMax(Math.max(...vals)), min = opt.min || 0; const yOf = v => padT + (H - padT - padB) * (1 - (v - min) / (max - min));
    let s = svgOpen(W, H);
    s += text(x1, 12, opt.aLabel || 'Before', { fill: INK, size: 11, anchor: 'middle', weight: 600 }) + text(x2, 12, opt.bLabel || 'After', { fill: INK, size: 11, anchor: 'middle', weight: 600 });
    s += `<line x1="${x1}" x2="${x1}" y1="${padT}" y2="${H - padB}" stroke="${GRID}"/><line x1="${x2}" x2="${x2}" y1="${padT}" y2="${H - padB}" stroke="${GRID}"/>`;
    const f = opt.fmt || (v => fmt(v, 1));
    items.forEach(it => {
      if (it.a == null || it.b == null) return;
      const col = it.color || (it.region ? regionColor(it.region) : 'var(--s1)');
      s += `<line x1="${x1}" x2="${x2}" y1="${yOf(it.a)}" y2="${yOf(it.b)}" stroke="${col}" stroke-width="2" opacity="0.7"${tip(`${it.label}: ${f(it.a)} → ${f(it.b)}`)}/>`;
      s += `<circle cx="${x1}" cy="${yOf(it.a)}" r="4.5" fill="${col}" stroke="${SURF}" stroke-width="2"/><circle cx="${x2}" cy="${yOf(it.b)}" r="4.5" fill="${col}" stroke="${SURF}" stroke-width="2"/>`;
      s += text(x1 - 10, yOf(it.a), `${it.label}  ${f(it.a)}`, { fill: INK, size: 10.5, anchor: 'end' }) + text(x2 + 10, yOf(it.b), `${f(it.b)}  ${it.label}`, { fill: INK, size: 10.5 });
    });
    return s + '</svg>';
  }

  /* ---------- Bullet / meter (value vs target within a limit) ---------- */
  function bullet(items, opt = {}) {
    // items: [{label, value, target?, max?, region?}] ; same-ramp track (light step of the fill's hue)
    const W = opt.width || 640, labelW = opt.labelW || 190, rowH = opt.rowH || 30, padT = 8, padB = 8, valueW = opt.valueW || 70;
    const x0 = labelW + 8, plotW = W - x0 - valueW - 8; const H = padT + items.length * rowH + padB;
    let s = svgOpen(W, H);
    items.forEach((it, i) => {
      const y = padT + i * rowH + Math.max(2, (rowH - 16) / 2), max = it.max || opt.max || 100;
      s += text(x0 - 8, y + 8, it.label, { fill: INK, size: 11, anchor: 'end' });
      s += `<rect x="${x0}" y="${y}" width="${plotW}" height="16" rx="4" fill="${opt.track || 'var(--seq100)'}"/>`;
      if (it.value != null) s += `<rect x="${x0}" y="${y + 3}" width="${Math.max(2, plotW * Math.min(it.value, max) / max)}" height="10" rx="3" fill="${it.color || (it.region ? regionColor(it.region) : 'var(--seq550)')}"${tip(`${it.label}: ${fmt(it.value, 1)}${it.target != null ? ' (target ' + fmt(it.target, 1) + ')' : ''}`)}/>`;
      if (it.target != null) { const tx = x0 + plotW * it.target / max; s += `<line x1="${tx}" x2="${tx}" y1="${y - 2}" y2="${y + 18}" stroke="${INK}" stroke-width="2"/>`; }
      s += text(x0 + plotW + 8, y + 8, it.value == null ? 'not disclosed' : (it.display || (opt.valFmt || (v => fmt(v, 1)))(it.value)), { fill: it.value == null ? MUTED : INK2, size: 10.5, extra: it.value == null ? ' font-style="italic"' : '' });
    });
    return s + '</svg>';
  }

  /* ---------- Waffle (part-to-whole, ≤6 parts, 100 cells) ---------- */
  function waffle(parts, opt = {}) {
    // parts: [{label, value, color?}] values sum to any total; renders 10x10
    const total = parts.reduce((a, p) => a + p.value, 0), cell = opt.cell || 16, gap = 3, cols = 10;
    const legendW = opt.legendW || 240, W = cols * (cell + gap) + legendW, H = 10 * (cell + gap) + 4;
    let s = svgOpen(W, H), k = 0;
    const cells = []; parts.forEach((p, pi) => { const n = Math.round(p.value / total * 100); for (let i = 0; i < n && cells.length < 100; i++) cells.push(pi); });
    while (cells.length < 100) cells.push(parts.length - 1);
    cells.forEach((pi, i) => { const c = i % cols, r = Math.floor(i / cols); const p = parts[pi]; s += `<rect x="${c * (cell + gap)}" y="${(9 - r) * (cell + gap)}" width="${cell}" height="${cell}" rx="3" fill="${p.color || `var(--s${pi + 1})`}"${tip(`${p.label}: ${Math.round(p.value / total * 100)}%`)}/>`; });
    let ly = 10; parts.forEach((p, pi) => { const x = cols * (cell + gap) + 16; s += `<rect x="${x}" y="${ly - 6}" width="12" height="12" rx="3" fill="${p.color || `var(--s${pi + 1})`}"/>`; s += text(x + 18, ly, `${p.label} · ${Math.round(p.value / total * 100)}%`, { fill: INK, size: 11 }); ly += 22; });
    return s + '</svg>';
  }

  /* ---------- Heatmap (continuous magnitude on a grid, sequential ramp) ---------- */
  function heatmap(rows, cols, values, opt = {}) {
    // values[r][c] number|null ; sequential blue ramp light→dark; opt: {fmt, labelW, cell}
    const RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'];
    const labelW = opt.labelW || 160, cell = opt.cell || 30, headH = opt.headH || 70, W = labelW + cols.length * cell + 20, H = headH + rows.length * cell + 40;
    const flat = values.flat().filter(v => v != null); const max = opt.max || Math.max(...flat), min = opt.min || Math.min(...flat);
    const f = opt.fmt || compact;
    let s = svgOpen(W, H);
    cols.forEach((c, i) => { const x = labelW + i * cell + cell / 2; s += `<text x="${x}" y="${headH - 8}" fill="${INK2}" font-size="10" transform="rotate(-45 ${x} ${headH - 8})">${esc(c)}</text>`; });
    rows.forEach((r, ri) => {
      s += text(labelW - 8, headH + ri * cell + cell / 2, r, { fill: INK, size: 11, anchor: 'end' });
      cols.forEach((c, ci) => {
        const v = values[ri][ci]; const x = labelW + ci * cell, y = headH + ri * cell;
        if (v == null) { s += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="3" fill="var(--zebra)"/>`; return; }
        const t = (v - min) / ((max - min) || 1); const col = RAMP[Math.round(t * (RAMP.length - 1))];
        s += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="3" fill="${col}"${tip(`${r} · ${c}: ${f(v)}`)}/>`;
        if (opt.labels && cell >= 26) s += text(x + cell / 2, y + cell / 2, f(v), { fill: t > 0.55 ? '#fff' : INK, size: 9.5, anchor: 'middle' });
      });
    });
    // scale legend
    const ly = H - 18, lx = labelW; RAMP.forEach((c, i) => s += `<rect x="${lx + i * 14}" y="${ly}" width="14" height="10" fill="${c}"/>`);
    s += text(lx, ly - 5, f(min), { fill: MUTED, size: 9.5 }) + text(lx + RAMP.length * 14, ly - 5, f(max), { fill: MUTED, size: 9.5, anchor: 'end' });
    return s + '</svg>';
  }


  /* ---------- Diverging heatmap (signed magnitude on a grid: correlations, deviations). blue <-> red, gray midpoint ---------- */
  function divHeatmap(rows, cols, values, opt = {}) {
    // values[r][c] number|null in [-lim, lim]; opt: {lim, fmt, labelW, cell, headH, labels}
    const BLUE = ['#0d366b', '#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];
    const RED = ['#fbd7d7', '#f5b0af', '#ee8a89', '#e66767', '#e34948', '#c93a39', '#a52d2c'];
    const MID = '#f0efec';
    const RAMP = BLUE.concat([MID], RED); // 15 steps, gray exactly at the centre
    const labelW = opt.labelW || 160, cell = opt.cell || 30, headH = opt.headH || 70, W = labelW + cols.length * cell + 20, H = headH + rows.length * cell + 44;
    const flat = values.flat().filter(v => v != null); const lim = opt.lim || Math.max(...flat.map(Math.abs)) || 1;
    const f = opt.fmt || (v => (Math.round(v * 100) / 100).toFixed(2));
    let s = svgOpen(W, H);
    cols.forEach((c, i) => { const x = labelW + i * cell + cell / 2; s += `<text x="${x}" y="${headH - 8}" fill="${INK2}" font-size="10" transform="rotate(-45 ${x} ${headH - 8})">${esc(c)}</text>`; });
    rows.forEach((r, ri) => {
      s += text(labelW - 8, headH + ri * cell + cell / 2, r, { fill: INK, size: 11, anchor: 'end' });
      cols.forEach((c, ci) => {
        const v = values[ri][ci]; const x = labelW + ci * cell, y = headH + ri * cell;
        if (v == null) { s += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="3" fill="var(--zebra)"/>`; return; }
        const t = Math.max(-1, Math.min(1, v / lim)); const col = RAMP[Math.round((t + 1) / 2 * (RAMP.length - 1))];
        s += `<rect x="${x + 1}" y="${y + 1}" width="${cell - 2}" height="${cell - 2}" rx="3" fill="${col}"${tip(`${r} · ${c}: ${f(v)}`)}/>`;
        if (opt.labels && cell >= 26) s += text(x + cell / 2, y + cell / 2, f(v), { fill: Math.abs(t) > 0.55 ? '#fff' : INK, size: 9.5, anchor: 'middle' });
      });
    });
    const ly = H - 18, lx = labelW; RAMP.forEach((c, i) => s += `<rect x="${lx + i * 14}" y="${ly}" width="14" height="10" fill="${c}"/>`);
    s += text(lx, ly - 5, f(-lim), { fill: MUTED, size: 9.5 }) + text(lx + RAMP.length * 7, ly - 5, '0', { fill: MUTED, size: 9.5, anchor: 'middle' }) + text(lx + RAMP.length * 14, ly - 5, f(lim), { fill: MUTED, size: 9.5, anchor: 'end' });
    return s + '</svg>';
  }

  /* ---------- Treemap (squarified, part-to-whole with many parts; ≤8 colors, rest folded) ---------- */
  function treemap(items, opt = {}) {
    // items: [{label, value, color?, group?}] ; colors by group slot or single hue with lightness by size
    const W = opt.width || 720, H = opt.height || 360, gap = 2;
    const data = items.filter(i => i.value > 0).sort((a, b) => b.value - a.value);
    const total = data.reduce((a, i) => a + i.value, 0);
    const rects = []; let x = 0, y = 0, w = W, h = H, rest = data.slice();
    while (rest.length) {
      const horiz = w >= h; let row = [], rowSum = 0, best = Infinity;
      for (const it of rest) {
        const cand = [...row, it], sum = rowSum + it.value, side = horiz ? h : w, len = (sum / total) * (W * H) / side;
        const worst = Math.max(...cand.map(c => { const a = (c.value / total) * W * H, b = a / len; return Math.max(len / b, b / len); }));
        if (worst > best && row.length) break; row = cand; rowSum = sum; best = worst;
      }
      const len = (rowSum / total) * (W * H) / (horiz ? h : w); let off = 0;
      row.forEach(it => { const a = (it.value / total) * W * H; const b = a / len; rects.push(horiz ? { it, x, y: y + off, w: len, h: b } : { it, x: x + off, y, w: b, h: len }); off += b; });
      if (horiz) { x += len; w -= len; } else { y += len; h -= len; }
      rest = rest.slice(row.length);
    }
    let s = svgOpen(W, H);
    rects.forEach((r, i) => {
      const col = r.it.color || (r.it.group ? regionColor(r.it.group) : ['var(--seq700)', 'var(--seq550)', 'var(--seq450)', 'var(--seq400)', 'var(--seq350)', 'var(--seq300)', 'var(--seq250)', 'var(--seq200)'][Math.min(i, 7)]);
      s += `<rect x="${r.x + gap / 2}" y="${r.y + gap / 2}" width="${Math.max(r.w - gap, 0)}" height="${Math.max(r.h - gap, 0)}" rx="3" fill="${col}"${tip(`${r.it.label}: ${fmt(r.it.value, 1)} (${(r.it.value / total * 100).toFixed(1)}%)`)}/>`;
      if (r.w > 60 && r.h > 28) { s += text(r.x + 8, r.y + 14, r.it.label, { fill: '#fff', size: 11, weight: 600 }); s += text(r.x + 8, r.y + 28, `${(r.it.value / total * 100).toFixed(1)}%`, { fill: '#fff', size: 10 }); }
    });
    return s + '</svg>';
  }

  /* ---------- Small multiples: sparkline rows (one series per row, shared scale optional) ---------- */
  function sparkRows(items, opt = {}) {
    // items: [{label, values:[...], region?}] ; last point in accent, rest in de-emphasis gray
    const W = opt.width || 720, labelW = opt.labelW || 170, rowH = 30, sw = opt.sparkW || 200, sh = 18, padT = 6;
    const H = padT + items.length * rowH + 6; const shared = opt.shared ? Math.max(...items.flatMap(i => i.values)) : null;
    let s = svgOpen(W, H);
    items.forEach((it, i) => {
      const y = padT + i * rowH; const mx = shared || Math.max(...it.values), mn = opt.zero ? 0 : Math.min(...it.values);
      const xOf = k => labelW + 8 + sw * k / (it.values.length - 1), yOf = v => y + 4 + sh - sh * (v - mn) / ((mx - mn) || 1);
      s += text(labelW, y + 4 + sh / 2, it.label, { fill: INK, size: 11, anchor: 'end' });
      s += `<path d="${it.values.map((v, k) => (k ? 'L' : 'M') + xOf(k) + ',' + yOf(v)).join(' ')}" fill="none" stroke="var(--axis)" stroke-width="2" stroke-linejoin="round"/>`;
      const lk = it.values.length - 1, col = it.region ? regionColor(it.region) : 'var(--s1)';
      s += `<circle cx="${xOf(lk)}" cy="${yOf(it.values[lk])}" r="4" fill="${col}" stroke="${SURF}" stroke-width="2"${tip(`${it.label}: ${fmt(it.values[lk], 1)}`)}/>`;
      const d = it.values[lk] - it.values[0];
      s += text(labelW + 8 + sw + 12, y + 4 + sh / 2, `${(opt.valFmt || (v => fmt(v, 1)))(it.values[lk])}  ${d >= 0 ? '▲' : '▼'} ${fmt(Math.abs(d), 1)}`, { fill: d >= 0 ? (opt.upGood === false ? 'var(--st-critical)' : '#006300') : (opt.upGood === false ? '#006300' : 'var(--st-critical)'), size: 10.5, extra: ' style="font-variant-numeric:tabular-nums"' });
    });
    return s + '</svg>';
  }

  /* ---------- Stat tile row (when the form is a number, not a chart) ---------- */
  function statTiles(tiles) {
    // tiles: [{label, value, delta?, deltaGood?, note?, spark?:[..], status?:{label}}] -> HTML (not SVG)
    // A missing value (null, undefined or '') reads "not disclosed", never 0. `status` marks a tile
    // that crossed a limit: class "alert" plus a written label, so colour is never the only signal.
    return '<div class="gc-tiles">' + tiles.map(t => {
      const na = t.value === null || t.value === undefined || t.value === '';
      const st = t.status ? `<div class="gc-status">${esc(t.status.label || 'Above the alert level')}</div>` : '';
      let sp = '';
      if (t.spark && t.spark.length > 1) { const w = 90, h = 24, mx = Math.max(...t.spark), mn = Math.min(...t.spark); const pts = t.spark.map((v, i) => `${w * i / (t.spark.length - 1)},${h - h * (v - mn) / ((mx - mn) || 1)}`); sp = `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" style="overflow:visible"><polyline points="${pts.join(' ')}" fill="none" stroke="var(--axis)" stroke-width="2" stroke-linejoin="round"/><circle cx="${w}" cy="${pts[pts.length - 1].split(',')[1]}" r="3.5" fill="var(--s1)"/></svg>`; }
      const dl = t.delta != null ? `<span class="gc-delta ${t.delta >= 0 ? (t.deltaGood === false ? 'bad' : 'good') : (t.deltaGood === false ? 'good' : 'bad')}">${t.delta >= 0 ? '▲' : '▼'} ${esc(String(Math.abs(t.delta)))}${t.deltaUnit || '%'}</span>` : '';
      return `<div class="gc-tile${t.status ? ' alert' : ''}"><div class="l">${esc(t.label)}</div><div class="v${na ? ' na' : ''}">${na ? 'not disclosed' : esc(String(t.value))}</div><div class="row">${dl}${sp}</div>${st}${t.note ? `<div class="d">${esc(t.note)}</div>` : ''}</div>`;
    }).join('') + '</div>';
  }


  /* ---------- Scatter / quadrant (two measures per item; optional dividers turn it into a 2x2) ---------- */
  function scatter(points, opt = {}) {
    // points: [{label, x, y, color?, region?, tip?, bold?, ring?, pos?:'right'|'left'|'top'|'bottom'}]
    // opt: {xLabel, yLabel, xMin, xMax, yMin, yMax, xLog, xReverse, xDiv, yDiv, quads:[topLeft, topRight, bottomLeft, bottomRight], xFmt, yFmt, xTicks[], yTicks[], width, height}
    const W = opt.width || 640, H = opt.height || 420, padL = 58, padR = 24, padT = 16, padB = 46;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const xs = points.map(p => p.x).filter(v => v != null), ys = points.map(p => p.y).filter(v => v != null);
    const xMin = opt.xMin ?? Math.min(0, ...xs), xMax = opt.xMax ?? niceMax(Math.max(...xs));
    const yMin = opt.yMin ?? Math.min(0, ...ys), yMax = opt.yMax ?? niceMax(Math.max(...ys));
    const tx = v => opt.xLog ? Math.log10(v) : v;
    let fx = v => (tx(v) - tx(xMin)) / (tx(xMax) - tx(xMin)); if (opt.xReverse) { const f0 = fx; fx = v => 1 - f0(v); }
    const xOf = v => padL + plotW * fx(v), yOf = v => padT + plotH - plotH * (v - yMin) / (yMax - yMin);
    const xf = opt.xFmt || (v => fmt(v, 1)), yf = opt.yFmt || (v => fmt(v, 1));
    let s = svgOpen(W, H);
    const yT = opt.yTicks || ticks(yMax - yMin, 4).map(t => yMin + t);
    const xT = opt.xTicks || ticks(xMax - xMin, 4).map(t => xMin + t);
    yT.forEach(t => { const y = yOf(t); s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" stroke="${GRID}"/>`; s += text(padL - 8, y, yf(t), { fill: MUTED, size: 10, anchor: 'end' }); });
    xT.forEach(t => { const x = xOf(t); s += `<line x1="${x}" x2="${x}" y1="${padT}" y2="${padT + plotH}" stroke="${GRID}"/>`; s += text(x, padT + plotH + 14, xf(t), { fill: MUTED, size: 10, anchor: 'middle' }); });
    s += `<line x1="${padL}" x2="${W - padR}" y1="${padT + plotH}" y2="${padT + plotH}" stroke="${AXIS}"/><line x1="${padL}" x2="${padL}" y1="${padT}" y2="${padT + plotH}" stroke="${AXIS}"/>`;
    if (opt.xLabel) s += text(padL + plotW / 2, H - 8, opt.xLabel, { fill: INK2, size: 10.5, anchor: 'middle' });
    if (opt.yLabel) s += `<text x="14" y="${padT + plotH / 2}" fill="${INK2}" font-size="10.5" text-anchor="middle" dominant-baseline="middle" transform="rotate(-90 14 ${padT + plotH / 2})">${esc(opt.yLabel)}</text>`;
    // dividers and quadrant names
    if (opt.xDiv != null) { const x = xOf(opt.xDiv); s += `<line x1="${x}" x2="${x}" y1="${padT}" y2="${padT + plotH}" stroke="${INK2}" stroke-width="1.25"/>`; }
    if (opt.yDiv != null) { const y = yOf(opt.yDiv); s += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}" stroke="${INK2}" stroke-width="1.25"/>`; }
    if (opt.quads) {
      const q = opt.quads, l = padL + 8, r = W - padR - 8, t = padT + 12, b = padT + plotH - 10;
      if (q[0]) s += text(l, t, q[0], { fill: INK2, size: 11, weight: 600 });
      if (q[1]) s += text(r, t, q[1], { fill: INK2, size: 11, weight: 600, anchor: 'end' });
      const bq = opt.yDiv != null ? yOf(opt.yDiv) + 13 : b; // bottom names sit just under the divider, clear of points near the axis
      if (q[2]) s += text(l, bq, q[2], { fill: INK2, size: 11, weight: 600 });
      if (q[3]) s += text(r, bq, q[3], { fill: INK2, size: 11, weight: 600, anchor: 'end' });
    }
    points.forEach((p, i) => {
      if (p.x == null || p.y == null) return;
      const x = xOf(p.x), y = yOf(p.y), col = p.color || (p.region ? regionColor(p.region) : 'var(--s1)');
      if (p.ring) s += `<circle cx="${x}" cy="${y}" r="10" fill="none" stroke="${col}" stroke-width="1.5"/>`;
      s += `<circle cx="${x}" cy="${y}" r="5.5" fill="${col}" stroke="${SURF}" stroke-width="2"${tip(p.tip || `${p.label}: ${xf(p.x)}, ${yf(p.y)}`)}/>`;
      const pos = p.pos || 'right', o = p.ring ? 15 : 10;
      const L = pos === 'left' ? [x - o, y, 'end'] : pos === 'top' ? [x, y - o - 2, 'middle'] : pos === 'bottom' ? [x, y + o + 4, 'middle'] : [x + o, y, 'start'];
      s += text(L[0], L[1], p.label, { fill: INK, size: 11, weight: p.bold ? 650 : 400, anchor: L[2] });
    });
    return s + '</svg>';
  }

  global.Charts = { divHeatmap, scatter, hbar, stackedH, ribbonRank, sankey, matrix, moveCards, dotTimeline, dumbbell, divergingBar, gantt, columns, signedColumns, scoreBars, line, lollipop, slope, bullet, waffle, heatmap, treemap, sparkRows, statTiles, wireTooltips, fmt, compact, regionColor };
})(window);
