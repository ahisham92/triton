// The look shared by every tab inside a project: a coloured banner with the tab's icon, where it sits in
// the work (set up, design, check, cost and build) and Previous / Next tab; rows of coloured number
// tiles; a bar split by share; and the tide levels drawn against an underside. Presentation only.

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// The groups of tabs, in the order the work goes, with their colour (the same as the tab dots).
export const GROUPS = {
  setup: { label: "Set up", color: "#1e8fc0" },
  design: { label: "Design", color: "#d9730d" },
  check: { label: "Check", color: "#c2477d" },
  build: { label: "Cost and build", color: "#7b4bc4" },
  about: { label: "About", color: "#8a8a84" },
};

const P = (d) => `<path d="${d}"/>`;
// A small line drawing per tab, in the tab's colour.
const ICONS = {
  info: P("M5 20V9l7-5 7 5v11zM9 20v-6h6v6"),
  settings: P("M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"),
  sections: P("M3 18h18M5 18V9h5v9M12 18V6h7v12M5 12h5M12 10h7M12 14h7"),
  elements: P("M4 7h16v3H4zM6 10v10M12 10v10M18 10v10M3 20h18"),
  workbook: P("M5 3h10l4 4v14H5zM15 3v4h4M8 11h8M8 14h8M8 17h5"),
  design: P("M4 20h16M6 20V10M10 20V6M14 20v-8M18 20V4"),
  openings: P("M4 4h16v16H4zM8 8h4v4H8zM14 13h3v3h-3z"),
  view3d: P("M12 3 3 8v8l9 5 9-5V8zM3 8l9 5 9-5M12 13v8"),
  clashes: P("M4 4l7 7M4 20l7-7M20 4l-7 7M20 20l-7-7M12 12m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0"),
  furniture: P("M9 20v-9a3 3 0 0 1 6 0v9M6 20h12M8 11h8M10 7V4h4v3"),
  moved: P("M7 17a3 3 0 1 0 0-.01M17 7a3 3 0 1 0 0-.01M9.5 14.5l5-5M14.5 9.5h-3M14.5 9.5v3"),
  sequence: P("M3 20h18M5 20v-5h4v5M10 20v-9h4v9M15 20V6h4v14"),
  costing: P("M12 3v18M16.5 7.5c0-1.7-2-3-4.5-3s-4.5 1.3-4.5 3 2 2.6 4.5 3 4.5 1.3 4.5 3-2 3-4.5 3-4.5-1.3-4.5-3"),
  compare: P("M4 20V10h4v10M10 20V4h4v16M16 20v-7h4v7"),
  ve: P("M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z"),
  method: P("M4 5h7a3 3 0 0 1 3 3v12a2 2 0 0 0-2-2H4zM20 5h-5a3 3 0 0 0-3 3M20 5v13h-6"),
};
export function tabIcon(tab, size = 22) {
  return `<svg class="tab-icon" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[tab] || ICONS.info}</svg>`;
}

// What each tab is for, in one line, and its group.
export const TAB_INFO = {
  info: ["setup", "The project's name, prices and drawing names, and how the steps fit together."],
  settings: ["setup", "Codes, grades, covers and factors, shared by every section."],
  sections: ["setup", "Each stretch of quay with its own model, levels and loads."],
  elements: ["setup", "The piles, walls, slab and beams of this section, with their sizes."],
  workbook: ["setup", "The Plaxis straining actions for this section, checked and mapped to the elements."],
  design: ["design", "Bars and utilisation for every element, with the calculation reports."],
  openings: ["design", "Holes through the deck and the bars around them."],
  view3d: ["check", "The section in 3D: elements, results, site and deformed shape."],
  clashes: ["check", "Pile bars against the bars of the slab or beam over them, with the ways out."],
  furniture: ["check", "Fenders, bollards, rails and ties laid out along the berth and fixed into the beam."],
  moved: ["check", "Piles built away from where they were drawn, and what that does to the design."],
  sequence: ["build", "How the section is built, stage by stage, with the plant and the existing structure."],
  costing: ["build", "What the design costs: the totals, the cost per metre and where the money goes."],
  compare: ["build", "Several sizes and options designed side by side, with their cost."],
  ve: ["build", "Where the design can be made lighter or cheaper, element by element."],
  method: ["about", "How Triton designs each element, step by step."],
};

