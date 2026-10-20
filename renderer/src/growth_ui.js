// The 🧠 window's Growth tab: her growth as charts (brain/metrics.py's `growth` message), weekly
// or daily. Hand-drawn SVG -- three small charts don't need a library. Hover a bar or dot for its
// number (an SVG <title>). Memories are model-written: textContent only.

import { $ } from "./ui.js";

// Categorical slots, fixed order, checked for colorblind separation and contrast against the
// modal's #1e2130. A memory type keeps its color whatever else is on the chart.
const COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"];
const TYPES = ["fact", "decision", "lesson", "opinion"]; // a new memory, then her own (learning.distill_chat)
const W = 520;
const H = 170;
const PAD = { left: 34, right: 70, top: 10, bottom: 22 };
const NS = "http://www.w3.org/2000/svg";

const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...kids);
  return node;
};
const tip = (text) => el("title", {}, text);
const typeRank = (type) => (TYPES.includes(type) ? TYPES.indexOf(type) : TYPES.length);
const typeColor = (type) => COLORS[Math.min(typeRank(type), COLORS.length - 1)];
const label = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString([], { month: "short", day: "numeric" });


export function renderGrowth(data, period) {
  const series = data[period];
  if (!series) return;
  const when = period === "week" ? "week of" : "";
  const names = series.buckets.map((b) => `${when} ${label(b)}`.trim());

  const types = Object.keys(series.approved).sort((a, b) => typeRank(a) - typeRank(b) || a.localeCompare(b));
  $("growth-approved").replaceChildren(
    stackedBars(series.buckets, period, names, types.map((t) => ({ name: t, values: series.approved[t], color: typeColor(t) }))),
  );
  $("growth-approved-legend").replaceChildren(...legend(types.map((t) => ({ name: t, color: typeColor(t) }))));

  const lines = [
    { name: "recalled", values: series.recalled, color: COLORS[0] },
    { name: "in her bank", values: series.bank, color: COLORS[1] },
  ];
  $("growth-recalled").replaceChildren(lineChart(series.buckets, period, names, lines));
  $("growth-recalled-legend").replaceChildren(...legend(lines));

  const waiting = [
    { name: "memories", values: series.waiting.memories, color: COLORS[2] },
    { name: "lessons", values: series.waiting.lessons, color: COLORS[3] },
  ];
  $("growth-waiting").replaceChildren(lineChart(series.buckets, period, names, waiting));
  $("growth-waiting-legend").replaceChildren(...legend(waiting));

  $("growth-activities").replaceChildren(hbars(series.activities));
  $("growth-top").replaceChildren(
    ...(data.top.length
      ? data.top.map(([memory, n]) => Object.assign(document.createElement("li"), { textContent: `${memory} -- ${n}×` }))
      : [Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "Nothing recalled yet -- this starts with her next reply." })]),
  );
}

function frame(buckets, period, max) {
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i) => PAD.left + (innerW * (i + 0.5)) / buckets.length;
  const y = (v) => PAD.top + innerH - (innerH * v) / (max || 1);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "growth-svg", role: "img" });
  for (const v of [0, max / 2, max]) {
    const yy = y(v);
    svg.append(el("line", { x1: PAD.left, x2: W - PAD.right, y1: yy, y2: yy, class: "growth-grid" }));
    svg.append(el("text", { x: PAD.left - 6, y: yy + 4, class: "growth-axis", "text-anchor": "end" }, String(Math.round(v))));
  }
  const every = Math.ceil(buckets.length / 6); // at most ~6 date labels, so they never collide
  buckets.forEach((b, i) => {
    if ((buckets.length - 1 - i) % every === 0) svg.append(el("text", { x: x(i), y: H - 6, class: "growth-axis", "text-anchor": "middle" }, label(b)));
  });
  return { svg, x, y, slot: innerW / buckets.length };
}

function stackedBars(buckets, period, names, series) {
  const totals = buckets.map((_, i) => series.reduce((sum, s) => sum + s.values[i], 0));
  const { svg, x, y, slot } = frame(buckets, period, Math.max(1, ...totals));
  const width = Math.max(3, Math.min(28, slot * 0.6));
  buckets.forEach((_, i) => {
    let base = 0;
    for (const s of series) {
      const v = s.values[i];
      if (!v) continue;
      const top = y(base + v);
      svg.append(
        el("rect", { x: x(i) - width / 2, y: top, width, height: Math.max(1, y(base) - top - 2), rx: 2, fill: s.color }, tip(`${names[i]}: ${v} ${s.name}`)),
      );
      base += v;
    }
  });
  return svg;
}

function lineChart(buckets, period, names, series) {
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  const { svg, x, y } = frame(buckets, period, Math.max(1, ...all));
  for (const s of series) {
    const points = s.values.map((v, i) => (v == null ? null : [x(i), y(v), v, i])).filter(Boolean);
    if (!points.length) continue;
    svg.append(el("path", { d: points.map(([px, py], k) => `${k ? "L" : "M"}${px},${py}`).join(""), stroke: s.color, class: "growth-line" }));
    for (const [px, py, v, i] of points) {
      svg.append(el("circle", { cx: px, cy: py, r: 4, fill: s.color, class: "growth-dot" }, tip(`${names[i]}: ${v} ${s.name}`)));
    }
    const [lx, ly] = points[points.length - 1];
    svg.append(el("text", { x: lx + 8, y: ly + 4, class: "growth-label" }, s.name));
  }
  return svg;
}

function hbars(rows) {
  if (!rows.length) return Object.assign(document.createElement("p"), { className: "settings-hint", textContent: "Nothing yet." });
  const max = Math.max(...rows.map(([, n]) => n));
  const rowH = 22;
  const labelW = 120; // "visit friends" at a phone's larger chart text
  const svg = el("svg", { viewBox: `0 0 ${W} ${rows.length * rowH + 4}`, class: "growth-svg", role: "img" });
  rows.forEach(([what, n], i) => {
    const yy = i * rowH + 4;
    const width = Math.max(2, ((W - labelW - 50) * n) / max);
    svg.append(el("text", { x: labelW - 8, y: yy + 13, class: "growth-axis", "text-anchor": "end" }, what));
    svg.append(el("rect", { x: labelW, y: yy + 2, width, height: rowH - 8, rx: 2, fill: COLORS[0] }, tip(`${what}: ${n}`)));
    svg.append(el("text", { x: labelW + width + 6, y: yy + 13, class: "growth-label" }, String(n)));
  });
  return svg;
}

function legend(items) {
  return items.map(({ name, color }) => {
    const item = Object.assign(document.createElement("span"), { className: "growth-legend-item" });
    item.append(Object.assign(document.createElement("i"), { style: `background:${color}` }), name);
    return item;
  });
}
