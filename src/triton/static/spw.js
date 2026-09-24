// Sheet pile wall results: EN 1993-5 checks as ArcelorMittal Durability 4.2.1 does them, on every
// Plaxis result of the wall. app.js passes its helpers in (fmt, esc, frame, showTip, v3dSlot) and
// onIgnore(element, rules), which saves the element's "Ignore N or Q" rules and designs it again.

const ALL = "All combinations";
const CHECKS = ["bending", "bending_shear", "web_buckling", "buckling", "bending_axial", "bending_shear_axial"];

export function spwCard(w, h) {
  const { fmt, esc } = h;
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const d = w.design;
  if (!d || d.error) {
    card.innerHTML = `<div class="element-head"><h3>${esc(w.element)}<span class="type">sheet pile wall</span></h3></div>
      <p class="status">${esc(d?.error || "No results to design with.")}</p>`;
    return card;
  }
  const p = d.properties;
  const s = d.settings;
  const T = d.check_titles;
  const des = d.designed;
  const pla = d.as_plaxis;
  const g = des.governing;
  const gp = pla.governing;
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const ufCell = (u) => (u == null ? `<td class="muted">–</td>` : `<td class="cell ${u <= 1 ? "ok" : "error"}">${fmt(u, 2)}</td>`);
  const where = (r) => `${esc(r.combination)}, z ${fmt(r.z, 2)} m`;
  const ignoredText = d.ignored
    .map((r) => `${[r.ignore_n && "N", r.ignore_q && "Q"].filter(Boolean).join(" and ")} left out in ${r.combination === ALL ? "every combination" : esc(r.combination)}`)
    .join("; ");
  card.innerHTML = `<div class="element-head"><h3>${esc(w.element)}<span class="type">${esc(d.section)}, fy ${fmt(d.steel.fy)} MPa, EN 1993-5 (as Durability 4.2.1)</span></h3>
      ${ok(des.ok)}</div>
    <div class="counts" style="margin-top:0">
      <div class="count"><b class="${des.uf > 1 ? "bad" : ""}">${fmt(des.uf, 2)}</b>Uf${d.adjusted ? " as designed" : ""}: ${esc(T[g.governs])}, ${where(g)}</div>
      ${d.adjusted ? `<div class="count"><b class="${pla.uf > 1 ? "bad" : ""}">${fmt(pla.uf, 2)}</b>Uf with every Plaxis action: ${esc(T[gp.governs])}, ${where(gp)}</div>` : ""}
      <div class="count"><b>${fmt(p.mass, 1)}</b>kg/m² of wall (${fmt(d.top, 2)} to ${fmt(d.toe, 2)} m)</div>
      <div class="count"><b>${fmt(s.buckling_length, 2)} m</b>buckling length${s.buckling_length_given ? "" : " (assumed)"}</div>
    </div>
    ${d.adjusted ? `<p class="fail-why"><b>Adjusted:</b> ${ignoredText}. Both results are shown; the one with every Plaxis action is ${pla.ok ? "also safe" : `<b>unsafe</b> (Uf ${fmt(pla.uf, 2)})`}.</p>` : ""}
    ${!des.ok ? `<div class="fail-why"><b>Fails because:</b> ${esc(T[g.governs])} Uf ${fmt(g.uf, 2)} at ${where(g)} (M ${fmt(g.M, 1)} kNm/m, V ${fmt(g.V, 1)} kN/m, N ${fmt(g.N, 1)} kN/m, ${fmt(g.loss, 2)} mm corrosion). ${hint(g, d, T)}</div>` : ""}
    ${[...(w.notes || []), ...d.notes].map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${h.v3dSlot ? h.v3dSlot(w.element) : ""}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;align-items:start"><div class="chart" data-spw="profile"></div>
      <div class="scroll"><table>
        <tr><th colspan="2">Section ${esc(d.section)} (catalogue, per m)</th></tr>
        <tr><td>h, tf, tw</td><td>${fmt(p.h)}, ${fmt(p.tf, 1)}, ${fmt(p.tw, 1)} mm</td></tr>
        <tr><td>A, I</td><td>${fmt(p.area, 1)} cm²/m, ${fmt(p.inertia)} cm⁴/m</td></tr>
        <tr><td>Wel, Wpl</td><td>${fmt(p.wel)}, ${fmt(p.wpl)} cm³/m</td></tr>
        <tr><td>Flange b, web α</td><td>${fmt(p.flange)} mm${p.flange_given ? "" : " (est.)"}, ${fmt(p.angle, 1)}°${p.angle_given ? "" : " (est.)"}</td></tr>
        <tr><td>Web slant c, Av</td><td>${fmt(p.c)} mm, ${fmt(p.av, 1)} cm²/m</td></tr>
        <tr><td>Catalogue class</td><td>${p.catalogue_class}${s.class_from === "catalogue" ? " (floor)" : ""}</td></tr>
        <tr><td>γM0, γM1</td><td>${fmt(s.gamma_m0, 2)}, ${fmt(s.gamma_m1, 2)}</td></tr>
        <tr><td>Water head, ρP</td><td>${fmt(s.head, 1)} m${s.welded ? ", welded interlocks" : ""}</td></tr>
        <tr><td>Shear, e</td><td>${esc(s.shear)}, ${fmt(s.eccentricity_mm)} mm</td></tr>
        <tr><td>Points checked</td><td>${fmt(d.points)}${d.left_out_king_piles ? ` (inside ${d.left_out_king_piles} king piles left out)` : ""}</td></tr>
      </table></div></div>
    <h3 style="margin-top:18px">Results by corrosion zone</h3>
    <div class="row" style="gap:8px;margin:4px 0"><label class="chip"><input type="radio" name="spw-view-${esc(w.element)}" value="designed" checked> ${d.adjusted ? "As designed" : "Results"}</label>
      ${d.adjusted ? `<label class="chip"><input type="radio" name="spw-view-${esc(w.element)}" value="as_plaxis"> With every Plaxis action</label>` : ""}</div>
    <div class="scroll" data-spw="zones"></div>
    <h3 style="margin-top:18px">Each check at its worst</h3>
    <div class="scroll" data-spw="checks"></div>
    <details style="margin-top:12px" data-spw="detail-box"><summary>Numerical details at the governing point</summary><div data-spw="detail"></div></details>
    <h3 style="margin-top:18px">By combination: leave out N or Q</h3>
    <p class="status">Where a Plaxis value is suspect (the plate smears the wall), leave N or Q out for that combination and design again. The check with every action stays on this card.</p>
    <div class="scroll" data-spw="combos"></div>
    <details style="margin-top:12px"><summary>Uf of every AZ section and grade (Durability's Uf summary)</summary><div class="scroll" data-spw="summary"></div></details>`;

  const zonesBox = card.querySelector('[data-spw="zones"]');
  const checksBox = card.querySelector('[data-spw="checks"]');
  const detail = card.querySelector('[data-spw="detail"]');
  const draw = (which) => {
    const r = d[which];
    zonesBox.innerHTML = `<table><tr><th>Zone</th><th>Down to (m)</th><th>Loss front + back (mm)</th><th>Worst at z (m)</th><th>Combination</th>
        <th>M kNm/m</th><th>V kN/m</th><th>N kN/m</th>${CHECKS.map((c) => `<th>${esc(T[c])}</th>`).join("")}<th>Uf</th><th>Class</th></tr>
      ${r.zones.map((z) => {
        const zz = d.zones[z.zone - 1] || {};
        return `<tr><td>${z.zone}</td><td>${fmt(zz.bottom, 2)}</td><td>${fmt(zz.front, 2)} + ${fmt(zz.back, 2)} = ${fmt(z.loss, 2)}</td><td>${fmt(z.z, 2)}</td><td>${esc(z.combination)}</td>
          <td>${fmt(z.M, 1)}</td><td>${fmt(z.V, 1)}</td><td>${fmt(z.N, 1)}</td>${CHECKS.map((c) => ufCell(z.checks[c])).join("")}${ufCell(z.uf)}<td>${z.values.class}${z.values.class === 4 ? "→3" : ""}</td></tr>`;
      }).join("")}</table>`;
    checksBox.innerHTML = `<table><tr><th>Check</th><th>Uf</th><th>z (m)</th><th>Combination</th><th>M</th><th>V</th><th>N</th><th>Loss mm</th><th>Resistance</th></tr>
      ${CHECKS.map((c) => {
        const x = r.checks[c];
        if (!x) return `<tr><td>${esc(T[c])}</td><td class="muted" colspan="8">not needed anywhere${c === "web_buckling" ? " (c / tw ≤ 72 ε)" : ""}</td></tr>`;
        return `<tr><td>${esc(T[c])}</td>${ufCell(x.checks[c])}<td>${fmt(x.z, 2)}</td><td>${esc(x.combination)}</td><td>${fmt(x.M, 1)}</td><td>${fmt(x.V, 1)}</td><td>${fmt(x.N, 1)}</td><td>${fmt(x.loss, 2)}</td><td>${resistance(c, x.values, fmt)}</td></tr>`;
      }).join("")}</table>`;
    detail.innerHTML = details(r.governing, d, fmt, esc);
    profile(card.querySelector('[data-spw="profile"]'), d, h);
  };
  draw("designed");
  card.querySelectorAll(`input[name="spw-view-${CSS.escape(w.element)}"]`).forEach((i) => (i.onchange = () => draw(i.value)));

  // Per combination, with ticks that change the element's "Ignore N or Q" rules.
  const combos = card.querySelector('[data-spw="combos"]');
  const all = d.ignored.find((r) => r.combination === ALL) || { ignore_n: false, ignore_q: false };
  combos.innerHTML = `<table><tr><th>Combination</th><th>max N kN/m</th><th>max |V| kN/m</th><th>max |M| kNm/m</th><th>Uf, every action</th><th>Uf as designed</th><th>Ignore N</th><th>Ignore Q</th></tr>
    <tr><td><b>${ALL}</b></td><td colspan="5" class="muted">applies to every combination</td>
      <td><input type="checkbox" data-c="${ALL}" data-k="ignore_n" ${all.ignore_n ? "checked" : ""}></td><td><input type="checkbox" data-c="${ALL}" data-k="ignore_q" ${all.ignore_q ? "checked" : ""}></td></tr>
    ${d.by_combination.map((c) => {
      const own = d.ignored.find((r) => r.combination === c.combination) || {};
      return `<tr><td>${esc(c.combination)}</td><td>${fmt(c.max_N, 1)}</td><td>${fmt(c.max_V, 1)}</td><td>${fmt(c.max_M, 1)}</td>${ufCell(c.uf_plaxis)}${ufCell(c.uf)}
        <td><input type="checkbox" data-c="${esc(c.combination)}" data-k="ignore_n" ${own.ignore_n ? "checked" : ""}></td>
        <td><input type="checkbox" data-c="${esc(c.combination)}" data-k="ignore_q" ${own.ignore_q ? "checked" : ""}></td></tr>`;
    }).join("")}</table>
    <p><button data-spw="apply" disabled>Design again with these</button> <span class="status" data-spw="apply-status"></span></p>`;
  const apply = combos.querySelector('[data-spw="apply"]');
  combos.querySelectorAll("input[type=checkbox]").forEach((c) => (c.onchange = () => (apply.disabled = false)));
  apply.onclick = async () => {
    const rules = {};
    combos.querySelectorAll("input[type=checkbox]").forEach((c) => {
      const r = (rules[c.dataset.c] ??= { combination: c.dataset.c, ignore_n: false, ignore_q: false });
      r[c.dataset.k] = c.checked;
    });
    apply.disabled = true;
    apply.textContent = "Saving…";
    const msg = await h.onIgnore?.(w.element, Object.values(rules).filter((r) => r.ignore_n || r.ignore_q));
    if (msg) {
      apply.textContent = "Design again with these";
      card.querySelector('[data-spw="apply-status"]').textContent = msg;
    }
  };

  // Durability's Uf summary: every AZ section and grade for the same actions and corrosion.
  const sum = d.uf_summary;
  if (sum) {
    card.querySelector('[data-spw="summary"]').innerHTML = `<p class="status">Actions as designed, the element's corrosion zones, buckling length and settings; flange width and web angle from Triton's estimate for each section. Sorted by mass.</p>
      <table><tr><th>Section</th><th>kg/m²</th>${sum.grades.map((gr) => `<th>${esc(gr)}</th>`).join("")}</tr>
      ${[...sum.rows].sort((a, b) => a.mass - b.mass).map((r) => `<tr${r.section === d.section ? ' class="hl"' : ""}><td>${r.section === d.section ? `<b>${esc(r.section)}</b>` : esc(r.section)}</td><td>${fmt(r.mass, 1)}</td>
        ${sum.grades.map((gr) => ufCell(r.uf[gr])).join("")}</tr>`).join("")}</table>`;
  }
  return card;
}

