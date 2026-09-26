// Moved piles: what a pile row (or single piles) moved in plan does to the piles, beams and slab,
// without a new Plaxis run. Triton works out the change in pile loads and deck actions on a grillage of
// the deck on pile springs matched to the Plaxis pile loads, adds it to the Plaxis results, checks every
// element with the bars as designed, and designs afresh the ones that no longer pass. Nothing here
// changes the design. app.js passes its helpers in: api, again, esc, fmt, secUrl, ROOT, project(),
// sectionId(), designHash.

const STATE = {
  passes: ["flag-ok", "Passes"],
  "fails now": ["flag-bad", "Fails now"],
  "worse than before": ["flag-bad", "Worse than before"],
  "failed before too": ["flag-bad", "Failed before too"],
  "to do": ["status", "To check"],
  "not checked": ["status", "Not designed"],
};

export async function renderMovedPiles(host, h) {
  const { api, again, esc, fmt, secUrl, ROOT } = h;
  host.innerHTML = `<p class="sub">Move a row of piles, or single piles, and see whether the piles, beams and slab are still safe, without a new Plaxis run.
      Triton adds the change the move makes to the Plaxis results and checks every element with the bars it has now.
      Nothing here changes your design.</p>
    <div id="mv-out"></div>`;
  const out = host.querySelector("#mv-out");
  let data;
  let picked = null; // scenario id shown
  let draft = null; // the scenario being edited
  let running = null;

  const load = async () => {
    try {
      data = h.tabData ? await h.tabData("moved-piles", { box: out, title: "Loading the moved piles", keep: false }) : await api(`${secUrl()}/moved-piles`);
    } catch (e) {
      out.innerHTML = `<p class="status">${esc(e.message)}</p>`;
      return false;
    }
    return true;
  };
  if (!(await load())) return;

  const newScenario = () => {
    const first = Object.keys(data.piles)[0];
    return { id: Math.random().toString(16).slice(2, 10), name: "Moved piles", moves: first ? [{ element: first, x: null, y: null, dx: 0, dy: 1.4 }] : [], stiffness_factor: 1, grid: 0.5 };
  };
  const entryOf = (id) => data.scenarios.find((s) => s.scenario.id === id);
  const choose = (id) => {
    picked = id;
    const e = entryOf(id);
    draft = e ? structuredClone(e.scenario) : newScenario();
  };
  choose(data.scenarios[0]?.scenario.id ?? null);

  const u = (v) => (v == null ? "–" : fmt(v, 3));
  const flag = (v) => (v == null ? "–" : `<span class="${v > 1 + 1e-9 ? "flag-bad" : "flag-ok"}">${fmt(v, 3)}</span>`);
  const signed = (v, d = 0) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${fmt(Math.abs(v), d)}`);
  const where = (x, y) => `X ${fmt(x, 2)}, Y ${fmt(y, 2)}`;

  // Plan of the deck with every pile where it was and where it goes, coloured by its change in load.
  const plan = (entry) => {
    const ch = entry?.change;
    const piles = ch?.piles || Object.entries(data.piles).flatMap(([el, pts]) => pts.map(([x, y]) => ({ element: el, x, y, new_x: x, new_y: y, moved: false, kind: "pile" })));
    if (!piles.length) return "";
    const xs = piles.flatMap((p) => [p.x, p.new_x]).concat(ch?.deck?.x || []);
    const ys = piles.flatMap((p) => [p.y, p.new_y]).concat(ch?.deck?.y || []);
    const x0 = Math.min(...xs) - 1.5, x1 = Math.max(...xs) + 1.5, y0 = Math.min(...ys) - 1.5, y1 = Math.max(...ys) + 1.5;
    const s = Math.min(720 / Math.max(x1 - x0, 1), 420 / Math.max(y1 - y0, 1)), W = (x1 - x0) * s, H = (y1 - y0) * s;
    const X = (x) => (x - x0) * s, Y = (y) => H - (y - y0) * s;
    const deck = ch?.deck ? `<rect x="${X(ch.deck.x[0])}" y="${Y(ch.deck.y[1])}" width="${(ch.deck.x[1] - ch.deck.x[0]) * s}" height="${(ch.deck.y[1] - ch.deck.y[0]) * s}" fill="var(--miss-bg)" stroke="var(--line)"/>` : "";
    const r = Math.max(3, 0.6 * s);
    const tone = (p) => {
      if (!ch) return "var(--muted)";
      const share = p.change_share ?? 0;
      if (share > data.rerun_share) return "var(--err)";
      if (share > 0.1) return "var(--warn)";
      return "var(--muted)";
    };
    const marks = piles
      .map((p) => {
        const tip = `${p.element} at ${where(p.x, p.y)}${p.moved ? ` moved to ${where(p.new_x, p.new_y)}` : ""}${p.largest_change_kN != null ? `: head load ${signed(p.largest_change_kN)} kN (${p.change_combination}, ${Math.round(100 * p.change_share)}% of its largest)` : ""}`;
        const ghost = p.moved ? `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${r}" fill="none" stroke="var(--muted)" stroke-dasharray="3 2"/>
          <line x1="${X(p.x)}" y1="${Y(p.y)}" x2="${X(p.new_x)}" y2="${Y(p.new_y)}" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#mv-arrow)"/>` : "";
        const shape = p.kind === "combi" ? `<rect x="${X(p.new_x) - r}" y="${Y(p.new_y) - r}" width="${2 * r}" height="${2 * r}"` : `<circle cx="${X(p.new_x)}" cy="${Y(p.new_y)}" r="${r}"`;
        return `<g><title>${esc(tip)}</title>${ghost}${shape} fill="${p.moved ? "var(--accent)" : tone(p)}" stroke="${tone(p)}" stroke-width="2"/></g>`;
      })
      .join("");
    return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px" role="img" aria-label="Plan of the piles, moved ones in blue">
      <defs><marker id="mv-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker></defs>
      ${deck}${marks}</svg></div>
      <p class="status">Plan, X across the quay (to the right), Y along it (up). Blue: moved piles (dashed where they were). ${ch ? `Red: a pile whose head load changes by more than ${Math.round(100 * data.rerun_share)}% of its largest; amber: more than 10%. Hover a pile for its change.` : ""}</p>`;
  };

  const editor = () => {
    const els = Object.keys(data.piles);
    if (!els.length) return '<p class="status">This section has no pile results in its workbook to move.</p>';
    const rows = draft.moves
      .map((m, i) => {
        const pts = data.piles[m.element] || [];
        const which = m.x == null ? "" : `${m.x},${m.y}`;
        return `<tr>
          <td><select data-m="${i}" data-f="element" style="min-width:7em">${els.map((e) => `<option ${e === m.element ? "selected" : ""}>${esc(e)}</option>`).join("")}</select></td>
          <td><select data-m="${i}" data-f="which" style="min-width:14em"><option value="">The whole row (${pts.length} piles)</option>${pts
            .map(([x, y]) => `<option value="${x},${y}" ${which === `${x},${y}` ? "selected" : ""}>Pile at ${where(x, y)}</option>`)
            .join("")}</select></td>
          <td><input type="number" step="0.1" data-m="${i}" data-f="dx" value="${m.dx}" style="width:6em"> m</td>
          <td><input type="number" step="0.1" data-m="${i}" data-f="dy" value="${m.dy}" style="width:6em"> m</td>
          <td><button class="small quiet" data-del-move="${i}" title="Take this move off">×</button></td></tr>`;
      })
      .join("");
    return `<div class="scroll"><table class="cost trials"><tr><th>Piles</th><th>Which</th><th>Move by dX</th><th>and dY</th><th></th></tr>${rows}</table></div>
      <div class="row" style="margin-top:8px"><button class="small quiet" id="mv-add">Add a move</button></div>
      <details style="margin-top:8px"><summary>Model</summary>
        <div class="row" style="margin-top:6px"><label>Name <input id="mv-name" value="${esc(draft.name)}" style="width:14em"></label>
        <label>Pile head stiffness × <input type="number" step="0.1" min="0.1" id="mv-k" value="${draft.stiffness_factor}" style="width:5em"></label>
        <label>Grillage spacing <input type="number" step="0.25" min="0.25" max="2" id="mv-grid" value="${draft.grid}" style="width:5em"> m</label></div>
        <p class="status">Each pile is a spring at its head of E·A / (0.5 L), L its length in the workbook, times this factor: lower it for piles that
        settle more, raise it for piles on rock. The stiffer the piles against the deck, the more a move changes their loads.</p></details>`;
  };

  // Why an element that failed before is worse now: the parts that passed and fail with the piles moved.
  const newly = (r) => {
    const strips = (r.strips || []).filter((x) => x.before != null && x.before <= 1 && !x.passed).length;
    const heads = (r.punching || []).filter((q) => q.passed_before && !q.passed && !q.under_beam).length;
    const parts = [strips && `${strips} strip${strips > 1 ? "s" : ""}`, heads && `${heads} pile head${heads > 1 ? "s" : ""}`].filter(Boolean);
    return parts.length ? `${parts.join(" and ")} that passed ${strips + heads > 1 ? "now fail" : "now fails"}` : "its utilisation goes up";
  };

  const elementsTable = (entry) => {
    const rows = entry.elements
      .map((r) => {
        const [cls, text] = STATE[r.state] || ["status", r.state];
        const b = r.before, c = r.check, d = r.redesign;
        return `<tr><td>${esc(r.element)}</td>
          <td>${b ? `${flag(b.utilisation)}<div class="hint">${esc(b.bars || "")}</div>` : '<span class="status">not designed</span>'}</td>
          <td>${c ? flag(c.utilisation) : "–"}</td>
          <td><span class="${cls}">${text}</span>${r.state === "worse than before" ? `<div class="hint">${newly(r)}</div>` : ""}</td>
          <td>${d ? `${flag(d.utilisation)}<div class="hint">${esc(d.bars || "")}</div>` : r.state === "fails now" || r.state === "worse than before" ? '<span class="status">to design</span>' : ""}</td></tr>`;
      })
      .join("");
    return `<div class="scroll"><table class="cost trials"><tr><th>Element</th><th>As designed</th><th>Piles moved, same bars</th><th></th><th>Designed afresh</th></tr>${rows}</table></div>`;
  };

  const pileTable = (ch) => {
    const list = ch.piles.filter((p) => p.moved || p.change_share > 0.05).sort((a, b) => b.change_share - a.change_share);
    if (!list.length) return "";
    return `<h3>Pile head loads</h3><div class="scroll"><table class="cost trials"><tr><th>Pile</th><th>Where</th><th>Largest Plaxis load (kN)</th><th>Change (kN)</th><th>In</th><th>Share</th></tr>
      ${list
        .map((p) => `<tr><td>${esc(p.element)}</td><td>${where(p.x, p.y)}${p.moved ? ` → ${where(p.new_x, p.new_y)}` : ""}</td><td>${fmt(p.largest_N_kN)}</td>
          <td>${signed(p.largest_change_kN)}</td><td>${esc(p.change_combination)}</td>
          <td><span class="${p.change_share > data.rerun_share ? "flag-bad" : ""}">${Math.round(100 * p.change_share)}%</span></td></tr>`)
        .join("")}</table></div>
      <p class="status">Compression +. The change is added to the pile's axial force over its whole length; its bending moments stay as Plaxis gives them.
      Piles not listed change by 5% or less.</p>`;
  };

  const slabTables = (r) => {
    const strips = r.strips || [];
    const punch = (r.punching || []).filter((q) => q.moved || (q.before != null && q.after != null && Math.abs(q.after - q.before) >= 0.01) || (q.passed_before && !q.passed));
    let html = "";
    if (strips.length)
      html += `<h3>${esc(r.element)}: strips and zones that change</h3><div class="scroll"><table class="cost trials"><tr><th>Bars</th><th>Where</th><th>Bars as designed</th><th>M before → after (kNm/m)</th><th>Before</th><th>Piles moved</th></tr>
        ${strips
          .map((s) => `<tr><td>${esc((s.layer || "").replace("_", " "))}</td><td>${s.station ? `${fmt(s.station[0], 2)} to ${fmt(s.station[1], 2)} m, ${esc(s.strip || "")}` : esc(s.key)}</td>
            <td style="white-space:normal">${esc(s.bars || "")}</td><td>${s.M_before == null ? "–" : fmt(s.M_before)} → ${s.M_after == null ? "–" : fmt(s.M_after)}</td>
            <td>${u(s.before)}</td><td>${flag(s.after)}</td></tr>`)
          .join("")}</table></div><p class="status">Utilisation of each strip: bending (ULS) or crack width (QP), whichever is higher.</p>`;
    if (punch.length)
      html += `<h3>${esc(r.element)}: punching at the pile heads</h3><div class="scroll"><table class="cost trials"><tr><th>Pile</th><th>Where</th><th>V before → after (kN)</th><th>Before</th><th>Piles moved</th><th>Links</th></tr>
        ${punch
          .map((q) => `<tr><td>${esc(q.pile)}</td><td>${where(q.x, q.y)}${q.moved ? ` → ${where(q.new_x, q.new_y)}` : ""}</td>
            <td>${q.V_before == null ? "–" : fmt(q.V_before)} → ${q.V_after == null ? "under a beam" : fmt(q.V_after)}</td>
            <td>${u(q.before)}</td><td>${q.after == null ? "–" : `<span class="${q.passed ? "flag-ok" : "flag-bad"}">${fmt(q.after, 3)}</span>`}</td>
            <td>${q.links_after ? (q.links_before ? "needed, as before" : '<span class="flag-bad">needed now</span>') : q.links_before ? "no longer needed" : "none"}</td></tr>`)
          .join("")}</table></div><p class="status">Punching utilisation is vEd over vRd,c; above 1 the head needs links, which can carry up to 1.5 (kmax).</p>`;
    return html;
  };

  const results = (entry) => {
    if (!entry) return "";
    if (entry.state === "problems") return `<div class="panel warning"><ul>${entry.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`;
    if (entry.state === "not run") return '<p class="status">Not checked yet: press Check.</p>';
    const ch = entry.change || {};
    const stale = entry.state === "out of date" ? '<p class="warning" style="padding:6px 10px;border-radius:8px">The design, the workbook or the scenario changed since this check: press Check again.</p>' : "";
    const part = entry.state === "part done" ? `<p class="status">${entry.left} step(s) still to do: press Check to carry on.</p>` : "";
    const verdict = entry.fails_now.length
      ? `<p><strong class="flag-bad">Not safe with the bars as designed:</strong> ${esc(entry.fails_now.join(", "))} ${entry.fails_now.length === 1 ? "fails" : "fail"} with the piles moved.</p>`
      : entry.elements.every((r) => r.check)
        ? `<p><strong class="flag-ok">Safe with the bars as designed</strong>: no element that passed fails with the piles moved.${entry.failed_before.length ? ` ${esc(entry.failed_before.join(", "))} failed before the move too.` : ""}</p>`
        : "";
    const warns = (ch.warnings || []).map((w) => `<li>${esc(w)}</li>`).join("");
    const big = ch.largest_deck_change || {};
    const notes = [
      ...(ch.notes || []),
      ...(ch.sign_notes || []),
      `Deck grillage at ${fmt(ch.grid_m, 2)} m spacing (${ch.grid_nodes} nodes), matched to the Plaxis pile head loads of each combination (${(ch.combinations || []).join(", ")}).`,
      `Largest change in the deck: Mx ${fmt(big.Mx_kNm_per_m)}, My ${fmt(big.My_kNm_per_m)}, Mxy ${fmt(big.Mxy_kNm_per_m)} kNm/m; Qx ${fmt(big.Qx_kN_per_m)}, Qy ${fmt(big.Qy_kN_per_m)} kN/m.`,
      ...(entry.loose?.length ? [`Bars could not be kept for ${entry.loose.join(", ")}: chosen afresh in the check.`] : []),
      "Bars along the berth that the slab design places in zones over the whole deck are kept as designed; the check places no new zones.",
    ];
    return `${stale}${part}${verdict}
      ${warns ? `<div class="warning" style="padding:6px 12px;border-radius:8px"><ul style="margin:4px 0;padding-left:18px">${warns}</ul></div>` : ""}
      ${elementsTable(entry)}
      ${pileTable(ch)}
      ${entry.elements.map(slabTables).join("")}
      <details style="margin-top:10px"><summary>How this was worked out</summary><ul>${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
        <p class="status">An approximation: the grillage carries vertical load only and the soil round the piles stays as Plaxis had it.
        When a pile's load changes a lot, or a row moves across the quay, confirm with a new Plaxis run.</p></details>
      <p class="status">Checked ${esc(entry.run_at || "")} against the design of ${esc(data.design_run_at || "")}.</p>`;
  };

  const say = (text) => {
    if (running) running.text = text;
    const s = out.querySelector("#mv-status");
    if (s) s.textContent = text;
  };

  const draw = () => {
    const entry = entryOf(picked);
    const tabs = data.scenarios
      .map((e) => `<button class="small ${e.scenario.id === picked ? "" : "quiet"}" data-pick="${esc(e.scenario.id)}">${esc(e.scenario.name)}</button>`)
      .join(" ");
    out.innerHTML = `${data.designed ? "" : '<p class="warning" style="padding:6px 10px;border-radius:8px">Design the section on the Design tab first: a scenario checks the bars that design has.</p>'}
      <div class="row" style="margin-bottom:8px">${tabs}<button class="small quiet" id="mv-new">New scenario</button></div>
      <div class="panel"><h2 style="margin-top:0">${esc(draft.name)}</h2>
        ${editor()}
        <div class="row" style="margin-top:10px"><button id="mv-run" ${running || !draft.moves.length ? "disabled" : ""}>${running ? "Checking…" : "Save and check"}</button>
          ${running ? '<button id="mv-stop" class="quiet">Stop</button>' : ""}
          ${entry ? '<button class="quiet danger" id="mv-del">Delete scenario</button>' : ""}
          <span class="status" id="mv-status">${running ? esc(running.text) : ""}</span></div>
        ${plan(entry)}
      </div>
      <div class="panel" style="margin-top:12px"><h2 style="margin-top:0">Result</h2>${entry ? results(entry) : '<p class="status">Not saved yet.</p>'}</div>`;

    out.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => {
      choose(b.dataset.pick);
      draw();
    }));
    out.querySelector("#mv-new").onclick = () => {
      picked = null;
      draft = newScenario();
      draft.name = `Moved piles ${data.scenarios.length + 1}`;
      draw();
    };
    out.querySelectorAll("[data-m]").forEach((inp) => {
      inp.onchange = () => {
        const m = draft.moves[+inp.dataset.m];
        const f = inp.dataset.f;
        if (f === "element") {
          m.element = inp.value;
          m.x = m.y = null;
          draw();
        } else if (f === "which") {
          if (!inp.value) m.x = m.y = null;
          else [m.x, m.y] = inp.value.split(",").map(Number);
        } else m[f] = inp.value === "" ? 0 : Number(inp.value);
      };
    });
    out.querySelectorAll("[data-del-move]").forEach((b) => (b.onclick = () => {
      draft.moves.splice(+b.dataset.delMove, 1);
      draw();
    }));
    const add = out.querySelector("#mv-add");
    if (add) add.onclick = () => {
      const last = draft.moves[draft.moves.length - 1];
      draft.moves.push({ element: last?.element || Object.keys(data.piles)[0], x: null, y: null, dx: 0, dy: 0 });
      draw();
    };
    const bind = (id, f, num) => {
      const el = out.querySelector(id);
      if (el) el.onchange = () => (draft[f] = num ? Number(el.value) : el.value);
    };
    bind("#mv-name", "name", false);
    bind("#mv-k", "stiffness_factor", true);
    bind("#mv-grid", "grid", true);
    out.querySelector("#mv-run").onclick = () => !running && run();
    const stop = out.querySelector("#mv-stop");
    if (stop) stop.onclick = () => (running.stopped = true);
    const del = out.querySelector("#mv-del");
    if (del) del.onclick = async () => {
      if (!confirm(`Delete "${draft.name}"?`)) return;
      data = await api(`${secUrl()}/moved-piles/${draft.id}`, { method: "DELETE" });
      choose(data.scenarios[0]?.scenario.id ?? null);
      draw();
    };
  };

  const run = async () => {
    running = { text: "Saving…", stopped: false };
    draw();
    const key = `moved-${h.project().id}-${h.sectionId()}`;
    const poll = setInterval(async () => {
      try {
        const p = await api(`${ROOT}/api/progress/${key}`);
        if (p.step) say(`${p.step}…`);
      } catch {
        /* between requests */
      }
    }, 1500);
    try {
      data = await api(`${secUrl()}/moved-piles/${draft.id}`, { method: "PUT", body: JSON.stringify(draft) });
      picked = draft.id;
      for (;;) {
        const res = await again(() => api(`${secUrl()}/moved-piles/${draft.id}/run`, { method: "POST", body: JSON.stringify({ budget_s: 3 }) }));
        data = res;
        if (!res.left || res.problems?.length || running.stopped) break;
        running.text = `${res.left} step(s) to go…`;
        draw();
      }
      running = null;
      choose(picked);
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
