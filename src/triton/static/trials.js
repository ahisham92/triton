// Comparisons: one slab, beam or pile designed at several sizes, with its reinforcement (bars, shear
// links, punching links) and its cost per metre of berth side by side. Trials never change the
// section; "Use this size" does. app.js passes its helpers in: api, again, esc, fmt, secUrl, ROOT,
// project(), costingHash, and used(answer) after a size was taken.

import { renderMatrix } from "./matrix.js";

const KIND = { slabs: "slab", beams: "beam", piles: "pile" };
const FIELDS = {
  slabs: [["thickness", "Thickness"]],
  piles: [["diameter", "Diameter"]],
  beams: [["width", "Width"], ["depth", "Depth"]],
};
const VOIDS = [["thickness", "Thickness"], ["void_diameter", "Voids Ø"], ["void_spacing", "at"]];
const MAIN = { slabs: "thickness", piles: "diameter", beams: "depth" };
const fieldsOf = (el) => (el.kind === "slabs" && el.voided ? VOIDS : FIELDS[el.kind]);
const ALL = "*all*"; // the "All elements" choice: a change on every element of the section
const MATRIX = "*matrix*"; // the option matrix of a deck (matrix.js)

export async function renderTrials(host, h) {
  const { api, again, esc, fmt, secUrl, ROOT } = h;
  host.innerHTML = `<p class="sub">Design one element at several sizes and compare its reinforcement, links and cost per metre of berth.
      Trials never change the design; <strong>Use this size</strong> does. Costs use the unit prices on the Project tab and the berth
      length and spacings on the <a href="${h.costingHash}">Costing tab</a>.</p>
    <div id="tr-out"><p class="status">Loading…</p></div>`;
  const out = host.querySelector("#tr-out");
  let data;
  try {
    data = await api(`${secUrl()}/trials`);
  } catch (e) {
    out.innerHTML = `<p class="status">${esc(e.message)}</p>`;
    return;
  }
  if (!data.elements.length) {
    out.innerHTML = '<p class="status">This section has no slab, beam or pile to try.</p>';
    return;
  }
  const pid = h.project().id;
  const pickKey = `triton-trials-${pid}`;
  let picked = null;
  try {
    picked = localStorage.getItem(pickKey);
  } catch {
    /* no storage */
  }
  if (picked !== ALL && picked !== MATRIX && !data.elements.some((e) => e.element === picked)) picked = (data.elements.find((e) => e.kind === "slabs") || data.elements[0]).element;
  let sizes = null; // the list being edited, for the picked element
  let running = null;

  const cur = data.currency || "";
  const money = (v) => (v == null ? "–" : fmt(v));

  // Which element (or all of them) the tab is on.
  const picker = () => `<div class="row"><label>Element <select id="tr-el">
      <option value="${ALL}" ${picked === ALL ? "selected" : ""}>All elements (the whole section, a change on every element)</option>
      ${data.elements.some((e) => e.kind === "slabs") ? `<option value="${MATRIX}" ${picked === MATRIX ? "selected" : ""}>Deck option matrix (thicknesses × crack widths × pile-face methods × deck types)</option>` : ""}${data.elements
        .map((e) => `<option value="${esc(e.element)}" ${e.element === picked ? "selected" : ""}>${esc(e.element)} (${KIND[e.kind]}, now ${esc(e.current_label)})</option>`)
        .join("")}</select></label></div>`;
  const wirePicker = () => {
    out.querySelector("#tr-el").onchange = (e) => {
      picked = e.target.value;
      sizes = null;
      try {
        localStorage.setItem(pickKey, picked);
      } catch {
        /* no storage */
      }
      draw();
    };
  };

  const draw = () => {
    if (picked === ALL) return drawAll();
    if (picked === MATRIX) return renderMatrix(out, { ...h, picker, wirePicker });
    const el = data.elements.find((e) => e.element === picked);
    sizes ??= el.sizes.map((s) => ({ ...s }));
    const fields = fieldsOf(el);
    const slab = el.kind === "slabs";
    const pile = el.kind === "piles";
    const done = el.rows.filter((r) => r.state === "done");
    const maxCost = Math.max(0, ...done.map((r) => r.cost_per_m || 0));
    const head = `<tr><th>Size (mm)</th><th>Safe</th><th>Utilisation</th><th>Cost per m (${esc(cur)}/m)</th><th>Against current, per m</th>
      ${data.berth_length_m ? `<th>Whole ${fmt(data.berth_length_m)} m berth</th>` : ""}<th>Bars kg/m³</th>
      ${slab ? "<th>With links kg/m³</th><th>Shear links kg/m²</th><th>Punching</th>" : "<th>Links</th>"}
      ${pile ? "<th>Cage</th>" : ""}
      <th>Concrete m³/m</th><th>Reinforcement t/m</th><th></th></tr>`;
    // Saving (green) or extra cost (red) against the current size; an unsafe trial's is only shown.
    const saving = (v, safe) => (v == null ? "–" : v === 0 ? "0" : `<span class="${!safe ? "" : v > 0 ? "flag-ok" : "flag-bad"}">${v > 0 ? "saves " : "costs "}${fmt(Math.abs(v))}</span>`);
    const rows = el.rows
      .map((r) => {
        const tags = `${r.current ? ' <span class="chip small-chip">current</span>' : ""}${r.best ? ' <span class="chip small-chip trial-best">cheapest safe</span>' : ""}`;
        if (r.state !== "done") {
          const why = r.state === "error" ? esc(r.error) : r.state === "out of date" ? "Inputs changed since this trial: run it again." : "Not run yet.";
          return `<tr class="trial-idle"><td>${esc(r.label)}${tags}</td><td colspan="${(slab ? 11 : pile ? 10 : 9) + (data.berth_length_m ? 1 : 0)}" class="status">${why}</td></tr>`;
        }
        const punch = slab
          ? `${r.punching_need_links || 0} of ${r.punching_heads || 0} need links${r.punching_fail ? `, <span class="flag-bad">${r.punching_fail} fail</span>` : ""}`
          : "";
        const bar = maxCost && r.cost_per_m != null ? `<span class="trial-bar"><i style="width:${(100 * r.cost_per_m) / maxCost}%"></i></span>` : "";
        return `<tr class="${r.best ? "trial-best-row" : ""}"><td>${esc(r.label)}${tags}</td>
          <td title="${esc(r.why || "")}">${r.passed ? '<span class="flag-ok">Yes</span>' : '<span class="flag-bad">No</span>'}</td>
          <td class="cell ${r.utilisation > 1 ? "error" : "ok"}">${fmt(r.utilisation, 2)}</td>
          <td><strong>${money(r.cost_per_m)}</strong>${bar}${r.missing?.length ? `<div class="flag-bad">Missing: ${esc(r.missing.join(", "))}</div>` : ""}</td>
          <td>${saving(r.saving_per_m, r.passed)}</td>
          ${data.berth_length_m ? `<td>${saving(r.saving, r.passed)}</td>` : ""}
          <td>${fmt(r.kg_per_m3)}${slab && r.mesh_mm ? `<div class="hint">${fmt(r.mesh_mm)} mm mesh</div>` : ""}</td>
          ${slab
            ? `<td>${fmt(r.kg_per_m3_with_links)}</td><td>${fmt(r.shear_links_kg_per_m2, 1)}</td><td>${punch}${r.punching_links_kg_per_m2 ? `<div class="hint">links ${fmt(r.punching_links_kg_per_m2, 1)} kg/m²</div>` : ""}</td>`
            : `<td>${esc(r.links || "–")}</td>`}
          ${pile ? `<td>${esc(r.bars || "–")}</td>` : ""}
          <td>${fmt(r.concrete_m3_per_m, 2)}</td><td>${fmt(r.rebar_t_per_m, 3)}</td>
          <td>${r.current ? "" : `<button class="small" data-use="${esc(r.key)}">Use this size</button>`}</td></tr>`;
      })
      .join("");
    const editor = sizes
      .map(
        (s, i) => `<span class="chip trial-size">${fields
          .map(([k, t]) => `<label title="${t} (mm)">${fields.length > 1 ? `${t} ` : ""}<input type="number" min="50" step="any" data-i="${i}" data-k="${k}" value="${s[k] ?? ""}" style="width:5.5em"></label>`)
          .join(el.kind === "beams" ? " × " : " ")}<button class="small" data-drop="${i}" title="Take this size off">×</button></span>`,
      )
      .join(" ");
    out.innerHTML = `<div class="panel">
        ${picker()}
        <p class="status" style="margin-bottom:4px">Sizes to try (mm)${el.kind === "beams" ? ", width × depth" : el.voided ? ", slab thickness with the voids' diameter and spacing" : ""}:</p>
        <div class="row" style="flex-wrap:wrap;gap:6px">${editor}
          <button class="small" id="tr-add">Add a size</button><button class="small" id="tr-reset">Around the current size</button></div>
        <div class="row" style="margin-top:10px"><button id="tr-run">${running ? "Running…" : "Run the trials"}</button>
          ${running ? '<button id="tr-stop" class="quiet">Stop</button>' : ""}<span class="status" id="tr-status">${running ? esc(running.text) : ""}</span></div>
        <p class="status">Each trial is the full design of ${esc(el.element)} at that size${slab ? ": bending with the 150 / 200 mm mesh choice, crack widths, shear links and punching" : el.kind === "beams" ? ": bending, cracks, shear and torsion links" : ": N-M cage, cracks and links"}.
          Bars you set by hand belong to the current size, so each trial picks its own. Sizes already run from the same inputs are not run again.
          ${pile ? "The deck's punching and the beams' supports use the pile diameter; they are not redesigned in a pile trial." : ""}</p>
      </div>
      ${data.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}
      <div class="panel scroll"><table class="cost trials">${head}${rows}</table></div>
      ${done.some((r) => !r.passed) ? `<ul class="status trial-why">${done.filter((r) => !r.passed).map((r) => `<li><strong>${esc(r.label)}</strong> is not safe: ${esc(r.why || "see the Design tab.")}</li>`).join("")}</ul>` : ""}
      ${done.length && !done.some((r) => r.passed) ? '<p class="status flag-bad">None of the trials run is safe.</p>' : ""}`;

    wirePicker();
    out.querySelectorAll("[data-i]").forEach((inp) => {
      inp.onchange = () => {
        sizes[+inp.dataset.i][inp.dataset.k] = inp.value === "" ? null : Number(inp.value);
      };
    });
    out.querySelectorAll("[data-drop]").forEach((b) => {
      b.onclick = () => {
        sizes.splice(+b.dataset.drop, 1);
        draw();
      };
    });
    out.querySelector("#tr-add").onclick = () => {
      const last = sizes[sizes.length - 1] || el.current;
      const step = el.kind === "slabs" ? 50 : el.kind === "piles" ? 200 : 100;
      const k = el.kind === "slabs" ? "thickness" : el.kind === "piles" ? "diameter" : "depth";
      sizes.push({ ...last, [k]: (last[k] || 0) + step });
      draw();
    };
    out.querySelector("#tr-reset").onclick = () => {
      const c = el.current;
      const k = el.kind === "slabs" ? "thickness" : el.kind === "piles" ? "diameter" : "depth";
      const steps = el.kind === "slabs" ? [-100, -50, 0, 50, 100] : el.kind === "piles" ? [-200, 0, 200] : [-200, -100, 0, 100, 200];
      sizes = steps.map((d) => ({ ...c, [k]: c[k] + d })).filter((s) => s[k] > 0);
      draw();
    };
    out.querySelector("#tr-run").onclick = () => !running && run(el);
    const stop = out.querySelector("#tr-stop");
    if (stop) stop.onclick = () => (running.stopped = true);
    out.querySelectorAll("[data-use]").forEach((b) => {
      b.onclick = () => use(el, el.rows.find((r) => r.key === b.dataset.use));
    });
  };

  // ---- All elements: the whole section designed with a change on every element (the crack width
  // limit), against the section as set.
  let scen = null; // GET scenarios
  let limits = null; // the crack width limits being edited
  const drawAll = async () => {
    if (!scen) {
      out.innerHTML = `<div class="panel">${picker()}<p class="status">Loading…</p></div>`;
      wirePicker();
      try {
        scen = await api(`${secUrl()}/scenarios`);
      } catch (e) {
        out.querySelector(".panel").insertAdjacentHTML("beforeend", `<p class="status">${esc(e.message)}</p>`);
        return;
      }
      if (picked !== ALL) return;
    }
    limits ??= scen.variants.filter((v) => !v.base).map((v) => v.variant.crack_width_limit);
    const cols = scen.variants;
    const base = cols[0];
    const saving = (v, c) => (v == null ? "–" : c.base ? "–" : v === 0 ? "0" : `<span class="${v > 0 ? "flag-ok" : "flag-bad"}">${v > 0 ? "saves " : "costs "}${fmt(Math.abs(v))}</span>`);
    const cell = (c, f) => (c.complete ? f(c) : `<span class="status">${c.missing} element${c.missing === 1 ? "" : "s"} not designed yet</span>`);
    const line = (label, f, strong) => `<tr><th>${label}</th>${cols.map((c) => `<td>${strong ? "<strong>" : ""}${cell(c, f)}${strong ? "</strong>" : ""}</td>`).join("")}</tr>`;
    const elRow = (n) =>
      `<tr><td>${esc(n)}</td>${cols
        .map((c) => {
          const e = c.elements[n] || {};
          if (e.state !== "done") return `<td class="status">${esc(e.state || "–")}</td>`;
          return `<td><span class="${e.passed ? "flag-ok" : "flag-bad"}">${fmt(e.utilisation, 2)}</span>${e.cost_per_m != null ? ` · ${fmt(e.cost_per_m)}/m` : ""}${e.kg_per_m3 != null ? `<div class="hint">${fmt(e.kg_per_m3)} kg/m³</div>` : ""}</td>`;
        })
        .join("")}</tr>`;
    out.innerHTML = `<div class="panel">
        ${picker()}
        <p class="status" style="margin-bottom:4px">Crack width limits to try (mm), each on every element and both faces, against the section as set:</p>
        <div class="row" style="flex-wrap:wrap;gap:6px">${limits
          .map((v, i) => `<span class="chip trial-size"><input type="number" min="0.05" max="0.5" step="0.05" data-w="${i}" value="${v ?? ""}" style="width:5em"><button class="small" data-wdrop="${i}" title="Take this off">×</button></span>`)
          .join(" ")}<button class="small" id="sc-add">Add a limit</button></div>
        <div class="row" style="margin-top:10px"><button id="sc-run">${running ? "Running…" : "Design the whole section"}</button>
          ${running ? '<button id="tr-stop" class="quiet">Stop</button>' : ""}<span class="status" id="tr-status">${running ? esc(running.text) : ""}</span></div>
        <p class="status">Every element is designed again for the section as set and for each limit, without the bars you set by hand, so the
          columns differ only by the limit. Elements with nothing to change (steel) are designed once. This takes a while: about as long as
          designing the section once per column. Nothing here changes your design; set the limit on the Elements tab to use it.</p>
      </div>
      <div class="panel scroll"><table class="cost trials">
        <tr><th></th>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr>
        ${line(`Cost per m (${esc(cur)}/m)`, (c) => money(c.cost_per_m), true)}
        ${line("Against as set, per m", (c) => saving(c.saving_per_m, c))}
        ${scen.berth_length_m ? line(`Whole ${fmt(scen.berth_length_m)} m berth`, (c) => saving(c.saving, c)) : ""}
        ${line("Concrete m³/m", (c) => fmt(c.concrete_m3_per_m, 2))}
        ${line("Reinforcement t/m", (c) => fmt(c.rebar_t_per_m, 3))}
        ${line("Structural steel t/m", (c) => fmt(c.steel_t_per_m, 3))}
        ${line("Safe elements", (c) => `${c.safe_count} of ${c.safe_count + c.unsafe.length}${c.unsafe.length ? `<div class="hint flag-bad">Not safe: ${esc(c.unsafe.join(", "))}</div>` : ""}`)}
        <tr><th colspan="${cols.length + 1}" style="padding-top:14px">Each element: utilisation · cost per m</th></tr>
        ${scen.elements.map(elRow).join("")}
      </table></div>
      ${cols.some((c) => c.missing_prices?.length) ? `<p class="status flag-bad">Prices missing, so totals leave them out: ${esc([...new Set(cols.flatMap((c) => c.missing_prices || []))].join(", "))}.</p>` : ""}
      ${base.complete && !scen.berth_length_m ? '<p class="status">No berth length on the Costing tab: costs are per metre over the length the model covers.</p>' : ""}`;
    wirePicker();
    out.querySelectorAll("[data-w]").forEach((inp) => {
      inp.onchange = () => (limits[+inp.dataset.w] = inp.value === "" ? null : Number(inp.value));
    });
    out.querySelectorAll("[data-wdrop]").forEach((b) => {
      b.onclick = () => {
        limits.splice(+b.dataset.wdrop, 1);
        drawAll();
      };
    });
    out.querySelector("#sc-add").onclick = () => {
      limits.push(Math.min(0.5, Math.round(((limits[limits.length - 1] ?? 0.2) + 0.05) * 100) / 100));
      drawAll();
    };
    out.querySelector("#sc-run").onclick = () => !running && runAll();
    const stop = out.querySelector("#tr-stop");
    if (stop) stop.onclick = () => (running.stopped = true);
  };

  const runAll = async () => {
    const variants = limits.filter((v) => v).map((v) => ({ crack_width_limit: v }));
    running = { text: "Starting…", stopped: false };
    drawAll();
    const key = `trials-${pid}-${h.sectionId()}`;
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
      for (;;) {
        const res = await again(() => api(`${secUrl()}/scenarios`, { method: "POST", body: JSON.stringify({ variants, budget_s: 3 }) }));
        scen = res;
        total ??= res.left + res.done;
        if (!res.left || running.stopped) break;
        running.text = `${total - res.left} of ${total} element designs done…`;
        drawAll();
      }
      running = null;
      limits = null;
      drawAll();
    } catch (e) {
      running = null;
      drawAll();
      say(e.message);
    } finally {
      clearInterval(poll);
    }
  };

  const say = (text) => {
    if (running) running.text = text;
    const s = out.querySelector("#tr-status");
    if (s) s.textContent = text;
  };

  const run = async (el) => {
    const asked = sizes.filter((s) => s[MAIN[el.kind]]);
    if (!asked.length) return say("List at least one size.");
    running = { text: "Starting…", stopped: false };
    draw();
    const key = `trials-${pid}-${h.sectionId()}`;
    const poll = setInterval(async () => {
      try {
        const p = await api(`${ROOT}/api/progress/${key}`);
        if (p.step) say(`${p.step}…`);
      } catch {
        /* between requests */
      }
    }, 1500);
    try {
      for (;;) {
        const res = await again(() =>
          api(`${secUrl()}/trials`, { method: "POST", body: JSON.stringify({ element: el.element, sizes: asked, budget_s: 3 }) }),
        );
        data = res;
        sizes = data.elements.find((e) => e.element === el.element).sizes.map((s) => ({ ...s }));
        const n = asked.length - res.left.length;
        say(res.left.length ? `${n} of ${asked.length} sizes designed…` : "");
        if (!res.left.length || running.stopped) break;
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

  const use = async (el, r) => {
    if (!r) return;
    if (!confirm(`Give ${el.element} the size ${r.label} and take this trial as its design? Bars set by hand for its current size go.`)) return;
    try {
      const res = await api(`${secUrl()}/trials/use`, { method: "POST", body: JSON.stringify({ element: el.element, size: r.size }) });
      h.used(res);
      data = await api(`${secUrl()}/trials`);
      sizes = null;
      draw();
      if (res.affected?.length)
        say(`${el.element} is now ${r.label}. ${res.affected.join(", ")} ${res.affected.length === 1 ? "was" : "were"} designed with its old size: design ${res.affected.length === 1 ? "it" : "them"} again on the Design tab.`);
      else say(`${el.element} is now ${r.label}.`);
    } catch (e) {
      say(e.message);
    }
  };

  draw();
}
