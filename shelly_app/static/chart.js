"use strict";
/* Graphiques de consommation : aire (puissance sur 24 h) et barres (énergie par jour). */

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const M = { top: 12, right: 8, bottom: 24, left: 58 };
  const H = 220;

  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (parent) parent.append(n);
    return n;
  };

  function niceMax(v) {
    if (!v || v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
    return 10 * p;
  }

  function frame(svg, maxY, fmtY) {
    svg.replaceChildren();
    const W = svg.clientWidth || 320;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const iw = W - M.left - M.right, ih = H - M.top - M.bottom;
    const y = (v) => M.top + ih - (v / maxY) * ih;
    for (let i = 0; i <= 4; i++) {
      const v = (maxY / 4) * i, yy = y(v);
      if (i > 0) el("line", { class: "grid", x1: M.left, x2: W - M.right, y1: yy, y2: yy }, svg);
      const t = el("text", { x: M.left - 6, y: yy + 4, "text-anchor": "end" }, svg);
      t.textContent = fmtY(v);
    }
    el("line", { class: "base", x1: M.left, x2: W - M.right, y1: y(0), y2: y(0) }, svg);
    return { W, iw, ih, y };
  }

  function tooltip(wrap, svg, x, yPx, html) {
    const tip = wrap.querySelector(".tooltip");
    const scale = svg.clientWidth / svg.viewBox.baseVal.width || 1;
    tip.innerHTML = html;
    tip.hidden = false;
    const w = tip.offsetWidth / 2;
    const left = Math.min(Math.max(x * scale, w), svg.clientWidth - w);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(yPx * scale - 8, tip.offsetHeight)}px`;
  }

  function hideTip(wrap) {
    wrap.querySelector(".tooltip").hidden = true;
  }

  /** points: [{ts, avg, max}] */
  function power(svg, points) {
    const wrap = svg.parentElement;
    const maxV = Math.max(0, ...points.map((p) => p.max ?? p.avg ?? 0));
    const maxY = niceMax(maxV * 1.1);
    const { W, iw, y } = frame(svg, maxY, (v) => `${Math.round(v)} W`);
    const n = points.length;
    const x = (i) => M.left + (n > 1 ? (i / (n - 1)) * iw : iw / 2);

    // Aire + ligne, interrompues là où il manque des mesures
    let line = "", area = "", run = [];
    const flush = () => {
      if (!run.length) return;
      line += run.map(([i, v], k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
      area += `M${x(run[0][0]).toFixed(1)},${y(0)}` +
        run.map(([i, v]) => `L${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("") +
        `L${x(run[run.length - 1][0]).toFixed(1)},${y(0)}Z`;
      run = [];
    };
    points.forEach((p, i) => (p.avg == null ? flush() : run.push([i, p.avg])));
    flush();
    el("path", { class: "area", d: area }, svg);
    el("path", { class: "line", d: line }, svg);

    // Heures sur l'axe X
    points.forEach((p, i) => {
      const d = new Date(p.ts * 1000);
      if (d.getMinutes() === 0 && d.getHours() % 6 === 0) {
        const t = el("text", { x: x(i), y: H - 6, "text-anchor": "middle" }, svg);
        t.textContent = `${String(d.getHours()).padStart(2, "0")}h`;
      }
    });

    // Réticule + info-bulle
    const cross = el("line", { class: "cross", y1: M.top, y2: y(0), visibility: "hidden" }, svg);
    const dot = el("circle", { class: "dot", r: 4.5, visibility: "hidden" }, svg);
    const hit = el("rect", { x: M.left, y: 0, width: iw, height: H, fill: "transparent" }, svg);
    const move = (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - M.left) / iw) * (n - 1))));
      const p = points[i];
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("visibility", "visible");
      const d = new Date(p.ts * 1000);
      const hh = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      if (p.avg == null) {
        dot.setAttribute("visibility", "hidden");
        tooltip(wrap, svg, x(i), M.top + 20, `${hh} · pas de mesure`);
        return;
      }
      dot.setAttribute("cx", x(i));
      dot.setAttribute("cy", y(p.avg));
      dot.setAttribute("visibility", "visible");
      tooltip(wrap, svg, x(i), y(p.avg), `${hh} · <b>${Math.round(p.avg)} W</b> (pic ${Math.round(p.max)} W)`);
    };
    const leave = () => {
      cross.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      hideTip(wrap);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", leave);
  }

  /** days: [{date: "YYYY-MM-DD", wh}] */
  function daily(svg, days, fmtEnergy) {
    const wrap = svg.parentElement;
    const maxV = Math.max(0, ...days.map((d) => d.wh ?? 0));
    const kwh = maxV >= 1000;
    const maxY = niceMax((kwh ? maxV / 1000 : maxV) * 1.1) * (kwh ? 1000 : 1);
    const { W, iw, y } = frame(svg, maxY, (v) => (kwh ? `${(v / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} kWh` : `${Math.round(v)} Wh`));
    const n = days.length;
    const slot = iw / n;
    const gap = Math.max(2, slot * 0.25);
    const bw = slot - gap;
    const r = Math.min(4, bw / 2);
    const todayIso = new Date().toLocaleDateString("sv");
    const labelEvery = n > 10 ? 5 : 1;

    days.forEach((d, i) => {
      const x0 = M.left + i * slot + gap / 2;
      const date = new Date(`${d.date}T12:00:00`);
      if ((n - 1 - i) % labelEvery === 0) {
        const t = el("text", { x: x0 + bw / 2, y: H - 6, "text-anchor": "middle" }, svg);
        t.textContent = n > 10
          ? String(date.getDate())
          : date.toLocaleDateString("fr-FR", { weekday: "short" }).replace(".", "");
      }
      const label = date.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
      const hitRect = el("rect", { x: M.left + i * slot, y: M.top, width: slot, height: y(0) - M.top, fill: "transparent" }, svg);
      if (d.wh == null || d.wh <= 0) {
        hitRect.addEventListener("pointerenter", () =>
          tooltip(wrap, svg, x0 + bw / 2, y(0) - 10, `${label} · ${d.wh == null ? "pas de mesure" : "0 Wh"}`));
      } else {
        const top = Math.min(y(d.wh), y(0) - 1);
        const rr = Math.min(r, y(0) - top);
        const path = `M${x0},${y(0)}V${top + rr}Q${x0},${top} ${x0 + rr},${top}H${x0 + bw - rr}Q${x0 + bw},${top} ${x0 + bw},${top + rr}V${y(0)}Z`;
        const bar = el("path", { class: `bar${d.date === todayIso ? " dim" : ""}`, d: path }, svg);
        const show = () => tooltip(wrap, svg, x0 + bw / 2, top,
          `${label}${d.date === todayIso ? " (en cours)" : ""} · <b>${fmtEnergy(d.wh)}</b>` +
          (d.eur != null && typeof fmtEur === "function" ? ` · ${fmtEur(d.eur)}` : ""));
        hitRect.addEventListener("pointerenter", show);
        hitRect.addEventListener("pointerdown", show);
        bar.style.pointerEvents = "none";
      }
      hitRect.addEventListener("pointerleave", () => hideTip(wrap));
    });
  }

  return { power, daily };
})();