function hint(g, d, T) {
  if (g.governs === "buckling" || g.governs === "bending_axial") return "N governs: check the buckling length, or leave N out where the Plaxis value is suspect.";
  if (g.governs === "web_buckling" || g.governs === "bending_shear") return "Shear governs: check the wall's top level (the connection peak) or leave Q out where suspect; a thicker web helps.";
  return "Bending governs: a stronger section or grade is needed (see the Uf summary).";
}

function resistance(c, v, fmt) {
  switch (c) {
    case "bending": return `Mc,Rd ${fmt(v.Mc, 1)} kNm/m`;
    case "bending_shear": return `Vpl,Rd ${fmt(v.Vpl, 1)} kN/m, MV,Rd ${fmt(v.Mv, 1)}`;
    case "web_buckling": return `Vb,Rd ${fmt(v.Vb, 1)} kN/m (c/tw/ε ${fmt(v.c_tw_eps, 1)})`;
    case "buckling": return `Npl,Rd ${fmt(v.Npl)}, Ncr ${fmt(v.Ncr)}, χ ${fmt(v.chi, 3)}`;
    case "bending_axial": return `Npl,Rd ${fmt(v.Npl)} kN/m, MN,Rd ${fmt(v.Mn, 1)}`;
    default: return `fy (1 − ρ) on the shear area`;
  }
}