// The banner at the top of a tab. tabs: the [key, title] list in order; returns HTML.
export function tabBanner(tab, tabs) {
  const [group, line] = TAB_INFO[tab] || ["about", ""];
  const g = GROUPS[group];
  const i = tabs.findIndex(([k]) => k === tab);
  const prev = tabs[i - 1], next = tabs[i + 1];
  const plain = (t) => String(t).replace(/\s*\(\d+\)$/, "");
  return `<div class="tab-banner" style="--tabc:${g.color}">
    <span class="tb-icon">${tabIcon(tab, 26)}</span>
    <div class="tb-text"><span class="tb-group">${esc(g.label)}</span><b>${esc(plain(tabs[i]?.[1] ?? tab))}</b><span class="tb-line">${esc(line)}</span></div>
    <span class="tb-nav" data-free>${prev ? `<button type="button" class="quiet" data-tab-go="${prev[0]}" title="${esc(plain(prev[1]))}">&#9664; <span>${esc(plain(prev[1]))}</span></button>` : ""}
      ${next ? `<button type="button" class="quiet" data-tab-go="${next[0]}" title="${esc(plain(next[1]))}"><span>${esc(plain(next[1]))}</span> &#9654;</button>` : ""}</span></div>`;
}
export const tabGroupColor = (tab) => GROUPS[(TAB_INFO[tab] || ["about"])[0]].color;

// A row of number tiles. tiles: [{ label, value, sub, tone: "ok"|"warn"|"bad"|"", color }].
export function statTiles(tiles) {
  return `<div class="stat-tiles">${tiles.filter(Boolean).map((t) =>
    `<div class="stat ${t.tone || ""}"${t.color ? ` style="--sc:${t.color}"` : ""}${t.title ? ` title="${esc(t.title)}"` : ""}>
      <span class="stat-label">${esc(t.label)}</span><b class="stat-value">${t.value}</b>${t.sub ? `<span class="stat-sub">${t.sub}</span>` : ""}</div>`).join("")}</div>`;
}

// A bar split by share, with a legend. parts: [{ label, value, color }]; label for the values.
export function shareBar(parts, { fmt = (v) => String(v), unit = "" } = {}) {
  const got = parts.filter((p) => p.value > 0);
  const total = got.reduce((a, p) => a + p.value, 0);
  if (!total) return "";
  return `<div class="share"><div class="share-bar">${got.map((p) =>
    `<i style="flex:${p.value};background:${p.color}" title="${esc(p.label)}: ${esc(fmt(p.value))}${unit ? ` ${esc(unit)}` : ""} (${Math.round((100 * p.value) / total)}%)"></i>`).join("")}</div>
    <div class="share-legend">${got.map((p) =>
      `<span><i style="background:${p.color}"></i>${esc(p.label)} <b>${Math.round((100 * p.value) / total)}%</b></span>`).join("")}</div></div>`;
}

// A small meter, e.g. 6 of 7 heads pass. tone from the share: all green, most amber, else red.
export function passMeter(n, of) {
  if (!of) return "";
  const f = n / of;
  const tone = f >= 1 ? "safe" : f >= 0.5 ? "limit" : "unsafe";
  return `<span class="meter ${tone} pass-meter" title="${n} of ${of}"><i style="width:${Math.max(4, f * 100).toFixed(0)}%"></i></span>`;
}

// The tide levels against one or more undersides, drawn to scale.
// levels: [{ name, level_m }]; marks: [{ label, level, tone }] with tone ok|warn|bad.
export function tideDiagram(levels, marks, { fmt = (v) => String(v) } = {}) {
  if (!levels?.length) return "";
  const all = [...levels.map((l) => l.level_m), ...marks.map((m) => m.level)];
  const top = Math.max(...all) + 0.3, bot = Math.min(...all) - 0.3;
  const H = 230, W = 300, y = (v) => 12 + ((top - v) / (top - bot || 1)) * (H - 24);
  const hi = Math.max(...levels.map((l) => l.level_m)), lo = Math.min(...levels.map((l) => l.level_m));
  const TONE = { ok: "#2e8b57", warn: "#e0a100", bad: "#c62828" };
  // Labels at least `gap` apart, top down, so close levels (MLWS, LAT) stay readable.
  const spread = (ys, gap) => { const out = []; ys.forEach((v, i) => out.push(i && v < out[i - 1] + gap ? out[i - 1] + gap : v)); return out; };
  levels = [...levels].sort((a, b) => b.level_m - a.level_m);
  marks = [...marks].sort((a, b) => b.level - a.level);
  const ly = spread(levels.map((l) => y(l.level_m) + 3), 10);
  const my = spread(marks.map((m) => y(m.level)), 28);
  return `<svg class="tide-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Water levels and undersides">
    <defs><linearGradient id="tide-sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9fd3ea"/><stop offset="1" stop-color="#3f93c4"/></linearGradient></defs>
    <rect x="40" y="${y(hi)}" width="130" height="${H - y(hi) - 4}" fill="url(#tide-sea)" opacity=".85"/>
    <rect x="40" y="${y(hi)}" width="130" height="${y(lo) - y(hi)}" fill="#ffffff" opacity=".28"/>
    ${levels.map((l, i) => `<line x1="36" x2="174" y1="${y(l.level_m)}" y2="${y(l.level_m)}" stroke="#1f5f8b" stroke-width=".8" stroke-dasharray="${i ? "3 2" : ""}"/>
      <text x="32" y="${ly[i]}" text-anchor="end" class="tick">${esc(l.name)}</text>`).join("")}
    ${marks.map((m, i) => `<rect x="120" y="${y(m.level) - 16}" width="50" height="16" rx="2" fill="#b8b4aa" stroke="#8c877c"/>
      <line x1="120" x2="${W - 6}" y1="${y(m.level)}" y2="${y(m.level)}" stroke="${TONE[m.tone] || TONE.ok}" stroke-width="2"/>
      <text x="178" y="${my[i] - 4}" class="tide-label" fill="${TONE[m.tone] || TONE.ok}">${esc(m.label)}</text>
      <text x="178" y="${my[i] + 11}" class="tick">${m.level >= 0 ? "+" : ""}${fmt(m.level, 2)} m</text>`).join("")}
  </svg>`;
}
