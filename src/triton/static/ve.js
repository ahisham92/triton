// Value engineering: the ideas Triton can design and cost for a section (sizes, voids, crack width
// limits, the slab's pile-face method), each costed on its own or mixed with others, against the
// section as set. Every run designs the whole section, so a change that moves other elements (a pile
// diameter under the deck) is costed with them. Nothing here changes the design.
// app.js passes its helpers in: api, again, esc, fmt, secUrl, ROOT, project(), sectionId(), costingHash.

export async function renderValueEngineering(host, h) {
  const { api, again, esc, fmt, secUrl, ROOT } = h;
  host.innerHTML = `<p class="sub">Ideas to bring the cost down (or check that a heavier option pays), each designed with the whole section and
      costed per metre of berth against the section as set. Tick ideas, add them on their own or as a mix, then design and cost them.
      Nothing here changes your design. Prices and the berth length are on the Project and <a href="${h.costingHash}">Costing</a> tabs.</p>
    <div id="ve-out"><p class="status">Loading…</p></div>`;
  const out = host.querySelector("#ve-out");
  let data;
  try {
    data = await api(`${secUrl()}/value-engineering`);
  } catch (e) {
    out.innerHTML = `<p class="status">${esc(e.message)}</p>`;
    return;
  }
  const cur = data.currency || "";
  const money = (v) => (v == null ? "–" : fmt(v));
  const ticked = new Set();
  const values = {}; // idea id -> the value typed over the suggested one
  // What to cost: the lines already costed, and the ones added since. One line per change.
  const fromServer = () => data.variants.filter((v) => !v.base).map((v) => ({ ...v.variant, label: v.label, ideas: v.ideas }));
  let queue = fromServer();
  const canon = (x) => JSON.stringify(x, (k, v) => (v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort()) : v));
  const changeKey = (q) => canon({ crack_width_limit: q.crack_width_limit ?? null, elements: q.elements ?? null, approach: q.approach ?? null });
  const enqueue = (q) => {
    queue = queue.filter((x) => changeKey(x) !== changeKey(q));
    queue.push(q);
  };
  let running = null;

  const byId = () => Object.fromEntries(data.ideas.map((i) => [i.id, i]));
  // The idea's change with the value typed over it.
  const changeOf = (idea) => {
    const c = structuredClone(idea.change);
    const v = values[idea.id];
    if (v == null || idea.what === "peaks") return c;
    if (c.elements) c.elements[idea.element][idea.what] = v;
    else if (c.approach) c.approach[idea.what] = v;
    else c[idea.what] = v;
    return c;
  };
  const valueOf = (idea) => {
    const c = idea.change;
    return values[idea.id] ?? (c.elements ? c.elements[idea.element][idea.what] : c.approach ? c.approach[idea.what] : c[idea.what]);
  };
  const WHAT = { thickness: "thickness", depth: "depth", diameter: "diameter", void_diameter: "void diameter", void_spacing: "void spacing", crack_width_limit: "crack width limit", length: "length", ledge_depth: "ledge depth", ledge_projection: "ledge projection" };
  const shortLabel = (idea) =>
    values[idea.id] == null ? idea.label : `${idea.element ?? "Every element"}: ${WHAT[idea.what]} ${values[idea.id]} ${idea.unit || "mm"}`;
  const merge = (ideas) => {
    const c = {};
    for (const idea of ideas) {
      const x = changeOf(idea);
      if (x.crack_width_limit != null) c.crack_width_limit = x.crack_width_limit;
      for (const [n, e] of Object.entries(x.elements || {})) c.elements = { ...c.elements, [n]: { ...(c.elements?.[n] || {}), ...e } };
      if (x.approach) c.approach = { ...(c.approach || {}), ...x.approach };
    }
    return c;
  };

  const draw = () => {
    const ideas = data.ideas;
    const groups = [...new Set(ideas.map((i) => i.element ?? ""))];
    const ideaRows = groups
      .map((g) => {
        const rows = ideas
          .filter((i) => (i.element ?? "") === g)
          .map((i) => {
            const editable = i.what !== "peaks";
            const step = i.what === "crack_width_limit" ? 0.05 : i.unit === "m" ? 0.5 : 50;
            return `<tr><td><input type="checkbox" data-tick="${i.id}" ${ticked.has(i.id) ? "checked" : ""}></td>
              <td>${esc(i.label)}</td>
              <td>${editable ? `<input type="number" step="${step}" data-val="${i.id}" value="${valueOf(i)}" style="width:6em"> ${i.unit || "mm"}` : ""}</td>
              <td class="hint" style="white-space:normal">${esc(i.note || "")}</td></tr>`;
          })
          .join("");
        return `<tr><th colspan="4" style="padding-top:12px">${g ? esc(g) : "All elements"}</th></tr>${rows}`;
      })
      .join("");
    const cols = data.variants;
    const base = cols[0];
    const saving = (v, c) => (c.base || v == null ? "–" : v === 0 ? "0" : `<span class="${v > 0 ? "flag-ok" : "flag-bad"}">${v > 0 ? "saves " : "costs "}${fmt(Math.abs(v))}</span>`);
    const safeBetter = (c) => c.complete && base.complete && c.unsafe.length <= base.unsafe.length;
    const best = cols.filter((c) => !c.base && safeBetter(c) && c.saving_per_m > 0).sort((a, b) => b.saving_per_m - a.saving_per_m)[0];
    const results = cols
      .map((c) => {
        const tag = c === best ? ' <span class="chip small-chip trial-best">best saving, no less safe</span>' : "";
        if (!c.complete)
          return `<tr class="trial-idle"><td>${esc(c.label)}</td><td colspan="${data.berth_length_m ? 7 : 6}" class="status">${c.missing} element design${c.missing === 1 ? "" : "s"} to run</td></tr>`;
        return `<tr class="${c === best ? "trial-best-row" : ""}"><td style="white-space:normal;min-width:16em">${esc(c.label)}${tag}</td>
          <td><strong>${money(c.cost_per_m)}</strong></td><td>${saving(c.saving_per_m, c)}</td>
          ${data.berth_length_m ? `<td>${saving(c.saving, c)}</td>` : ""}
          <td>${fmt(c.concrete_m3_per_m, 2)}</td><td>${fmt(c.rebar_t_per_m, 3)}</td>
          <td>${c.safe_count} of ${c.safe_count + c.unsafe.length}${c.unsafe.length ? `<div class="hint flag-bad" style="white-space:normal">Not safe: ${esc(c.unsafe.join(", "))}</div>` : ""}</td>
          <td>${c.base ? "" : `<button class="small quiet" data-unqueue="${esc(c.key)}" title="Take this off the list">×</button>`}</td></tr>`;
      })
      .join("");
    const costed = new Set(cols.map((c) => changeKey(c.variant || {})));
    const waiting = queue.filter((q) => !costed.has(changeKey(q)));
    const detail = cols.every((c) => c.complete)
      ? `<details style="margin-top:8px"><summary>Each element: utilisation · cost per m</summary><div class="scroll"><table class="cost trials">
          <tr><th></th>${cols.map((c) => `<th style="white-space:normal;min-width:9em">${esc(c.label)}</th>`).join("")}</tr>
          ${data.elements
            .map((n) => `<tr><td>${esc(n)}</td>${cols
              .map((c) => {
                const e = c.elements[n] || {};
                return e.state !== "done" ? `<td class="status">${esc(e.state || "–")}</td>` : `<td><span class="${e.passed ? "flag-ok" : "flag-bad"}">${fmt(e.utilisation, 2)}</span>${e.cost_per_m != null ? ` · ${fmt(e.cost_per_m)}` : ""}</td>`;
              })
              .join("")}</tr>`)
            .join("")}</table></div></details>`
      : "";
    out.innerHTML = `<div class="panel"><h2 style="margin-top:0">Ideas</h2>
        <div class="scroll"><table class="cost trials ve-ideas"><tr><th></th><th>Idea</th><th>Value</th><th>Note</th></tr>${ideaRows}</table></div>
        <div class="row" style="margin-top:10px;flex-wrap:wrap;gap:6px">
          <button class="small" id="ve-each" ${ticked.size ? "" : "disabled"}>Add each ticked idea on its own</button>
          <button class="small" id="ve-mix" ${ticked.size > 1 ? "" : "disabled"}>Add the ticked ideas as one mix</button>
          <span class="status">Ideas that change the same thing (two deck thicknesses) cannot be mixed.</span></div>
      </div>
      <div class="panel"><h2 style="margin-top:0">Costed against the section as set</h2>
        ${waiting.length ? `<p class="status">To design and cost: ${waiting.map((q) => `<span class="chip small-chip">${esc(q.label)} <button class="small quiet" data-unwait="${esc(changeKey(q))}">×</button></span>`).join(" ")}</p>` : ""}
        <div class="row"><button id="ve-run" ${running ? "disabled" : ""}>${running ? "Designing…" : "Design and cost"}</button>
          ${running ? '<button id="ve-stop" class="quiet">Stop</button>' : ""}<span class="status" id="ve-status">${running ? esc(running.text) : ""}</span>
          <span style="margin-left:auto">Export: ${["docx:Word", "pdf:PDF", "xlsx:Excel"].map((x) => { const [f, t] = x.split(":"); return `<a class="quiet-link" href="${secUrl()}/comparisons/report.${f}?what=ve">${t}</a>`; }).join(" ")}</span></div>
        <p class="status">Each line designs every element of the section with the change, without the bars you set by hand (so the lines differ only
          by the idea). Elements an idea does not touch are designed once and shared, so a new idea costs little more than the elements it changes.</p>
        <div class="scroll"><table class="cost trials"><tr><th>What</th><th>Cost per m (${esc(cur)}/m)</th><th>Against as set, per m</th>
          ${data.berth_length_m ? `<th>Whole ${fmt(data.berth_length_m)} m berth</th>` : ""}<th>Concrete m³/m</th><th>Reinforcement t/m</th><th>Safe elements</th><th></th></tr>
          ${results}</table></div>
        ${detail}
        ${!data.berth_length_m ? '<p class="status">No berth length on the Costing tab: costs are per metre over the length the model covers.</p>' : ""}
      </div>`;

    out.querySelectorAll("[data-tick]").forEach((b) => {
      b.onchange = () => {
        const idea = byId()[b.dataset.tick];
        if (b.checked) {
          for (const other of data.ideas) if (other.group === idea.group) ticked.delete(other.id);
          ticked.add(idea.id);
        } else ticked.delete(idea.id);
        draw();
      };
    });
    out.querySelectorAll("[data-val]").forEach((inp) => {
      inp.onchange = () => {
        values[inp.dataset.val] = inp.value === "" ? null : Number(inp.value);
      };
    });
    const picked = () => data.ideas.filter((i) => ticked.has(i.id));
    out.querySelector("#ve-each").onclick = () => {
      for (const i of picked()) enqueue({ ...changeOf(i), ideas: [i.id], label: shortLabel(i) });
      ticked.clear();
      draw();
    };
    out.querySelector("#ve-mix").onclick = () => {
      const list = picked();
      enqueue({ ...merge(list), ideas: list.map((i) => i.id), label: `Mix: ${list.map(shortLabel).join(" + ")}` });
      ticked.clear();
      draw();
    };
    out.querySelectorAll("[data-unwait]").forEach((b) => {
      b.onclick = () => {
        queue = queue.filter((q) => changeKey(q) !== b.dataset.unwait);
        draw();
      };
    });
    out.querySelectorAll("[data-unqueue]").forEach((b) => {
      b.onclick = () => {
        // Off the list here; the saved list follows at the next Design and cost.
        const col = cols.find((c) => c.key === b.dataset.unqueue);
        queue = queue.filter((q) => changeKey(q) !== changeKey(col.variant));
        data.variants = data.variants.filter((c) => c !== col);
        draw();
      };
    });
    out.querySelector("#ve-run").onclick = () => !running && run();
    const stop = out.querySelector("#ve-stop");
    if (stop) stop.onclick = () => (running.stopped = true);
  };

  const say = (text) => {
    if (running) running.text = text;
    const s = out.querySelector("#ve-status");
    if (s) s.textContent = text;
  };

  const run = async () => {
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
      for (;;) {
        const res = await again(() =>
          api(`${secUrl()}/value-engineering`, { method: "POST", body: JSON.stringify({ variants: queue, budget_s: 3 }) }),
        );
        data = res;
        queue = fromServer();
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

  draw();
}
