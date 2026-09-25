// Comparisons, option matrix: a deck designed for every combination of the thicknesses, crack width
// limits, pile-face methods and deck types listed, with the whole section, then compared: a grid at a
// glance, a summary table, each option's bars and governing sections, and a report (Word, PDF, Excel).
// trials.js passes app.js's helpers in, with picker() / wirePicker() for the element choice and
// barDiagrams(card, design) to draw an option's bars as the design page does.

export async function renderMatrix(out, h) {
  const { api, again, esc, fmt, secUrl, ROOT } = h;
  let data, running = null, form = null, sortBy = "cost";
  const ticked = new Set(); // options left out of the report
  const open = []; // options whose details are shown

  const load = async () => {
    data = await api(`${secUrl()}/matrix`);
    form ??= fromSpec(data.spec);
    if (h.element && form.element !== h.element) form.element = h.element; // the deck picked above
  };
  const fromSpec = (s) => ({
    element: s?.element || data.slabs[0],
    thickness: (s?.thickness || []).join(", "),
    wk: (s?.crack_width_limit || []).join(", "),
    peaks: new Set(s?.peaks || []),
    solid: (s?.decks || []).some((d) => d.type === "solid"),
    voids: (s?.decks || []).filter((d) => d.type === "voided").map((d) => `${fmt(d.diameter)}@${fmt(d.spacing)}`).join(", "),
    mesh: new Set(s?.mesh || []),
    punching: new Set(s?.punching_per || []),
    use: { thickness: true, crack_width_limit: true, peaks: true, decks: true, mesh: false, punching_per: false, ...(s?.use || {}) },
  });
  const list = (t) => [...new Set(String(t || "").split(/[,;\s]+/).map(Number).filter((v) => Number.isFinite(v) && v > 0))];
  const voidList = (t) =>
    String(t || "")
      .split(/[,;]+/)
      .map((x) => x.trim().match(/^(\d+(?:\.\d+)?)\s*(?:@|at|\/)\s*(\d+(?:\.\d+)?)$/))
      .filter(Boolean)
      .map((m) => ({ type: "voided", diameter: +m[1], spacing: +m[2] }));
  const spec = () => ({
    element: form.element,
    thickness: list(form.thickness),
    crack_width_limit: list(form.wk),
    peaks: [...form.peaks],
    decks: [...(form.solid ? [{ type: "solid" }] : []), ...voidList(form.voids)],
    mesh: [...form.mesh].sort((a, b) => a - b),
    punching_per: [...form.punching],
    use: { ...form.use },
  });
  // A list with its tick box off is not compared: the deck keeps its own value for it.
  const AXES = ["thickness", "crack_width_limit", "peaks", "decks", "mesh", "punching_per"];
  const count = () => {
    const s = spec();
    return AXES.reduce((n, k) => n * (s.use[k] ? Math.max(1, s[k].length) : 1), 1);
  };

  const cur = () => data.currency || "";
  const money = (v) => (v == null ? "–" : fmt(v));
  const opt = (o) => o.option || {};
  const deckType = (o) => {
    const d = opt(o).deck;
    if (!d) return data.current?.voids ? `voids Ø${fmt(data.current.voids.diameter)} @ ${fmt(data.current.voids.spacing)}` : "solid";
    return d.type === "solid" ? "solid" : `voids Ø${fmt(d.diameter)} @ ${fmt(d.spacing)}`;
  };
  const wkOf = (o) => opt(o).crack_width_limit ?? data.current?.crack_width_limit;
  const peaksOf = (o) => data.peaks[opt(o).peaks || data.current?.peaks] || opt(o).peaks || "";
  const meshOf = (o) => (opt(o).mesh ? `mesh @ ${fmt(opt(o).mesh)}` : "");
  const punchOf = (o) => (opt(o).punching_per ? (data.punching || {})[opt(o).punching_per] : "");
  const acrossKey = (o) => `${wkOf(o)}|${peaksOf(o)}|${meshOf(o)}|${punchOf(o)}`;
  const safe = (o) => o.deck?.passed;

  const inputs = () => {
    const s = spec();
    const n = count();
    const use = (k) => `<input type="checkbox" data-use="${k}" ${form.use[k] ? "checked" : ""} title="Compare this: untick to leave it as the deck has it">`;
    const off = (k) => (form.use[k] ? "" : " mx-off");
    const cur_ = data.current || {};
    return `<div class="matrix-form">
      ${h.element ? "" : `<label>Deck <select data-f="element">${data.slabs.map((n) => `<option ${n === form.element ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>`}
      <div class="mx-axis${off("thickness")}"><label class="check">${use("thickness")} <b>Thicknesses (mm)</b></label><br><input data-f="thickness" value="${esc(form.thickness)}" placeholder="e.g. 650, 700, 750" style="width:12em" ${form.use.thickness ? "" : "disabled"}></div>
      <div class="mx-axis${off("crack_width_limit")}"><label class="check">${use("crack_width_limit")} <b>Crack width limits (mm)</b></label><br><input data-f="wk" value="${esc(form.wk)}" placeholder="e.g. 0.2, 0.3" style="width:8em" ${form.use.crack_width_limit ? "" : "disabled"}></div>
      <div class="mx-axis${off("peaks")}"><label class="check">${use("peaks")} <b>Moments at the pile faces</b></label><br>${Object.entries(data.peaks)
        .map(([k, t]) => `<label class="check"><input type="checkbox" data-peak="${k}" ${form.peaks.has(k) ? "checked" : ""} ${form.use.peaks ? "" : "disabled"}> ${esc(t)}</label>`)
        .join(" ")}</div>
      <div class="mx-axis${off("decks")}"><label class="check">${use("decks")} <b>Deck types</b></label><br><label class="check"><input type="checkbox" data-f="solid" ${form.solid ? "checked" : ""} ${form.use.decks ? "" : "disabled"}> Solid</label>
        <label>With voids, Ø @ spacing (mm) <input data-f="voids" value="${esc(form.voids)}" placeholder="e.g. 500@700, 400@600" style="width:12em" ${form.use.decks ? "" : "disabled"}></label></div>
      <div class="mx-axis${off("mesh")}"><label class="check">${use("mesh")} <b>Mesh spacing (mm)</b></label><br>${(data.spacings || [150, 200])
        .map((m) => `<label class="check"><input type="checkbox" data-mesh="${m}" ${form.mesh.has(m) ? "checked" : ""} ${form.use.mesh ? "" : "disabled"}> ${fmt(m)}</label>`)
        .join(" ")}</div>
      <div class="mx-axis${off("punching_per")}"><label class="check">${use("punching_per")} <b>Punching design</b></label><br>${Object.entries(data.punching || {})
        .map(([k, t]) => `<label class="check"><input type="checkbox" data-punch="${k}" ${form.punching.has(k) ? "checked" : ""} ${form.use.punching_per ? "" : "disabled"}> ${esc(t.replace("punching ", ""))}</label>`)
        .join(" ")}</div>
    </div>
    <p class="status">Untick a list to leave it out of the comparison: the deck keeps what it has (now ${fmt(cur_.thickness)} mm, wk ${fmt(cur_.crack_width_limit, 2)} mm, ${esc(data.peaks[cur_.peaks] || "")}${cur_.voids ? ", with voids" : ", solid"}, ${cur_.mesh ? `mesh @ ${fmt(cur_.mesh)}` : "the lighter mesh"}, ${esc((data.punching || {})[cur_.punching_per] || "")}).</p>
    <p class="status">${n} combination${n === 1 ? "" : "s"}${n > data.max ? `: <span class="flag-bad">at most ${data.max} at a time</span>` : ""}.
      An empty list keeps the deck as it is too.
      The crack width limit is the deck's own, on both faces; every combination is designed with the whole section and without the bars you set by hand.
      Combinations already designed from the same inputs are not designed again.${s.decks.some((d) => d.type === "voided") && !data.current?.voids ? " Voids run across the quay from 1 m behind the front beam to 1 m before the rear beam, as the Elements tab's defaults." : ""}</p>
    <div class="row"><button class="primary" id="mx-run" ${running || n > data.max ? "disabled" : ""}>Design every combination</button>
      ${running ? '<button id="mx-stop">Stop</button>' : ""}<span id="mx-status" class="status">${esc(running?.text || "")}</span></div>`;
  };

  // The grid: thickness and deck type down, crack width and method across; each cell the deck's cost
  // per metre, green where the deck is safe, with its bars in kg/m³.
  const grid = (rows) => {
    const down = [...new Map(rows.map((o) => [`${opt(o).thickness}|${deckType(o)}`, o])).values()].sort((a, b) => opt(a).thickness - opt(b).thickness || deckType(a).localeCompare(deckType(b)));
    const across = [...new Map(rows.map((o) => [acrossKey(o), o])).values()].sort((a, b) => acrossKey(a).localeCompare(acrossKey(b)));
    const cell = (r, c) => rows.find((o) => opt(o).thickness === opt(r).thickness && deckType(o) === deckType(r) && acrossKey(o) === acrossKey(c));
    return `<div class="scroll"><table class="matrix-grid"><tr><th>Thickness, type</th>${across.map((c) => `<th>wk ${fmt(wkOf(c), 2)} mm<br><span class="hint">${esc([peaksOf(c), meshOf(c), punchOf(c)].filter(Boolean).join(", "))}</span></th>`).join("")}</tr>
      ${down.map((r) => `<tr><th>${fmt(opt(r).thickness)} mm, ${esc(deckType(r))}</th>${across.map((c) => {
        const o = cell(r, c);
        if (!o) return "<td>–</td>";
        if (!o.deck) return `<td class="status">${esc(o.deck_state || "not run")}</td>`;
        return `<td class="${safe(o) ? "mx-safe" : "mx-unsafe"}${o.best ? " trial-best-row" : ""}" title="${esc(o.label)}${o.deck.why ? `: ${esc(o.deck.why)}` : ""}">
          <b>${money(o.deck_cost_per_m)}</b> ${esc(cur())}/m<br><span class="hint">${fmt(o.deck.kg_per_m3_with_links)} kg/m³ · u ${fmt(o.deck.utilisation, 2)}${o.deck.punching_needed ? " · punching links" : ""}</span></td>`;
      }).join("")}</tr>`).join("")}</table></div>
      <p class="status">Each cell: the deck's cost per metre of berth, bars with shear and punching links in kg/m³ and its utilisation. Green: the deck is safe.</p>`;
  };

  const sorted = (rows) => {
    const k = { cost: (o) => o.cost_per_m ?? Infinity, deck: (o) => o.deck_cost_per_m ?? Infinity, steel: (o) => o.deck?.kg_per_m3_with_links ?? Infinity, util: (o) => o.deck?.utilisation ?? Infinity, order: () => 0 }[sortBy];
    return [...rows].sort((a, b) => (safe(b) ? 1 : 0) - (safe(a) ? 1 : 0) || k(a) - k(b));
  };

  const table = (rows) => {
    const done = rows.filter((o) => o.deck);
    const head = `<tr><th title="In the report">⎙</th><th>Thickness</th><th>Type</th><th>wk (mm)</th><th>Pile faces</th>${form.use.mesh ? "<th>Mesh</th>" : ""}${form.use.punching_per ? "<th>Punching design</th>" : ""}<th>Deck safe</th><th>Utilisation</th><th>Bars kg/m³</th><th>With links kg/m³</th>
      <th>Punching links</th><th>Shear link cells</th><th>Concrete m³/m</th><th>Rebar t/m</th><th>Deck ${esc(cur())}/m</th><th>Section ${esc(cur())}/m</th><th>Over the cheapest safe</th><th></th></tr>`;
    const body = sorted(rows).map((o) => {
      const dk = o.deck || {};
      const need = (dk.punching || []).filter((p) => p.needs_links);
      const tags = `${o.base ? ' <span class="chip small-chip">as set</span>' : ""}${o.best ? ' <span class="chip small-chip trial-best">cheapest safe</span>' : ""}`;
      if (!o.deck) return `<tr class="trial-idle"><td></td><td colspan="4">${esc(o.label)}${tags}</td><td colspan="${12 + (form.use.mesh ? 1 : 0) + (form.use.punching_per ? 1 : 0)}" class="status">${o.deck_state === "out of date" ? "Inputs changed: design again." : "Not designed yet."}</td></tr>`;
      return `<tr class="${o.best ? "trial-best-row" : ""}"><td><input type="checkbox" data-rep="${esc(o.key)}" ${ticked.has(o.key) ? "" : "checked"}></td>
        <td>${fmt(opt(o).thickness ?? dk.thickness_mm)}${tags}</td><td>${esc(deckType(o))}</td><td>${fmt(wkOf(o), 2)}</td><td>${esc(peaksOf(o))}</td>${form.use.mesh ? `<td>${fmt(opt(o).mesh) || "–"}</td>` : ""}${form.use.punching_per ? `<td>${esc(punchOf(o) || "–")}</td>` : ""}
        <td title="${esc(dk.why || "")}">${safe(o) ? '<span class="flag-ok">Yes</span>' : '<span class="flag-bad">No</span>'}${o.others_unsafe?.length ? `<div class="hint" title="${esc(o.others_unsafe.join(", "))}">${o.others_unsafe.length} other element${o.others_unsafe.length > 1 ? "s" : ""} not safe</div>` : ""}</td>
        <td class="cell ${dk.utilisation > 1 ? "error" : "ok"}">${fmt(dk.utilisation, 2)}</td><td>${fmt(dk.kg_per_m3)}</td><td>${fmt(dk.kg_per_m3_with_links)}</td>
        <td>${need.length ? need.map((p) => `${esc(p.pile)} (${fmt(p.heads)})`).join(", ") : "none"}${(dk.punching || []).some((p) => !p.passed) ? '<div class="flag-bad">some fail even with links</div>' : ""}</td>
        <td>${fmt(dk.shear?.cells_needing_links)}</td><td>${fmt(o.concrete_m3_per_m, 2)}</td><td>${fmt(o.rebar_t_per_m, 3)}</td>
        <td>${money(o.deck_cost_per_m)}</td><td><strong>${money(o.cost_per_m)}</strong></td>
        <td>${o.over_best_per_m == null ? "–" : o.over_best_per_m === 0 ? "0" : `+${fmt(o.over_best_per_m)}`}</td>
        <td><button class="small" data-show="${esc(o.key)}">${open.includes(o.key) ? "Hide" : "Bars"}</button></td></tr>`;
    }).join("");
    const url = (fmtx) => `${secUrl()}/matrix/report.${fmtx}?keys=${encodeURIComponent(done.filter((o) => !ticked.has(o.key)).map((o) => o.key).join(","))}`;
    return `<div class="row" style="margin-top:14px"><h3 style="margin:0">Options</h3>
        <label>Sort <select id="mx-sort">${[["cost", "section cost"], ["deck", "deck cost"], ["steel", "kg/m³ with links"], ["util", "utilisation"]].map(([k, t]) => `<option value="${k}" ${k === sortBy ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        ${done.length ? `<span>Report of the ticked options: <a class="quiet-link" href="${url("docx")}">Word</a> <a class="quiet-link" href="${url("pdf")}">PDF</a> <a class="quiet-link" href="${url("xlsx")}">Excel</a></span>` : ""}</div>
      <div class="scroll"><table class="trials">${head}${body}</table></div>
      ${done.length && !done.some(safe) ? '<p class="status flag-bad">No option has a safe deck: the grid and the report still show what each would need.</p>' : ""}
      <div id="mx-details"></div>`;
  };

  const draw = () => {
    // "As set" is only a reference for the costs; the options themselves are the rows.
    const rows = (data.options || []).filter((o) => !o.base);
    out.innerHTML = `<div class="panel">${h.picker()}
      ${data.slabs.length ? inputs() : '<p class="status">This section has no slab.</p>'}
      ${rows.some((o) => o.deck) ? `<h3 style="margin-top:14px">At a glance</h3>${grid(rows.filter((o) => !o.base))}` : ""}
      ${rows.length ? table(rows) : ""}</div>`;
    h.wirePicker();
    wire();
    showDetails();
  };

  const wire = () => {
    out.querySelectorAll("[data-f]").forEach((i) => {
      i.onchange = () => {
        const k = i.dataset.f;
        form[k] = i.type === "checkbox" ? i.checked : i.value;
        draw();
      };
    });
    out.querySelectorAll("[data-use]").forEach((i) => (i.onchange = () => {
      form.use[i.dataset.use] = i.checked;
      draw();
    }));
    out.querySelectorAll("[data-mesh]").forEach((i) => (i.onchange = () => {
      if (i.checked) form.mesh.add(+i.dataset.mesh);
      else form.mesh.delete(+i.dataset.mesh);
      draw();
    }));
    out.querySelectorAll("[data-punch]").forEach((i) => (i.onchange = () => {
      if (i.checked) form.punching.add(i.dataset.punch);
      else form.punching.delete(i.dataset.punch);
      draw();
    }));
    out.querySelectorAll("[data-peak]").forEach((i) => (i.onchange = () => {
      if (i.checked) form.peaks.add(i.dataset.peak);
      else form.peaks.delete(i.dataset.peak);
      draw();
    }));
    const run = out.querySelector("#mx-run");
    if (run) run.onclick = () => !running && go();
    const stop = out.querySelector("#mx-stop");
    if (stop) stop.onclick = () => running && (running.stopped = true, say("Stopping after this step…"));
    const sort = out.querySelector("#mx-sort");
    if (sort) sort.onchange = () => { sortBy = sort.value; draw(); };
    out.querySelectorAll("[data-rep]").forEach((c) => (c.onchange = () => {
      if (c.checked) ticked.delete(c.dataset.rep);
      else ticked.add(c.dataset.rep);
      draw();
    }));
    out.querySelectorAll("[data-show]").forEach((b) => (b.onclick = () => {
      const k = b.dataset.show;
      const i = open.indexOf(k);
      if (i >= 0) open.splice(i, 1);
      else open.unshift(k);
      draw();
    }));
  };

  // Each shown option: its bars as the design page draws them, its governing sections and punching.
  const cache = new Map();
  const showDetails = async () => {
    const host = out.querySelector("#mx-details");
    if (!host) return;
    for (const k of open) {
      const o = data.options.find((x) => x.key === k);
      if (!o?.design_key) continue;
      const card = document.createElement("div");
      card.className = "panel matrix-detail";
      host.append(card);
      const dk = o.deck;
      card.innerHTML = `<h3>${esc(o.label)} ${safe(o) ? '<span class="sev ok">deck safe</span>' : '<span class="sev error">deck not safe</span>'}</h3>
        ${dk.why ? `<p class="status">${esc(dk.why)}</p>` : ""}
        <div class="scroll"><table><tr><th>Face</th><th>Mesh</th><th>Governing at</th><th>Bars there</th><th>M (kNm/m)</th><th>Combination</th><th>MEd/MRd</th><th>wk / limit</th><th>Set by</th><th>Heaviest bars</th></tr>
        ${(dk.governing || []).map((g) => `<tr><td>${esc(g.name)}</td><td>${esc(g.mesh || "–")}</td><td>${esc(g.where)}</td><td>${esc(g.bars || "")}</td><td>${fmt(g.M_kNm_per_m)}</td><td>${esc(g.combination || "")}</td>
          <td class="cell ${g.ratio > 1 ? "error" : "ok"}">${fmt(g.ratio, 2)}</td><td>${g.wk_mm == null ? "–" : `${fmt(g.wk_mm, 3)} / ${fmt(g.wk_limit_mm, 2)}`}</td><td>${esc(g.set_by || "")}</td><td>${esc(g.heaviest_bars || "")}<div class="hint">${esc(g.heaviest_where || "")}</div></td></tr>`).join("")}</table></div>
        <p class="status">Shear: utilisation ${fmt(dk.shear?.utilisation, 2)}, ${fmt(dk.shear?.cells_needing_links)} cells need links${dk.shear?.heaviest ? `, heaviest ${esc(dk.shear.heaviest)}` : ""}${dk.shear?.governing ? `; governing ${esc(dk.shear.governing)}` : ""}.</p>
        ${(dk.punching || []).length ? `<div class="scroll"><table><tr><th>Pile type</th><th>Heads</th><th>Utilisation without links</th><th>Punching links</th><th>Passes</th></tr>
          ${dk.punching.map((p) => `<tr><td>${esc(p.pile)}</td><td>${fmt(p.heads)}</td><td>${fmt(p.utilisation, 2)}</td><td>${p.needs_links ? esc(p.links || "links alone cannot") : "not needed"}</td><td>${p.passed ? '<span class="flag-ok">yes</span>' : '<span class="flag-bad">no</span>'}</td></tr>`).join("")}</table></div>` : ""}
        ${(dk.ductility || []).length ? `<p class="status flag-bad">Over-reinforced: ${esc(dk.ductility.join("; "))}</p>` : ""}
        <h4>Reinforcement along X and Y</h4><div class="row" data-kind="bardiag-pick"></div><div class="chart wide" data-kind="bardiag"><p class="status">Loading…</p></div>`;
      try {
        let design = cache.get(o.design_key);
        if (!design) {
          design = await api(`${secUrl()}/matrix/design/${o.design_key}`);
          cache.set(o.design_key, design);
        }
        h.barDiagrams(card, design);
      } catch (e) {
        card.querySelector('[data-kind="bardiag"]').innerHTML = `<p class="status">${esc(e.message)}</p>`;
      }
    }
  };

  const say = (text) => {
    if (running) running.text = text;
    const s = out.querySelector("#mx-status");
    if (s) s.textContent = text;
  };

  const go = async () => {
    running = { text: "Starting…", stopped: false };
    draw();
    const key = `trials-${h.project().id}-${h.sectionId()}`;
    const poll = setInterval(async () => {
      try {
        const p = await api(`${ROOT}/api/progress/${key}`);
        if (p.step) say(`${p.step}…`);
      } catch {
        /* between requests */
      }
    }, 1500);
    let total = null;
    try {
      const body = JSON.stringify({ spec: spec(), budget_s: 3 });
      for (;;) {
        const res = await again(() => api(`${secUrl()}/matrix`, { method: "POST", body }));
        data = res;
        total ??= res.left + res.done;
        if (!res.left || running.stopped) break;
        running.text = `${total - res.left} of ${total} element designs done…`;
        draw();
      }
      running = null;
      draw();
    } catch (e) {
      running = null;
      draw();
      say(e.message);
    } finally {
      clearInterval(poll);
    }
  };

  out.innerHTML = '<p class="status">Loading…</p>';
  try {
    await load();
  } catch (e) {
    out.innerHTML = `${h.picker()}<p class="status">${esc(e.message)}</p>`;
    h.wirePicker();
    return;
  }
  draw();
}