function details(r, d, fmt, esc) {
  const v = r.values;
  const s = d.settings;
  const n = r.N;
  const lines = [
    `Point: ${esc(r.combination)}, z ${fmt(r.z, 2)} m${r.node != null ? `, node ${r.node}` : ""}, corrosion zone ${r.zone}: ${fmt(r.loss, 2)} mm off every plate.`,
    `Reduced section: tf ${fmt(v.tf, 2)} mm, tw ${fmt(v.tw, 2)} mm, h ${fmt(v.h, 1)} mm, A ${fmt(v.area, 1)} cm²/m, I ${fmt(v.inertia)} cm⁴/m, Wel ${fmt(v.wel)} cm³/m, Wpl ${fmt(v.wpl)} cm³/m, Av ${fmt(v.av, 1)} cm²/m.`,
    `Class: (b / tf) / ε = ${fmt(v.slender, 1)} → class ${v.class}${v.class === 4 ? `, taken as class 3 with fy,red = 235 × 66² × tf² / b² = ${fmt(v.fy_used, 1)} MPa` : ""}${d.settings.class_from === "catalogue" ? ` (never better than the catalogue's ${d.properties.catalogue_class})` : ""}.`,
    `Bending: MEd = ${fmt(r.M, 1)} kNm/m ≤ Mc,Rd = W fy ${v.rho_p < 1 ? `ρP (${fmt(v.rho_p, 3)}) ` : ""}/ γM0 = ${fmt(v.W)} × ${fmt(v.fy_used, 1)} / ${fmt(s.gamma_m0, 2)} = ${fmt(v.Mc, 1)} kNm/m.`,
    `Shear: VEd = ${fmt(r.V, 1)} kN/m, Vpl,Rd = Av fy / (√3 γM0) = ${fmt(v.Vpl, 1)} kN/m; ${r.V > 0.5 * v.Vpl ? `over 0.5 Vpl,Rd, so MV,Rd = ${fmt(v.Mv, 1)} kNm/m` : `≤ 0.5 Vpl,Rd = ${fmt(0.5 * v.Vpl, 1)}, no reduction of M`}.`,
    v.Vb != null
      ? `Web shear buckling: c / (tw ε) = ${fmt(v.c_tw_eps, 1)} > 72, Vb,Rd = (h − tf) tw fbv / (γM0 bs) = ${fmt(v.Vb, 1)} kN/m.`
      : `Web shear buckling: c / (tw ε) = ${fmt(v.c_tw_eps, 1)} ≤ 72, not needed.`,
    n > 0
      ? `Buckling: NEd = ${fmt(n, 1)} kN/m, Npl,Rd = ${fmt(v.Npl)} kN/m, Ncr = E I π² / l² = ${fmt(v.Ncr)} kN/m (l ${fmt(s.buckling_length, 2)} m); NEd / Ncr = ${fmt(n / v.Ncr, 3)}${n / v.Ncr <= 0.04 ? " ≤ 0.04, no buckling check" : `, χ = ${fmt(v.chi, 3)} (curve d): NEd / (χ Npl,Rd) + 1.15 MEd / Mc,Rd ≤ γM0 / γM1`}.`
      : n < 0 ? `N is tension (${fmt(-n, 1)} kN/m): no buckling.` : `No axial force${d.adjusted ? " (left out)" : ""}.`,
    n !== 0
      ? `Bending and axial: |NEd| / Npl,Rd = ${fmt(Math.abs(n) / v.Npl, 3)}${Math.abs(n) / v.Npl <= 0.1 ? " ≤ 0.10, M not reduced" : `, MN,Rd = Mc,Rd (1 − NEd / Npl,Rd) = ${fmt(v.Mn, 1)} kNm/m`}.`
      : "",
    `Uf = ${fmt(r.uf, 3)} (${esc(d.check_titles[r.governs])}).`,
  ];
  return `<ul>${lines.filter(Boolean).map((l) => `<li>${l}</li>`).join("")}</ul>`;
}

function profile(el, d, h) {
  const { fmt, frame, showTip } = h;
  const a = d.designed.profile;
  const b = d.adjusted ? d.as_plaxis.profile : [];
  const zs = a.map((q) => q[0]);
  const maxU = Math.max(1.1, ...a.map((q) => q[1]), ...b.map((q) => q[1])) * 1.05;
  const c = frame(el, { xDomain: [0, maxU], yDomain: [Math.min(...zs), Math.max(...zs)], xLabel: "Uf", yLabel: "Level z (m)", title: "Uf down the wall" });
  const path = (rows) => rows.map((q, i) => `${i ? "L" : "M"}${c.x(q[1]).toFixed(1)},${c.y(q[0]).toFixed(1)}`).join("");
  const zones = d.zones.map((z) => z.bottom > Math.min(...zs) && z.bottom < Math.max(...zs)
    ? `<line class="grid" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(z.bottom)}" y2="${c.y(z.bottom)}" stroke-dasharray="4 3"/>
       <text class="tick" x="${c.w - c.m.r - 4}" y="${c.y(z.bottom) - 3}" text-anchor="end">${fmt(z.total, 2)} mm above</text>` : "").join("");
  c.g.innerHTML = `${zones}<line class="limit" x1="${c.x(1)}" x2="${c.x(1)}" y1="${c.m.t}" y2="${c.h - c.m.b}"/>
    <text class="label" x="${c.x(1) + 4}" y="${c.m.t + 12}">1.0</text>
    ${b.length ? `<path class="series low" d="${path(b)}"/>` : ""}<path class="series" d="${path(a)}"/>
    ${b.length ? `<text class="tick" x="${c.m.l + 6}" y="${c.m.t + 12}">dashed: every Plaxis action</text>` : ""}`;
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sy = ((evt.clientY - r.top) / r.height) * c.h;
    let best = 0;
    a.forEach((q, i) => { if (Math.abs(c.y(q[0]) - sy) < Math.abs(c.y(a[best][0]) - sy)) best = i; });
    const q = a[best];
    const other = b.find((x) => x[0] === q[0]);
    c.g.querySelector(".hover")?.remove();
    c.g.insertAdjacentHTML("beforeend", `<circle class="hover" cx="${c.x(q[1])}" cy="${c.y(q[0])}" r="4"/>`);
    showTip(c, evt, `z ${fmt(q[0], 1)} m<br>Uf ${fmt(q[1], 3)}${other ? `<br>every action ${fmt(other[1], 3)}` : ""}`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; c.g.querySelector(".hover")?.remove(); };
}
