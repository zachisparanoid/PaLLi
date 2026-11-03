/* PaLLi dashboard suite runtime: fetch loop, theme, chart helpers. */
window.Suite = (function () {
  const NS = "http://www.w3.org/2000/svg";
  const S = (tag, at = {}) => { const e = document.createElementNS(NS, tag);
    for (const k in at) e.setAttribute(k, at[k]); return e; };
  const el = (tag, cls, html) => { const e = document.createElement(tag);
    if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
  const sum = a => a.reduce((x, y) => x + (y || 0), 0);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

  /* ---------- theme ---------- */
  function themeInit() {
    let mode = "auto";
    try { mode = localStorage.getItem("palli-theme") || "auto"; } catch (e) {}
    const btn = document.getElementById("themebtn");
    apply(mode);
    if (btn) btn.addEventListener("click", () => {
      mode = { auto: "light", light: "dark", dark: "auto" }[mode];
      try { localStorage.setItem("palli-theme", mode); } catch (e) {}
      apply(mode);
    });
    function apply(m) {
      if (m === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", m);
      if (btn) btn.textContent = { auto: "◐ auto", light: "○ light", dark: "● dark" }[m];
    }
  }

  /* ---------- boot: fetch + refresh + ticker ---------- */
  function boot(render) {
    themeInit();
    let lastAt = null, failing = false;
    const dot = document.getElementById("livedot"), upd = document.getElementById("updated");
    async function load() {
      try {
        const r = await fetch("/api/stats", { cache: "no-store" });
        const data = await r.json();
        window.DATA = data;
        const main = document.querySelector("main");
        render(data);
        if (main) { main.classList.remove("fresh"); void main.offsetWidth; main.classList.add("fresh"); }
        lastAt = Date.now(); failing = false;
      } catch (e) { failing = true; console.error("stats fetch failed", e); }
    }
    setInterval(() => {
      if (!dot || !upd) return;
      dot.classList.toggle("err", failing);
      if (failing) { upd.textContent = "offline — retrying"; return; }
      if (lastAt == null) { upd.textContent = "loading…"; return; }
      const s = Math.round((Date.now() - lastAt) / 1000);
      upd.textContent = "live · updated " + (s < 60 ? s + "s" : Math.round(s / 60) + "m") + " ago";
    }, 1000);
    load();
    setInterval(load, 60000);
  }

  /* ---------- charts ---------- */
  function barList(host, rows) { /* rows: [{name, v, cls, suffix}] */
    host.innerHTML = "";
    const max = Math.max(...rows.map(r => r.v), 1);
    rows.forEach(r => {
      const d = el("div", "bar-row" + (r.cls ? " " + r.cls : ""));
      d.innerHTML = `<span class="bn">${esc(r.name)}${r.suffix || ""}</span>` +
        `<div class="track"><div class="fill" style="width:${(100 * r.v / max).toFixed(1)}%"></div></div>` +
        `<span class="bv">${r.v}</span>`;
      host.appendChild(d);
    });
  }

  function heatmap(host, days, grid, quietDay) {
    host.innerHTML = "";
    const max = Math.max(1, ...days.map(d => Math.max(...(grid[d] || [0]))));
    days.forEach(d => {
      host.appendChild(el("div", "lab", esc(d)));
      const row = el("div", "row" + (d === quietDay ? " quietday" : ""));
      (grid[d] || Array(24).fill(0)).forEach((v, h) => {
        const c = el("div", "c" + (v === 0 ? " z" : ""));
        if (v > 0) c.style.opacity = (0.18 + 0.82 * Math.min(v, max) / max).toFixed(2);
        c.title = `${d} · ${String(h).padStart(2, "0")}:00 — ${v} thought${v === 1 ? "" : "s"}`;
        row.appendChild(c);
      });
      host.appendChild(row);
    });
    host.appendChild(el("div"));
    const hl = el("div", "hlab");
    for (let h = 0; h < 24; h++) hl.appendChild(el("span", "", h % 3 === 0 ? h : ""));
    host.appendChild(hl);
  }

  function hourStrip(host, labelsHost, vals) {
    host.innerHTML = ""; labelsHost.innerHTML = "";
    const max = Math.max(...vals, 1);
    const crest = [...vals].sort((a, b) => b - a)[3] || max;
    vals.forEach((v, h) => {
      const b = el("div", "hbar" + (v >= crest && v > 0 ? " crest" : ""));
      b.style.height = Math.max(4, 100 * v / max) + "%";
      b.title = `${String(h).padStart(2, "0")}:00 — ${v}`;
      host.appendChild(b);
    });
    for (let h = 0; h < 24; h++) labelsHost.appendChild(el("span", "", h % 3 === 0 ? h : ""));
  }

  function axisY(svg, P, W, y, ticks, labels) {
    ticks.forEach((g, i) => {
      svg.appendChild(S("line", { x1: P.l, x2: W - P.r, y1: y(g), y2: y(g), stroke: "var(--line-2)" }));
      const t = S("text", { x: P.l - 6, y: y(g) + 3, class: "axis", "text-anchor": "end" });
      t.textContent = labels ? labels[i] : g; svg.appendChild(t);
    });
  }
  function axisX(svg, days, x, H, every = 3, mid = 0) {
    days.forEach((d, i) => { if (i % every === 0) {
      const t = S("text", { x: x(i) + mid, y: H - 2, class: "axis", "text-anchor": "middle" });
      t.textContent = d.slice(3); svg.appendChild(t); } });
  }

  function layers(host, days, D) { /* D: {hold, msgs_him, mono, bench} */
    host.innerHTML = "";
    const W = 1400, H = 290, P = { l: 34, r: 10, t: 14, b: 26 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
    const n = days.length, x = i => P.l + iw * (i + 0.5) / n;
    const maxY = Math.max(50, ...D.hold, ...D.msgs_him, ...D.mono) + 2;
    const y = v => P.t + ih * (1 - Math.min(v, maxY) / maxY);
    const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
    axisY(svg, P, W, y, [0, 10, 20, 30, 40, 50]);
    let dA = `M ${x(0)} ${y(D.hold[0])}`;
    D.hold.forEach((v, i) => { if (i) dA += ` L ${x(i)} ${y(v)}`; });
    const area = S("path", { d: dA + ` L ${x(n - 1)} ${y(0)} L ${x(0)} ${y(0)} Z` });
    area.style.fill = "var(--accent)"; area.style.opacity = ".10"; svg.appendChild(area);
    const aline = S("path", { d: dA, fill: "none", "stroke-width": 1.2, "stroke-dasharray": "3 3" });
    aline.style.stroke = "var(--accent)"; aline.style.opacity = ".55"; svg.appendChild(aline);
    D.msgs_him.forEach((v, i) => { if (!v) return;
      const b = S("rect", { x: x(i) - 11, y: y(v), width: 22, height: y(0) - y(v), rx: 3 });
      b.style.fill = "var(--him)"; b.style.opacity = ".6";
      const t = S("title"); t.textContent = `${days[i]} — ${v} msgs from you`; b.appendChild(t); svg.appendChild(b); });
    let dM = `M ${x(0)} ${y(D.mono[0])}`;
    D.mono.forEach((v, i) => { if (i) dM += ` L ${x(i)} ${y(v)}`; });
    const ml = S("path", { d: dM, fill: "none", "stroke-width": 2.4, "stroke-linejoin": "round", "stroke-linecap": "round" });
    ml.style.stroke = "var(--spark)"; svg.appendChild(ml);
    D.mono.forEach((v, i) => { const c = S("circle", { cx: x(i), cy: y(v), r: 3 }); c.style.fill = "var(--spark)";
      const t = S("title"); t.textContent = `${days[i]} — ${v} thoughts`; c.appendChild(t); svg.appendChild(c); });
    D.bench.forEach((v, i) => { const c = S("circle", { cx: x(i), cy: y(0) + 14, r: 3 + v }); c.style.fill = "var(--good)"; c.style.opacity = ".85";
      const t = S("title"); t.textContent = `${days[i]} — ${v} bench sessions`; c.appendChild(t); svg.appendChild(c); });
    axisX(svg, days, x, H, 1);
    host.appendChild(svg);
  }

  function stacked(host, days, say, hold) {
    host.innerHTML = "";
    const W = 430, H = 210, P = { l: 30, r: 6, t: 10, b: 22 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
    const n = days.length, max = Math.max(...days.map((_, i) => say[i] + hold[i]), 10) + 4;
    const x = i => P.l + iw * i / n + 2, bw = iw / n - 4, y = v => P.t + ih * (1 - v / max);
    const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
    axisY(svg, P, W, y, [0, Math.round(max / 2), max - 4].map(v => Math.round(v)));
    for (let i = 0; i < n; i++) {
      const h = hold[i], s = say[i];
      const hb = S("rect", { x: x(i), y: y(h), width: bw, height: y(0) - y(h), rx: 2 });
      hb.style.fill = "var(--line)"; svg.appendChild(hb);
      if (s) { const sb = S("rect", { x: x(i), y: y(h + s), width: bw, height: y(0) - y(s), rx: 2 });
        sb.style.fill = "var(--accent)"; svg.appendChild(sb); }
      const t = S("title"); t.textContent = `${days[i]} — say ${s}, hold ${h}`; hb.appendChild(t);
    }
    axisX(svg, days, i => x(i) + bw / 2, H, 3);
    host.appendChild(svg);
  }

  function line(host, days, vals, opts = {}) {
    host.innerHTML = "";
    const W = 430, H = 210, P = { l: 34, r: 8, t: 12, b: 22 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
    const n = days.length, max = opts.max || Math.max(...vals.filter(v => v != null), 10) + 10;
    const x = i => P.l + iw * (i + 0.5) / n, y = v => P.t + ih * (1 - v / max);
    const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
    axisY(svg, P, W, y, [0, Math.round(max / 2), max - 10]);
    if (opts.markerAfter != null) {
      const gx = x(opts.markerAfter) + (x(opts.markerAfter + 1) - x(opts.markerAfter)) * 0.55;
      svg.appendChild(S("line", { x1: gx, x2: gx, y1: P.t, y2: y(0), stroke: "var(--good)", "stroke-width": 1.4, "stroke-dasharray": "4 3" }));
      const gt = S("text", { x: gx - 4, y: P.t + 10, class: "axis", "text-anchor": "end" });
      gt.textContent = opts.markerLabel || ""; gt.style.fill = "var(--good)"; svg.appendChild(gt);
    }
    let d = "", pen = false;
    vals.forEach((v, i) => { if (v == null) { pen = false; return; }
      d += (pen ? " L" : " M") + ` ${x(i)} ${y(v)}`; pen = true; });
    const l = S("path", { d, fill: "none", "stroke-width": 2.2, "stroke-linejoin": "round" });
    l.style.stroke = "var(--spark)"; svg.appendChild(l);
    vals.forEach((v, i) => { if (v == null) return;
      const c = S("circle", { cx: x(i), cy: y(v), r: 3 }); c.style.fill = "var(--spark)";
      const t = S("title"); t.textContent = `${days[i]} — ${v}${opts.unit || ""}`; c.appendChild(t); svg.appendChild(c); });
    axisX(svg, days, x, H, 3);
    host.appendChild(svg);
  }

  function grouped(host, days, seriesArr) { /* [{vals, color, name}] */
    host.innerHTML = "";
    const W = 430, H = 210, P = { l: 26, r: 6, t: 12, b: 22 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
    const n = days.length, k = seriesArr.length;
    const max = Math.max(3, ...seriesArr.flatMap(s => s.vals));
    const x = i => P.l + iw * i / n + 2, bw = (iw / n - 6) / k, y = v => P.t + ih * (1 - v / max);
    const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
    axisY(svg, P, W, y, Array.from({ length: max + 1 }, (_, i) => i).filter(v => v <= 4 || v % 2 === 0));
    for (let i = 0; i < n; i++) seriesArr.forEach((s, j) => {
      const v = s.vals[i]; if (!v) return;
      const b = S("rect", { x: x(i) + j * bw, y: y(v), width: Math.max(bw - 1, 1), height: y(0) - y(v), rx: 2 });
      b.style.fill = s.color;
      const t = S("title"); t.textContent = `${days[i]} — ${s.name}: ${v}`; b.appendChild(t); svg.appendChild(b);
    });
    axisX(svg, days, i => x(i) + bw * k / 2, H, 3);
    host.appendChild(svg);
  }

  function gantt(host, days, projects) {
    host.innerHTML = "";
    projects.forEach(([name, perDay, total]) => {
      host.appendChild(el("div", "p", esc(name)));
      const row = el("div", "days");
      days.forEach(d => {
        const v = perDay[d] || 0;
        const c = el("div", "d" + (v ? " t" + Math.min(v, 3) : ""));
        c.title = `${name} · ${d}` + (v ? ` — ${v} file${v > 1 ? "s" : ""} touched` : " — untouched");
        row.appendChild(c);
      });
      host.appendChild(row);
      host.appendChild(el("div", "tot", total + " files"));
    });
  }

  function emoChart(host, pts, year) { /* pts: [["MM-DD HH:MM", w]] */
    host.innerHTML = "";
    if (!pts.length) return;
    const W = 1400, H = 200, P = { l: 36, r: 12, t: 16, b: 24 }, iw = W - P.l - P.r, ih = H - P.t - P.b;
    const T = md => Date.parse(`${year}-${md.replace(" ", "T")}:00`);
    const t0 = T(pts[0][0]) - 36e5 * 6, t1 = Date.now() + 36e5 * 2;
    const px = md => P.l + iw * (T(md) - t0) / (t1 - t0), y = v => P.t + ih * (1 - v);
    const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
    axisY(svg, P, W, y, [0, 0.5, 1], ["calm", "0.5", "heavy"]);
    let d = ""; pts.forEach(([md, w], i) => { d += (i ? " L" : "M") + ` ${px(md).toFixed(1)} ${y(w).toFixed(1)}`; });
    const lp = S("path", { d, fill: "none", "stroke-width": 1.6, "stroke-linejoin": "round" });
    lp.style.stroke = "var(--spark)"; lp.style.opacity = ".7"; svg.appendChild(lp);
    pts.forEach(([md, w]) => {
      const c = S("circle", { cx: px(md), cy: y(w), r: w >= 0.4 ? 5 : 3.5 });
      c.style.fill = w >= 0.4 ? "var(--warn)" : "var(--spark)";
      const t = S("title"); t.textContent = `${md} — weight ${w}`; c.appendChild(t); svg.appendChild(c);
    });
    const d0 = new Date(t0), d1 = new Date(t1);
    for (let dt = new Date(d0); dt <= d1; dt.setDate(dt.getDate() + 1)) {
      if (dt.getDate() % 2 === 0) {
        const md = String(dt.getMonth() + 1).padStart(2, "0") + "-" + String(dt.getDate()).padStart(2, "0");
        const t = S("text", { x: px(md + " 12:00"), y: H - 2, class: "axis", "text-anchor": "middle" });
        t.textContent = md; svg.appendChild(t);
      }
    }
    host.appendChild(svg);
  }

  function spark(host, vals) {
    host.innerHTML = "";
    const W = 200, H = 34, pad = 3, clean = vals.map(v => v == null ? 0 : v);
    const min = Math.min(...clean), max = Math.max(...clean), rng = (max - min) || 1;
    const pts = clean.map((v, i) => [pad + (W - 2 * pad) * i / (clean.length - 1),
      pad + (H - 2 * pad) * (1 - (v - min) / rng)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", preserveAspectRatio: "none" });
    svg.style.height = "34px";
    const fill = S("path", { d: d + ` L ${pts[pts.length - 1][0]} ${H} L ${pts[0][0]} ${H} Z` });
    fill.style.fill = "var(--spark)"; fill.style.opacity = ".12"; svg.appendChild(fill);
    const lp = S("path", { d, fill: "none", "stroke-width": 1.6, "stroke-linejoin": "round" });
    lp.style.stroke = "var(--spark)"; svg.appendChild(lp);
    const end = S("circle", { cx: pts[pts.length - 1][0], cy: pts[pts.length - 1][1], r: 2.4 });
    end.style.fill = "var(--accent)"; svg.appendChild(end);
    host.appendChild(svg);
  }

  return { S, el, sum, esc, boot, barList, heatmap, hourStrip, layers, stacked, line, grouped, gantt, emoChart, spark };
})();
