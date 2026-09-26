// Quay furniture: fenders, bollards, ladders, storm pins, crane rails and stoppers, tie rods. The
// items are the project's (the same on every berth); the section only says where its berth differs.
// Each item's fixing is designed (EN 1992-4 anchors, bearing, EN 1993-5 tie rods) and every item is
// laid out along this section's berth, clear of joints, piles, rails and each other.
// app.js passes its helpers in: api, again, esc, fmt, secUrl, forms() -> [project form, section form], save().

const COL = { fenders: "#2a5fae", bollards: "#c87814", ladders: "#2e8b4a", storm_pins: "#7a4696", crane_stoppers: "#c62828", tie_downs: "#1f8a9a" };
const NAMES = { fenders: "Fenders", bollards: "Bollards", ladders: "Ladders", storm_pins: "Storm pins", crane_stoppers: "Crane stoppers", tie_downs: "Crane tie-downs", crane_rails: "Crane rails", tie_rods: "Tie rods", fender_blocks: "Fender protrusions" };

export async function renderFurniture(host, h) {
  const { api, again, esc, fmt, secUrl } = h;
  host.innerHTML = `<p class="sub">Fenders, bollards, ladders, storm pins, crane rails and stoppers, and tie rods. The items and their loads are the
      project's, the same on every section's berth; below them is only what differs on this section's berth. Each item's fixing into the beam is
      designed, and the items are laid out along the berth clear of the joints, the pile heads, the rails and each other.</p>
    <details class="panel" id="fu-inputs" data-free><summary>Items, loads and arrangement rules (every section)</summary><div id="fu-project"></div></details>
    <details class="panel" id="fu-sec" data-free style="margin-top:10px"><summary>This section's berth</summary><div id="fu-section"></div></details>
    <div class="panel row" style="margin-top:10px"><button id="fu-run">Arrange and design</button>
      <span class="status" id="fu-status">Values filled in are common ones, not from your catalogues: replace them.</span></div>
    <div id="fu-out"></div>`;
  const [projectForm, sectionForm] = h.forms();
  host.querySelector("#fu-project").append(projectForm);
  host.querySelector("#fu-section").append(sectionForm);
  const out = host.querySelector("#fu-out");
  const status = host.querySelector("#fu-status");
  const f2 = (v) => (v == null ? "–" : fmt(v, 2));
  const util = (u, ok) => `<span class="${ok ? "flag-ok" : "flag-bad"}">${u == null ? "–" : fmt(u, 3)}</span>`;

  const plan = (r) => {
    const lay = r.layout;
    const L = r.berth_length_m;
    const fr = r.frame;
    const k = 10; // px per m
    const out0 = r.protrusion ? r.protrusion.projection / 1000 : 0; // the fender blocks stand out from the face
    const deep = Math.max(fr.rear_beam ? fr.rear_beam.centre_m + fr.rear_beam.width_mm / 2000 : 0, fr.front_beam.width_mm / 1000) + 1;
    const W = L * k + 40;
    const ky = k;
    const H = (deep + out0) * ky + 50;
    const X = (s) => 20 + s * k;
    const Y = (d) => 20 + (d + out0) * ky;
    const parts = [];
    parts.push(`<rect x="${X(0)}" y="${Y(0)}" width="${L * k}" height="${(fr.front_beam.width_mm / 1000) * ky}" fill="var(--panel2, #eeeee8)" stroke="#9aa0aa"/>`);
    if (fr.rear_beam) {
      const rb = fr.rear_beam;
      parts.push(`<rect x="${X(0)}" y="${Y(rb.centre_m - rb.width_mm / 2000)}" width="${L * k}" height="${(rb.width_mm / 1000) * ky}" fill="var(--panel2, #eeeee8)" stroke="#9aa0aa"/>`);
    }
    parts.push(`<line x1="${X(0)}" y1="${Y(0)}" x2="${X(L)}" y2="${Y(0)}" stroke="currentColor" stroke-width="2"/>`);
    if (r.protrusion)
      for (const it of lay.items.fenders || []) {
        const half = r.protrusion.length / 2000;
        parts.push(`<rect x="${X(it.s_m - half)}" y="${Y(-out0)}" width="${2 * half * k}" height="${out0 * ky}" fill="var(--panel2, #eeeee8)" stroke="#9aa0aa"><title>Fender protrusion at ${f2(it.s_m)} m</title></rect>`);
      }
    for (const p of lay.pile_heads) parts.push(`<ellipse cx="${X(p.s)}" cy="${Y(p.d)}" rx="${p.r * k}" ry="${p.r * ky}" fill="none" stroke="#b8bcc4"><title>${esc(p.element)} at ${f2(p.s)} m</title></ellipse>`);
    for (const rl of lay.rails) parts.push(`<line x1="${X(0)}" y1="${Y(rl.across_m)}" x2="${X(L)}" y2="${Y(rl.across_m)}" stroke="#5a5a5a" stroke-width="3"><title>${esc(rl.tag)}</title></line>`);
    for (const j of lay.joints_m) parts.push(`<line x1="${X(j)}" y1="${Y(-1)}" x2="${X(j)}" y2="${Y(deep - 1)}" stroke="#c62828" stroke-dasharray="4 3"><title>Expansion joint at ${f2(j)} m</title></line>`);
    for (const [kind, items] of Object.entries(lay.items))
      for (const it of items) {
        let [a0, a1] = it.across_m;
        if (it.plane === "face") [a0, a1] = [-1.2, -0.2];
        const bad = it.status === "clash";
        parts.push(`<rect x="${X(it.from_m)}" y="${Y(a0)}" width="${Math.max((it.to_m - it.from_m) * k, 2)}" height="${Math.max((a1 - a0) * ky, 2)}"
          fill="${COL[kind]}" fill-opacity="0.25" stroke="${bad ? "#c62828" : COL[kind]}" stroke-width="${bad ? 2.5 : 1.5}">
          <title>${esc(it.label)}${it.tag ? ` (${esc(it.tag)})` : ""} at ${f2(it.s_m)} m${it.moved_m ? `, moved ${f2(it.moved_m)} m` : ""}${it.clashes.length ? `\nClashes with ${esc(it.clashes.join("; "))}` : ""}</title></rect>`);
      }
    for (let m = 0; m <= L; m += 10) parts.push(`<text x="${X(m)}" y="${H - 8}" font-size="10" fill="#7a808c" text-anchor="middle">${m}</text>`);
    const legend = Object.entries(COL).map(([kk, c]) => `<span style="white-space:nowrap"><span style="display:inline-block;width:10px;height:10px;background:${c};opacity:.6;margin-right:4px"></span>${NAMES[kk]}</span>`).join(" ");
    return `<div class="scroll" style="border:1px solid var(--line);border-radius:8px"><svg width="${W}" height="${H}" style="display:block;color:var(--text)">${parts.join("")}</svg></div>
      <p class="status">${legend} · grey circles: pile heads · dark lines: crane rails · red dashes: expansion joints (${esc(lay.joints_from)}). Fenders and ladders are on the face, drawn just off it. Hover an item for its details.</p>`;
  };

  const anchorsHtml = (a) => {
    if (!a) return "";
    const re = a.reinforcement || {};
    const extra = [re.tension?.text, re.shear?.text, re.splitting_mm2 ? `splitting bars ${fmt(re.splitting_mm2)} mm²` : ""].filter(Boolean);
    return `<div class="scroll"><table class="cost"><tr><th>Anchor check</th><th>Clause</th><th class="num">Ed (kN)</th><th class="num">Rd (kN)</th><th class="num">Utilisation</th></tr>
      ${a.checks.map((c) => `<tr><td>${esc(c.check)}</td><td class="hint">${esc(c.clause)}</td><td class="num">${c.Ed_kN ?? "–"}</td><td class="num">${c.Rd_kN ?? "–"}</td><td class="num">${util(c.utilisation, c.passed)}</td></tr>`).join("")}
      </table></div><p class="status">${a.bolts} × ${esc(a.bolt)}, hef ${a.hef_mm} mm; most loaded bolt ${a.N_max_kN} kN in tension, ${a.V_bolt_kN} kN in shear.
      ${extra.length ? `Local bars: ${esc(extra.join("; "))}.` : ""}</p>`;
  };

  // The fender protrusion's and the STS crane's own figures: small key-value blocks and tables.
  const BLOCKS = { section: "Section at the joint", joint: "Joint to the beam (6.2.5)", downstand: "Downstand below the beam", beam: "Into the front beam (extra to its own design)", bars: "Bars", quantities: "Quantities per block", standoff: "Stand-off from the quay face", reach: "Crane outreach", legs: "Flare and the crane's legs" };
  const cell = (v) => (typeof v === "number" ? fmt(v, Math.abs(v) < 10 ? 3 : 1) : esc(String(v)));
  const detailsHtml = (i) => {
    const kv = Object.entries(BLOCKS)
      .filter(([k]) => i[k] && typeof i[k] === "object")
      .map(([k, t]) => `<tr><th colspan="2" style="text-align:left">${esc(t)}</th></tr>${Object.entries(i[k])
        .filter(([, v]) => v !== null && v !== "")
        .map(([kk, v]) => `<tr><td class="hint">${esc(kk.replace(/_/g, " "))}</td><td>${Array.isArray(v) ? v.map(cell).join(" × ") : typeof v === "boolean" ? (v ? "yes" : "no") : cell(v)}</td></tr>`)
        .join("")}`)
      .join("");
    const tables = (i.details || [])
      .map((t) => `<p><strong>${esc(t.title)}</strong></p><div class="scroll"><table class="cost"><tr>${t.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>
        ${t.rows.map((row) => `<tr>${row.map((v) => `<td${typeof v === "number" ? ' class="num"' : ""}>${cell(v)}</td>`).join("")}</tr>`).join("")}</table></div>`)
      .join("");
    return (kv ? `<div class="scroll"><table class="cost">${kv}</table></div>` : "") + tables;
  };

  const draw = (r) => {
    const lay = r.layout;
    const counts = Object.entries(lay.counts).map(([k, n]) => `<td><strong>${n}</strong><div class="hint">${NAMES[k] || k}</div></td>`).join("");
    const itemsRows = r.items
      .map((i) => `<details class="panel" style="margin-top:8px"><summary><strong>${esc(i.title)}</strong> · utilisation ${util(i.utilisation, i.passed)}${i.governing_case ? ` <span class="status">(${esc(i.governing_case)})</span>` : ""}</summary>
        <table class="cost">${i.parts.map((p) => `<tr><td>${esc(p.part)}</td><td class="num">${util(p.utilisation, p.passed)}</td></tr>`).join("")}</table>
        ${detailsHtml(i)}
        ${anchorsHtml(i.anchors)}
        ${(i.notes || []).map((n) => `<p class="flag-bad">${esc(n)}</p>`).join("")}</details>`)
      .join("");
    const all = Object.values(lay.items).flat();
    const listed = all.filter((it) => it.status !== "ok");
    const arrangement = `<div class="scroll"><table class="cost"><tr><th>Item</th><th>Where</th><th class="num">At (m)</th><th class="num">Moved (m)</th><th>Clashes with</th></tr>
      ${(listed.length ? listed : all).map((it) => `<tr><td>${esc(it.label)}</td><td>${esc(it.tag || it.plane)}</td><td class="num">${f2(it.s_m)}</td><td class="num">${it.moved_m ? f2(it.moved_m) : ""}</td>
        <td class="${it.clashes.length ? "flag-bad" : ""}">${esc(it.clashes.join("; "))}</td></tr>`).join("")}</table></div>
      <p class="status">${listed.length ? `Only the items moved from their spacing or still clashing are listed (${all.length - listed.length} others sit at their spacing).` : "Every item sits at its spacing."}</p>`;
    const dl = (x, t) => `<a href="${secUrl()}/furniture/${x}">${t}</a>`;
    out.innerHTML = `<div class="panel" style="margin-top:10px"><h2 style="margin-top:0">${esc(r.section)}: ${fmt(r.berth_length_m, 1)} m of berth</h2>
        <p class="status">Berth length from ${esc(r.berth_length_from)}; cope at ${f2(r.cope_m)} m.
          ${r.unsafe.length ? `<span class="flag-bad">Not safe: ${esc(r.unsafe.join(", "))}.</span>` : '<span class="flag-ok">Every item passes.</span>'}
          ${lay.clashes ? `<span class="flag-bad">${lay.clashes} item(s) still clash.</span>` : ""}</p>
        <div class="scroll"><table class="cost"><tr>${counts}</tr></table></div>
        <p>${dl("calc.docx", "Calculation (Word)")} · ${dl("calc.pdf", "PDF")} · ${dl("calc.xlsx", "Excel")} · ${dl("plan.dxf", "Plan and bolts (AutoCAD)")} · ${dl("plan.crm", "for Revit (.crm)")}</p>
        <p class="status">Costing takes these numbers for its Fenders, Bollards, Ladders, Storm pins, Crane stoppers, Crane tie-downs and Tie rods rows where no number is given, and the section's drawings include this plan.</p></div>
      <div class="panel" style="margin-top:10px"><h2 style="margin-top:0">Arrangement</h2>${plan(r)}${arrangement}</div>
      <div style="margin-top:10px"><h2>Design</h2>${itemsRows}</div>
      <div class="panel" style="margin-top:10px"><h2 style="margin-top:0">Assumptions to confirm</h2><ul>${[...r.assumptions, ...r.notes].map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>`;
  };

  const run = async () => {
    const b = host.querySelector("#fu-run");
    b.disabled = true;
    status.textContent = "Arranging and designing…";
    try {
      await h.save();
      const r = await again(() => api(`${secUrl()}/furniture`));
      if (r.use === false) {
        out.innerHTML = '<p class="status">This section has no quay furniture (see This section\'s berth).</p>';
      } else draw(r);
      status.textContent = "";
    } catch (e) {
      status.textContent = e.message;
    }
    b.disabled = false;
  };
  host.querySelector("#fu-run").onclick = run;
  run();
}
