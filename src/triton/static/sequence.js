// Construction sequence: the section's works in the order they are built, played stage by stage in 3D,
// with the existing structure on site (drawn, demolished where the sequence says, checked for clashes
// with the new piles and walls) and what its tie rods do to the new wall's movement. Never a design input.
// app.js passes its helpers in: api, esc, fmt, tabData, sectionGeometry, sectionSite, saveSite, View3D,
// section() -> the section object (edited in place), markDirty(), save(), existingForm() -> a form element.

const STATE_WORDS = {
  pipe: "steel pipe driven, empty",
  cage: "cage in, not cast yet",
  cast_high: "cast above the cut-off level",
  done: "built",
};
const TINT = { pipe: "#64748b", cage: "#b45309", active: "#0f766e" };

export async function renderSequence(host, h) {
  const { esc, fmt, tabData } = h;
  // One line per element and kind of clash, most first.
  const summaryHtml = (rows) => {
    const kinds = [
      [/existing pile/, "too close to existing piles"],
      [/tie rod/, "hit the tie rods"],
      [/existing combi wall/, "hit the existing combi wall"],
      [/existing blocks/, "run into the blocks"],
      [/quarry run/, "are bored through quarry run"],
      [/warehouse/, ""],
    ];
    const groups = new Map();
    for (const f of rows) {
      const k = kinds.find(([re]) => re.test(f.what));
      if (!k || !k[1] || !/^Pile at/.test(f.what)) {
        groups.set(f.what, { level: f.level, text: `${f.element}: ${f.what.startsWith(`${f.element} `) ? f.what.slice(f.element.length + 1) : f.what}`, n: 1 });
        continue;
      }
      const key = `${f.element}|${k[1]}`;
      const g = groups.get(key) || { level: f.level, element: f.element, words: k[1], n: 0 };
      g.n += 1;
      groups.set(key, g);
    }
    const items = [...groups.values()].map((g) =>
      g.words ? { ...g, text: `${g.element}: ${g.n} pile${g.n > 1 ? "s" : ""} ${g.words}` } : g);
    return items.length ? `<ul class="status">${items.map((g) => `<li><span class="${g.level === "clash" ? "flag-bad" : g.level === "warning" ? "flag-warn" : "hint"}">${esc(g.level)}</span> ${esc(g.text)}</li>`).join("")}</ul>` : "";
  };
  host.innerHTML = `<p class="sub">The order this section is built in, one stage at a time. Steps ticked "at the same time" play together.
      The existing structure is drawn where the section has one, taken away at the demolition, and checked against the new piles and walls.</p>
    <div class="v3d-layout"><div><div class="panel"><div class="row seq-bar"><button class="quiet" data-prev aria-label="Previous stage">◀</button>
      <input type="range" data-stage min="1" max="1" value="1" style="flex:1" aria-label="Stage">
      <button class="quiet" data-next aria-label="Next stage">▶</button><button class="quiet" data-play>Play</button></div>
      <h3 data-title style="margin:6px 0 2px"></h3><p class="status" data-what></p><div id="seq-view"></div></div>
      <div class="panel" style="margin-top:10px" id="seq-clashes"></div>
      <div class="panel" style="margin-top:10px" id="seq-ties"></div></div>
    <div class="panel v3d-side"><h3 style="margin-top:0">Steps</h3><div id="seq-steps"></div>
      <details class="panel" style="margin-top:10px" data-free id="seq-existing"><summary>Existing structure on this section</summary></details></div></div>
    <p class="status" id="seq-notes"></p>`;
  const $ = (s) => host.querySelector(s);
  $("#seq-existing").append(h.existingForm());
  let combo = "";
  let data;
  let geo;
  let site;
  try {
    [data, geo, site] = await Promise.all([tabData(`sequence?combination=${encodeURIComponent(combo)}`), h.sectionGeometry(), h.sectionSite()]);
  } catch (e) {
    $("#seq-view").innerHTML = `<p class="status">${esc(e.message)}</p>`;
    return;
  }
  if (!host.isConnected) return;
  if (!geo) {
    $("#seq-view").innerHTML = '<p class="status">Upload this section\'s workbook on the Workbook tab first.</p>';
    return;
  }
  const view = new h.View3D($("#seq-view"), { height: 520, onSite: h.saveSite, legend: false });
  const slider = $("[data-stage]");
  const stages = data.stages;
  slider.max = Math.max(stages.length, 1);
  let n = Math.min(h.lastStage ?? stages.length, stages.length) || 1;
  // Clash pins for what is built by the stage shown (the warehouses' and walls' rows have no point).
  const pins = (st) =>
    (data.existing?.found || []).filter((f) => f.at && f.level !== "note" && st.elements[f.element]).map((f) => ({ at: f.at, level: f.level, tip: `${f.element}: ${f.what}` }));

  const show = () => {
    const st = stages[n - 1];
    slider.value = n;
    h.lastStage = n;
    if (!st) {
      view.setScene({ elements: geo.elements, site });
      $("[data-title]").textContent = "No steps";
      return;
    }
    $("[data-title]").textContent = `Stage ${n} of ${stages.length}: ${st.steps.map((s) => s.name).join(" + ")}`;
    const active = new Set(st.active);
    const elements = geo.elements.map((e) => {
      const state = st.elements[e.element];
      if (!state) return e;
      const now = active.has(e.element);
      const tint = state === "pipe" || state === "cage" ? TINT[state] : now ? TINT.active : null;
      return { ...e, tint, thin: state === "cage", tip: `${e.element}: ${STATE_WORDS[state]}${now ? " (this stage)" : ""}` };
    });
    // Piles cast above their cut-off level; the heads broken down in their own stage.
    const extras = [];
    const breaking = st.steps.some((s) => s.work === "pile_heads");
    for (const e of geo.elements) {
      const state = st.elements[e.element];
      if (!e.lines || (state !== "cast_high" && !(breaking && active.has(e.element) && state === "done"))) continue;
      const size = site?.sizes?.[e.element]?.round;
      for (const [x, y, top] of e.lines)
        extras.push({ a: [x, y, top], b: [x, y, top + data.cast_above], color: state === "cast_high" ? "#ea580c" : "#dc2626",
          size: size || null, alpha: state === "cast_high" ? 1 : 0.55, element: e.element,
          tip: state === "cast_high" ? `${e.element}: cast ${fmt(data.cast_above, 1)} m above its cut-off level (${fmt(top, 2)} m)`
            : `${e.element}: head broken down to the cut-off level (${fmt(top, 2)} m)` });
    }
    view.setScene({ elements, site, stage: st, extras, pins: pins(st) });
    const built = Object.entries(st.elements).map(([k, v]) => `${esc(k)}: ${STATE_WORDS[v]}`);
    $("[data-what]").innerHTML = [
      built.length ? built.join(" · ") : "Nothing built yet.",
      st.demolished ? "Existing front beam and slab demolished." : "",
      `Seabed ${fmt(st.seabed, 2)} m.`,
    ].filter(Boolean).join(" ");
    host.querySelectorAll("[data-row]").forEach((r) => r.classList.toggle("on", st.steps.some((s) => String(s.index) === r.dataset.row)));
  };
  slider.oninput = () => {
    n = Number(slider.value);
    show();
  };
  $("[data-prev]").onclick = () => ((n = Math.max(1, n - 1)), show());
  $("[data-next]").onclick = () => ((n = Math.min(stages.length, n + 1)), show());
  let timer = null;
  $("[data-play]").onclick = (e) => {
    if (timer) {
      clearInterval(timer);
      timer = null;
      e.target.textContent = "Play";
      return;
    }
    e.target.textContent = "Stop";
    if (n >= stages.length) n = 0;
    const tick = () => {
      if (!host.isConnected || n >= stages.length) {
        clearInterval(timer);
        timer = null;
        e.target.textContent = "Play";
        return;
      }
      n += 1;
      show();
    };
    tick();
    timer = setInterval(tick, 1500);
  };

  // Steps: edited on the section; empty means the default order.
  const steps = () => h.section().sequence.steps;
  const edit = async (fn) => {
    const sec = h.section();
    if (!sec.sequence.steps.length) sec.sequence.steps = data.steps.map(({ work, name, with_previous }) => ({ work, name: "", with_previous }));
    fn(sec.sequence.steps);
    h.markDirty();
    await h.save();
    h.again();
  };
  const stepsHtml = () => {
    const opts = (w) => Object.entries(data.works).map(([k, t]) => `<option value="${k}" ${k === w ? "selected" : ""}>${esc(t)}</option>`).join("");
    return `<p class="status">${data.default ? "The default order for this section. Change any step to keep your own." : "Your own order for this section."}</p>
      <ol class="seq-steps">${data.steps.map((s, i) => `<li data-row="${i}"><select data-work="${i}" aria-label="Work">${opts(s.work)}</select>
        <input data-name="${i}" placeholder="${esc(data.works[s.work])}" value="${esc(steps()[i]?.name || "")}" aria-label="Name">
        <label class="toggle"><input type="checkbox" data-with="${i}" ${s.with_previous ? "checked" : ""} ${i ? "" : "disabled"}> Same time as the step before</label>
        <span><button class="quiet" data-up="${i}" ${i ? "" : "disabled"} aria-label="Move up">↑</button><button class="quiet" data-down="${i}" ${i < data.steps.length - 1 ? "" : "disabled"} aria-label="Move down">↓</button>
        <button class="quiet" data-del="${i}" aria-label="Remove">✕</button></span></li>`).join("")}</ol>
      <div class="row"><button class="quiet" data-add>Add a step</button>${data.default ? "" : '<button class="quiet" data-reset>Back to the default order</button>'}</div>
      <label class="toggle" style="margin-top:8px">Piles cast above their cut-off level by <input type="number" step="0.1" min="0" data-above value="${data.cast_above}" style="width:5em"> m</label>`;
  };
  const box = $("#seq-steps");
  box.innerHTML = stepsHtml();
  box.querySelectorAll("[data-work]").forEach((el) => (el.onchange = () => edit((s) => (s[+el.dataset.work].work = el.value))));
  box.querySelectorAll("[data-name]").forEach((el) => (el.onchange = () => edit((s) => (s[+el.dataset.name].name = el.value))));
  box.querySelectorAll("[data-with]").forEach((el) => (el.onchange = () => edit((s) => (s[+el.dataset.with].with_previous = el.checked))));
  box.querySelectorAll("[data-up]").forEach((el) => (el.onclick = () => edit((s) => {
    const i = +el.dataset.up;
    [s[i - 1], s[i]] = [s[i], s[i - 1]];
  })));
  box.querySelectorAll("[data-down]").forEach((el) => (el.onclick = () => edit((s) => {
    const i = +el.dataset.down;
    [s[i + 1], s[i]] = [s[i], s[i + 1]];
  })));
  box.querySelectorAll("[data-del]").forEach((el) => (el.onclick = () => edit((s) => s.splice(+el.dataset.del, 1))));
  box.querySelector("[data-add]").onclick = () => edit((s) => s.push({ work: "furniture", name: "", with_previous: false }));
  const reset = box.querySelector("[data-reset]");
  if (reset) reset.onclick = () => edit((s) => s.splice(0, s.length)).then(() => {});
  box.querySelector("[data-above]").onchange = (e) => {
    h.section().sequence.cast_above = Math.max(0, Number(e.target.value) || 0);
    h.markDirty();
    h.save().then(() => h.again());
  };
  box.querySelectorAll("[data-row]").forEach((r) => (r.onclick = (e) => {
    if (e.target.closest("select,input,button")) return;
    const k = stages.findIndex((st) => st.steps.some((s) => String(s.index) === r.dataset.row));
    if (k >= 0) ((n = k + 1), show());
  }));

  // Clashes with the existing structure.
  const ex = data.existing;
  const cl = $("#seq-clashes");
  if (!ex) {
    cl.innerHTML = `<h3 style="margin-top:0">Existing structure</h3><p class="status">None on this section. Tick "This section has an existing structure" under
      Existing structure to draw it, sequence its demolition and check the new piles against it.</p>`;
    $("#seq-ties").hidden = true;
  } else {
    const c = ex.counts;
    const rows = ex.found;
    cl.innerHTML = `<h3 style="margin-top:0">Clashes with the existing structure</h3>
      <p class="status">${c.clash ? `<span class="flag-bad">${c.clash} clash${c.clash > 1 ? "es" : ""}</span>` : '<span class="flag-ok">No clashes</span>'}${c.warning ? `, ${c.warning} warning${c.warning > 1 ? "s" : ""}` : ""}.
      Red pins in the view are clashes, amber ones warnings: hover them.</p>
      ${summaryHtml(rows)}
      ${rows.length ? `<details><summary>Every clash and warning (${rows.length})</summary><div class="scroll"><table class="cost"><tr><th></th><th>Element</th><th>What</th></tr>${rows.map((f) => `<tr><td><span class="${f.level === "clash" ? "flag-bad" : f.level === "warning" ? "flag-warn" : "hint"}">${esc(f.level)}</span></td>
        <td>${esc(f.element)}</td><td>${esc(f.what)}</td></tr>`).join("")}</table></div></details>` : ""}
      <p class="status">${(ex.layout.notes || []).map(esc).join(" ")}</p>`;
    const t = ex.tie_rods;
    const ties = $("#seq-ties");
    if (!t?.use) ties.hidden = true;
    else {
      const combos = t.combinations || [];
      ties.innerHTML = `<h3 style="margin-top:0">Tie rods and the new wall's movement</h3>
        ${combos.length ? `<label class="toggle">Combination <select data-tcombo>${combos.map((k) => `<option ${k === t.combination ? "selected" : ""}>${esc(k)}</option>`).join("")}</select></label>` : ""}
        ${t.u_mm != null ? `<div class="fields" style="margin:8px 0"><div><b>${fmt(t.u_mm, 1)} mm</b><br><span class="hint">at the tie level, no tie rods</span></div>
          <div><b>${fmt(t.u_with_mm, 1)} mm</b><br><span class="hint">with the tie rods (${t.reduction_pct}% less)</span></div>
          <div><b>${fmt(t.rod_force_kN, 0)} kN</b><br><span class="hint">per rod, ${fmt(t.rod_utilisation, 2)} of fy·A</span></div></div>`
          : `<p class="status">Movement at the tie level cut by ${t.reduction_pct}% while the tie rods hold. Run the design to see it in mm.</p>`}
        <ul class="status">${t.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
      const sel = ties.querySelector("[data-tcombo]");
      if (sel)
        sel.onchange = async () => {
          combo = sel.value;
          try {
            const d = await tabData(`sequence?combination=${encodeURIComponent(combo)}`);
            data.existing.tie_rods = d.existing.tie_rods;
            sel.disabled = true;
            renderTies(d.existing.tie_rods);
          } catch (e) {
            ties.insertAdjacentHTML("beforeend", `<p class="status">${esc(e.message)}</p>`);
          }
        };
      const renderTies = (tt) => {
        ties.querySelector(".fields")?.remove();
        const ul = ties.querySelector("ul");
        ul.innerHTML = tt.notes.map((x) => `<li>${esc(x)}</li>`).join("");
        if (tt.u_mm != null)
          ul.insertAdjacentHTML("beforebegin", `<div class="fields" style="margin:8px 0"><div><b>${fmt(tt.u_mm, 1)} mm</b><br><span class="hint">at the tie level, no tie rods</span></div>
            <div><b>${fmt(tt.u_with_mm, 1)} mm</b><br><span class="hint">with the tie rods (${tt.reduction_pct}% less)</span></div>
            <div><b>${fmt(tt.rod_force_kN, 0)} kN</b><br><span class="hint">per rod, ${fmt(tt.rod_utilisation, 2)} of fy·A</span></div></div>`);
        sel.disabled = false;
      };
    }
  }
  $("#seq-notes").innerHTML = data.notes.map(esc).join(" ");
  show();
}
