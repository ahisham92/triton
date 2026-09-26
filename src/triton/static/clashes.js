// Clashes: the pile bars where they run up into the slab or the beam over them, against that
// element's bars, links and punching links. Each pile head is drawn in plan, in section both ways and
// in 3D; each way out (turn the cage, move bars, crank, cut and add trimmers) is designed again, and
// "what if" takes chosen bars out of a connection and checks it again. Nothing here changes the
// design. app.js passes its helpers in: api, again, esc, fmt, secUrl.

const COL = {
  pile: "#7a4696", bottom: "#7b818c", top: "#2a5fae", punch: "#008c96", link: "#9aa0aa",
  trim: "#2e8b4a", clash: "#c62828", gone: "#b8bcc4", pick: "#e08a00", concrete: "rgba(160,160,150,0.13)",
};
const SOL_ORDER = ["set_out", "rotate", "shift", "rotate_shift", "crank", "cut_trim", "cut"];

export async function renderClashes(host, h) {
  const { api, again, esc, fmt, secUrl } = h;
  // The page's loader: kept while nothing changes, with a bar and the time spent while it works.
  const get = (path, box, title, progress = false) =>
    h.tabData ? h.tabData(path, { box, title, progress }) : again(() => api(`${secUrl()}/${path}`));
  host.innerHTML = `<p class="sub">The pile bars where they run up into the slab or beam over them, against that element's bars, links and
    punching links. Each way out is designed again. <strong>What if</strong> takes bars out of a connection and checks it again.
    Nothing on this tab changes the design.</p><div id="cl-out"></div>`;
  const out = host.querySelector("#cl-out");
  let data;
  const load = async () => {
    try {
      data = await get("clashes", out, "Finding the clashes", true);
    } catch (e) {
      out.innerHTML = `<p class="status">${esc(e.message)}</p>`;
      return false;
    }
    return true;
  };
  if (!(await load())) return;

  const st = { group: null, index: null, head: null, view: "before", picking: false, bars: new Set(), pileBars: new Set(), check: null, dim: "plan" };
  const f1 = (v) => fmt(v, 1);
  const f3 = (v) => (v == null ? "–" : fmt(v, 3));
  const yes = (ok) => `<span class="${ok ? "flag-ok" : "flag-bad"}">${ok ? "passes" : "fails"}</span>`;
  const solTitle = (id) => ({ set_out: "Set the bars out through the cage", rotate: "Turn the cage", shift: "Move the bars", rotate_shift: "Turn and move", crank: "Crank the pile bars", cut_trim: "Cut and add trimmers", cut: "Cut the bars" })[id] || id;
  const calcUrl = (fmtx, q) => `${secUrl()}/clashes/calc.${fmtx}?${new URLSearchParams(q)}`;

  const settingsHtml = () => {
    const s = data.settings;
    return `<details class="panel" data-free><summary>How clashes are found</summary>
      <div class="row"><label>A clash is <select id="cl-rule">
        <option value="touch" ${s.rule === "touch" ? "selected" : ""}>bars that would touch (closer than the fixing tolerance)</option>
        <option value="ec2" ${s.rule === "ec2" ? "selected" : ""}>bars closer than EN 1992-1-1 8.2(2) allows</option></select></label>
      <label>Fixing tolerance <input id="cl-tol" type="number" min="0" max="50" step="1" value="${s.fixing_tolerance}" style="width:5em"> mm</label>
      <label>Plaxis plates at the element's <select id="cl-plate"><option value="mid" ${s.plate_level === "mid" ? "selected" : ""}>mid-depth</option>
        <option value="top" ${s.plate_level === "top" ? "selected" : ""}>top</option></select></label>
      <label>Pile bars into a beam <select id="cl-beam"><option value="straight" ${s.beam_bars !== "l" ? "selected" : ""}>straight, under the top bars (as drawing SC-401)</option>
        <option value="l" ${s.beam_bars === "l" ? "selected" : ""}>L, outwards under the top bars</option></select></label>
      <label>Combi bars welded to the tube: fillet leg <input id="cl-leg" type="number" min="1" step="1" value="${s.weld?.leg ?? 16}" style="width:4em"> mm,
        filler fu <input id="cl-fu" type="number" min="1" step="0.1" value="${s.weld?.filler_fu ?? 482.6}" style="width:5em"> MPa (E70XX)</label>
      <label>Front beam clear height above the highest water <input id="cl-water" type="number" min="0" step="0.1" value="${s.water_margin ?? 0.5}" style="width:4em"> m</label>
      <button id="cl-save">Find again</button></div>
      <ul class="status">${data.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></details>`;
  };

  const groupsHtml = () => `<div class="panel"><h2>Connections</h2>
    ${data.standard?.length ? `<p class="status">Not checked: ${esc(data.standard.join(", "))} ${data.standard.length === 1 ? "was" : "were"} designed in Standard mode, which has no bar layout. Design ${data.standard.length === 1 ? "it" : "them"} in Detailed mode to check clashes.</p>` : ""}
    <p class="status">${data.heads_checked} pile heads checked, ${data.heads_clear} clear. Pick a row to see its heads, drawings and solutions.</p>
    <div class="scroll"><table><thead><tr><th>Pile into</th><th>Connection</th><th class="num">Heads</th><th class="num">With clashes</th>
      <th class="num">Bars overlapping</th><th class="num">Too close</th><th class="num">Punching links</th><th>Recommended</th><th>Solution to use</th></tr></thead><tbody>
    ${data.groups.map((g) => {
      const rec = g.summary.recommended;
      const tally = g.summary.solutions.find((s) => s.id === rec);
      return `<tr class="link ${g.key === st.group ? "on" : ""}" data-group="${esc(g.key)}"><td><strong>${esc(g.pile)}</strong> into ${esc(g.host)}</td>
        <td>${esc(g.connection.shape)}</td><td class="num">${g.count.heads}</td><td class="num">${g.count.with_clashes}</td>
        <td class="num">${g.count.clash}</td><td class="num">${g.count.pairs - g.count.clash}</td><td class="num">${g.count.punch_pairs}</td>
        <td>${rec ? `${esc(solTitle(rec))}${tally ? ` <span class="status">(${tally.passes_at}/${tally.heads} pass)</span>` : ""}` : "–"}</td>
        <td><select data-choice="${esc(g.key)}" data-free><option value="">As recommended</option>${SOL_ORDER.filter((id) => g.summary.solutions.some((s) => s.id === id))
          .map((id) => `<option value="${id}" ${g.choice === id ? "selected" : ""}>${esc(solTitle(id))}</option>`).join("")}</select></td></tr>`;
    }).join("")}</tbody></table></div></div>`;

  const FLAG = { ok: "flag-ok", warning: "flag-warn", critical: "flag-bad" };
  const waterHtml = () => !data.water?.length ? "" : `<div class="panel"><h2>Front beam and the water</h2>
    <p class="status">Water levels from the section's site settings (3D tab). A soffit in the tidal zone is cast between tides; below the lowest level it needs a cofferdam or a precast shell.</p>
    <div class="scroll"><table><thead><tr><th>Underside</th><th class="num">Level (m)</th><th>Where</th>${data.water[0].levels.map((l) => `<th class="num">${esc(l.name)} ${l.level_m}</th>`).join("")}</tr></thead><tbody>
    ${data.water.map((w) => `<tr><td>${esc(w.what)}</td><td class="num">${w.level_m.toFixed(2)}</td><td><span class="${FLAG[w.severity]}">${esc(w.status)}</span></td>
      ${w.levels.map((l) => `<td class="num">${l.above_m >= 0 ? "+" : ""}${l.above_m.toFixed(2)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <ul class="status">${data.water.filter((w) => w.severity !== "ok").map((w) => `<li>${esc(w.text)}</li>`).join("")}</ul></div>`;

  const whatifsHtml = () => !data.whatifs.length ? "" : `<div class="panel"><h2>What ifs kept</h2><div class="scroll"><table><thead><tr><th>Connection</th><th>Taken out</th><th>Note</th><th>Result</th><th>Steel</th><th>Calculation</th><th></th></tr></thead><tbody>
    ${data.whatifs.map((w) => `<tr><td>${esc(w.group.replace("|", " into "))}, head ${w.head + 1}</td><td>${w.bars.length} bar(s)${w.pile_bars.length ? `, ${w.pile_bars.length} pile bar(s)` : ""}</td>
      <td>${esc(w.note || "")}</td><td>${w.error ? `<span class="flag-bad">${esc(w.error)}</span>` : yes(w.passes)}</td><td class="num">${w.delta_kg == null ? "–" : `${f1(w.delta_kg)} kg`}</td>
      <td><a href="${calcUrl("docx", { whatif: w.id })}">Word</a> · <a href="${calcUrl("pdf", { whatif: w.id })}">PDF</a></td>
      <td><button class="quiet" data-open-wi="${esc(w.id)}" data-free>Open</button> <button class="danger" data-drop-wi="${esc(w.id)}" data-free>Remove</button></td></tr>`).join("")}
    </tbody></table></div></div>`;

  const draw = () => {
    out.innerHTML = settingsHtml() + waterHtml() + groupsHtml() + whatifsHtml() + `<div id="cl-head"></div>`;
    out.querySelector("#cl-save").onclick = async () => {
      const body = { rule: out.querySelector("#cl-rule").value, fixing_tolerance: Number(out.querySelector("#cl-tol").value), plate_level: out.querySelector("#cl-plate").value, beam_bars: out.querySelector("#cl-beam").value, water_margin: Number(out.querySelector("#cl-water").value),
        weld: { ...(data.settings.weld || {}), leg: Number(out.querySelector("#cl-leg").value), filler_fu: Number(out.querySelector("#cl-fu").value) } };
      out.querySelector("#cl-save").disabled = true;
      await api(`${secUrl()}/clashes/settings`, { method: "PUT", body: JSON.stringify(body) });
      st.head = null;
      if (await load()) draw();
    };
    out.querySelectorAll("tr[data-group]").forEach((tr) => (tr.onclick = (e) => {
      if (e.target.closest("select")) return;
      openGroup(tr.dataset.group);
    }));
    out.querySelectorAll("[data-choice]").forEach((s) => (s.onchange = async () => {
      const choices = Object.fromEntries(data.groups.map((g) => [g.key, g.choice]).filter(([, v]) => v));
      if (s.value) choices[s.dataset.choice] = s.value;
      else delete choices[s.dataset.choice];
      await api(`${secUrl()}/clashes/settings`, { method: "PUT", body: JSON.stringify({ choices }) });
      data.groups.find((g) => g.key === s.dataset.choice).choice = s.value || null;
    }));
    out.querySelectorAll("[data-drop-wi]").forEach((b) => (b.onclick = async () => {
      await api(`${secUrl()}/clashes/whatifs/${b.dataset.dropWi}`, { method: "DELETE" });
      data.whatifs = data.whatifs.filter((w) => w.id !== b.dataset.dropWi);
      draw();
    }));
    out.querySelectorAll("[data-open-wi]").forEach((b) => (b.onclick = async () => {
      const w = data.whatifs.find((x) => x.id === b.dataset.openWi);
      await openGroup(w.group, w.head);
      st.picking = true;
      st.bars = new Set(w.bars);
      st.pileBars = new Set(w.pile_bars);
      st.check = w;
      st.note = w.note;
      st.keptId = w.id;
      drawHead();
    }));
    if (st.head) drawHead();
  };

  async function openGroup(key, index = null) {
    const g = data.groups.find((x) => x.key === key);
    if (!g) return;
    st.group = key;
    st.index = index ?? g.worst;
    st.view = "before";
    st.picking = false;
    st.bars = new Set();
    st.pileBars = new Set();
    st.check = null;
    st.keptId = null;
    st.note = "";
    out.querySelectorAll("tr[data-group]").forEach((tr) => tr.classList.toggle("on", tr.dataset.group === key));
    const box = out.querySelector("#cl-head");
    try {
      st.head = await get(`clashes/head?${new URLSearchParams({ group: key, index: st.index })}`, box, "Loading the pile head");
    } catch (e) {
      box.innerHTML = `<p class="status">${esc(e.message)}</p>`;
      return;
    }
    drawHead();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // The bars as a solution (or the what-if) leaves them, in the head's local mm (u east, v north, z up from the soffit).
  function layout() {
    const e = st.head;
    const sc = e.scene;
    const sol = st.view === "snap" ? e.punching?.solution : (e.solutions || []).find((s) => s.id === st.view);
    const ch = (st.view === "snap" ? null : sol?.change) || {};
    const turn = ((ch.rotate_deg || 0) * Math.PI) / 180;
    const crank = ch.crank_mm || 0;
    const cut = new Set((ch.cut || []).map((j) => sc.hbars[j].id));
    const shift = ch.shift_mm || {};
    const pairs = sol ? sol.left_pairs || [] : sc.conflicts;
    const bad = { pile: new Set(), h: new Set(), l: new Set(), punch: new Set() };
    for (const [a, i, b, j] of pairs) {
      (a === "pile" ? bad.pile : bad.punch).add(i);
      if (b === "h") bad.h.add(j);
      else if (b === "l") bad.l.add(j);
    }
    const soffit = sc.host.soffit_m * 1e3;
    const hbars = sc.hbars.map((b, j) => ({ ...b, j, at: b.at + (shift[j] || 0), z: b.z_m * 1e3 - soffit, bad: bad.h.has(j),
      gone: cut.has(b.id) || st.bars.has(b.id), picked: st.bars.has(b.id) }));
    const linkShift = {};
    sc.hbars.forEach((b, j) => { if (b.link != null && shift[j]) linkShift[b.link] = shift[j]; });
    const legs = sc.legs.map((g, j) => {
      const ds = linkShift[g.link] || 0;
      return { ...g, u: g.u + (sc.host.along === "X" ? ds : 0), v: g.v + (sc.host.along === "Y" ? ds : 0), bad: bad.l.has(j) };
    });
    const pile = sc.pile_bars.map((b, i) => {
      const r = Math.hypot(b.u, b.v) - crank;
      const a = Math.atan2(b.v, b.u) + turn;
      return { ...b, u: r * Math.cos(a), v: r * Math.sin(a), a, bad: bad.pile.has(i), gone: st.pileBars.has(b.id), picked: st.pileBars.has(b.id),
        leg: (sc.pile.legs_m[b.row] || 0) * 1e3, top: b.top_m * 1e3 - soffit };
    });
    const after = st.view === "snap" ? e.punching.solution.legs_after : null;
    const punch = sc.punch.map((g, n) => ({ ...g, u: after ? after[n][0] : g.u, v: after ? after[n][1] : g.v, bad: bad.punch.has(n) }));
    const trim = (ch.trimmers || []).map((t) => {
      const c = t.along === "X" ? [sc.pile.x, sc.pile.y] : [sc.pile.y, sc.pile.x];
      return { ...t, at: (t.at - c[1]) * 1e3, lo: (t.lo - c[0]) * 1e3, hi: (t.hi - c[0]) * 1e3, z: t.z * 1e3 - soffit };
    });
    const r = sc.pile.diameter_mm / 2;
    const reach = Math.max(r + Math.max(0, ...sc.pile.legs_m) * 1e3, ...punch.map((g) => Math.hypot(g.u, g.v)), r) + 250;
    return { sc, hbars, legs, pile, punch, trim, reach, r, depth: sc.host.top_m * 1e3 - soffit, enters: sc.pile.enters_m * 1e3 - soffit,
      topMid: (sc.host.top_m * 1e3 - soffit) / 2 };
  }

  const barColor = (b, L) => (b.picked ? COL.pick : b.gone ? COL.gone : b.bad ? COL.clash : b.kind === "link leg" ? COL.link : b.z > L.topMid ? COL.top : COL.bottom);

  function planSvg(L) {
    const R = L.reach;
    const parts = [];
    const host = L.sc.host;
    if (host.kind === "beam" && host.centre_mm != null) {
      const half = host.width_mm / 2;
      parts.push(host.along === "X" ? `<rect x="${-R}" y="${host.centre_mm - half}" width="${2 * R}" height="${2 * half}" fill="${COL.concrete}"/>`
        : `<rect x="${host.centre_mm - half}" y="${-R}" width="${2 * half}" height="${2 * R}" fill="${COL.concrete}"/>`);
    } else parts.push(`<rect x="${-R}" y="${-R}" width="${2 * R}" height="${2 * R}" fill="${COL.concrete}"/>`);
    parts.push(`<circle cx="0" cy="0" r="${L.r}" fill="none" stroke="${COL.pile}" stroke-width="4" stroke-dasharray="20 12"/>`);
    const hb = L.hbars.filter((b) => b.kind !== "link leg" && Math.abs(b.at) <= R).sort((p, q) => (p.bad - q.bad) || (p.picked - q.picked) || p.z - q.z);
    for (const b of hb) {
      const lo = Math.max(b.lo ?? -R, -R);
      const hi = Math.min(b.hi ?? R, R);
      const [x1, y1, x2, y2] = b.along === "X" ? [lo, b.at, hi, b.at] : [b.at, lo, b.at, hi];
      parts.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${barColor(b, L)}" stroke-width="${b.phi}" ${b.gone && !b.picked ? 'stroke-dasharray="30 20" stroke-opacity="0.7"' : ""}
        data-bar="${b.id}"><title>${esc(b.group)} (${b.id})</title></line>`);
    }
    for (const t of L.trim) {
      const [x1, y1, x2, y2] = t.along === "X" ? [Math.max(t.lo, -R), t.at, Math.min(t.hi, R), t.at] : [t.at, Math.max(t.lo, -R), t.at, Math.min(t.hi, R)];
      parts.push(`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${COL.trim}" stroke-width="${t.phi}"><title>Trimmer Ø${t.phi} × ${t.length_m} m</title></line>`);
    }
    for (const g of L.legs) parts.push(`<circle cx="${g.u}" cy="${g.v}" r="${g.phi / 2 + 2}" fill="${g.bad ? COL.clash : COL.link}"><title>${esc(g.group)}</title></circle>`);
    for (const b of L.pile) {
      const col = b.picked ? COL.pick : b.gone ? COL.gone : b.bad ? COL.clash : COL.pile;
      if (b.leg > 0 && !b.gone) parts.push(`<line x1="${b.u}" y1="${b.v}" x2="${b.u + b.leg * Math.cos(b.a)}" y2="${b.v + b.leg * Math.sin(b.a)}" stroke="${col}" stroke-width="${b.phi * 0.8}" stroke-opacity="0.75"/>`);
      parts.push(`<circle cx="${b.u}" cy="${b.v}" r="${b.phi / 2 + 3}" fill="${b.gone && !b.picked ? "none" : col}" stroke="${col}" stroke-width="4" data-pile="${b.id}">
        <title>Pile bar ${b.id} (row ${b.row + 1}, Ø${b.phi})</title></circle>`);
    }
    for (const g of L.punch) parts.push(`<rect x="${g.u - g.phi / 2 - 3}" y="${g.v - g.phi / 2 - 3}" width="${g.phi + 6}" height="${g.phi + 6}" fill="${g.bad ? COL.clash : COL.punch}"><title>Punching leg, perimeter ${g.perimeter + 1}</title></rect>`);
    return `<svg class="cl-svg ${st.picking ? "picking" : ""}" viewBox="${-R} ${-R} ${2 * R} ${2 * R}" preserveAspectRatio="xMidYMid meet"><g transform="scale(1,-1)">${parts.join("")}</g>
      <text x="${-R + 20}" y="${-R + 60}" font-size="${R / 18}" fill="currentColor">N ↑ · E →</text></svg>`;
  }

  function sectionSvg(L, cut) {
    const R = L.reach;
    const top = L.depth;
    const below = 600;
    const H = top + below + 150;
    const r = L.r;
    const parts = [`<rect x="${-R}" y="0" width="${2 * R}" height="${top}" fill="${COL.concrete}" stroke="currentColor" stroke-width="4"/>`,
      `<rect x="${-r}" y="${-below}" width="${2 * r}" height="${below}" fill="${COL.concrete}"/>`,
      `<line x1="${-r}" y1="0" x2="${-r}" y2="${-below}" stroke="currentColor" stroke-width="4"/><line x1="${r}" y1="0" x2="${r}" y2="${-below}" stroke="currentColor" stroke-width="4"/>`];
    const other = cut === "X" ? "Y" : "X";
    for (const b of L.hbars) {
      if (b.kind === "link leg") continue;
      if (b.along === other) {
        if (Math.abs(b.at) <= R) parts.push(`<circle cx="${b.at}" cy="${b.z}" r="${b.phi / 2 + 2}" fill="${barColor(b, L)}" data-bar="${b.id}"><title>${esc(b.group)}</title></circle>`);
      } else if (Math.abs(b.at) < r + 80) {
        parts.push(`<line x1="${Math.max(b.lo ?? -R, -R)}" y1="${b.z}" x2="${Math.min(b.hi ?? R, R)}" y2="${b.z}" stroke="${barColor(b, L)}" stroke-width="${b.phi * 0.6}" data-bar="${b.id}"><title>${esc(b.group)}</title></line>`);
      }
    }
    for (const b of L.pile) {
      const across = cut === "X" ? b.v : b.u;
      if (Math.abs(across) > 0.35 * r) continue;
      const u = cut === "X" ? b.u : b.v;
      const col = b.picked ? COL.pick : b.gone ? COL.gone : b.bad ? COL.clash : COL.pile;
      const s = u >= 0 ? 1 : -1;
      parts.push(`<polyline points="${u},${-below} ${u},${b.top}${b.leg > 0 ? ` ${u + s * b.leg},${b.top}` : ""}" fill="none" stroke="${col}" stroke-width="${b.phi * 0.8}" data-pile="${b.id}"><title>Pile bar ${b.id}</title></polyline>`);
    }
    const zs = L.hbars.map((b) => b.z);
    const [zlo, zhi] = zs.length ? [Math.min(...zs), Math.max(...zs)] : [60, top - 60];
    for (const g of L.punch) {
      const across = cut === "X" ? g.v : g.u;
      if (Math.abs(across) > 60) continue;
      const u = cut === "X" ? g.u : g.v;
      parts.push(`<line x1="${u}" y1="${zlo}" x2="${u}" y2="${zhi}" stroke="${g.bad ? COL.clash : COL.punch}" stroke-width="${g.phi + 2}"/>`);
    }
    const lvl = (z, t) => `<text x="${-R + 10}" y="${-(z + 10)}" font-size="${R / 22}" fill="currentColor">${t}</text>`;
    return `<svg class="cl-svg" viewBox="${-R} ${-(top + 150)} ${2 * R} ${H}" preserveAspectRatio="xMidYMid meet"><g transform="scale(1,-1)">${parts.join("")}</g>
      ${lvl(top, `top ${f3(L.sc.host.top_m)}`)}${lvl(0, `soffit ${f3(L.sc.host.soffit_m)}`)}</svg>`;
  }

  // A small 3D view of the head's bars: drag to turn, wheel to zoom.
  function mount3d(canvas, L) {
    const segs = [];
    const add = (a, b, col, w) => segs.push({ a, b, col, w });
    const R = L.reach;
    for (const b of L.hbars) {
      if (b.kind === "link leg" || Math.abs(b.at) > R) continue;
      const lo = Math.max(b.lo ?? -R, -R);
      const hi = Math.min(b.hi ?? R, R);
      if (b.gone && !b.picked) continue;
      if (b.along === "X") add([lo, b.at, b.z], [hi, b.at, b.z], barColor(b, L), b.phi);
      else add([b.at, lo, b.z], [b.at, hi, b.z], barColor(b, L), b.phi);
    }
    for (const t of L.trim) {
      if (t.along === "X") add([Math.max(t.lo, -R), t.at, t.z], [Math.min(t.hi, R), t.at, t.z], COL.trim, t.phi);
      else add([t.at, Math.max(t.lo, -R), t.z], [t.at, Math.min(t.hi, R), t.z], COL.trim, t.phi);
    }
    const zs = L.hbars.map((b) => b.z);
    const [zlo, zhi] = zs.length ? [Math.min(...zs), Math.max(...zs)] : [60, L.depth - 60];
    for (const g of L.legs) add([g.u, g.v, zlo], [g.u, g.v, zhi], g.bad ? COL.clash : COL.link, g.phi);
    for (const g of L.punch) add([g.u, g.v, zlo], [g.u, g.v, zhi], g.bad ? COL.clash : COL.punch, g.phi);
    for (const b of L.pile) {
      if (b.gone && !b.picked) continue;
      const col = b.picked ? COL.pick : b.bad ? COL.clash : COL.pile;
      add([b.u, b.v, -600], [b.u, b.v, b.top], col, b.phi);
      if (b.leg > 0) add([b.u, b.v, b.top], [b.u + b.leg * Math.cos(b.a), b.v + b.leg * Math.sin(b.a), b.top], col, b.phi);
    }
    const box = [[-R, -R, 0], [R, -R, 0], [R, R, 0], [-R, R, 0]];
    const cam = { yaw: -0.7, pitch: 0.55, zoom: 1 };
    const ctx = canvas.getContext("2d");
    const project = (p, w, h, k) => {
      const [x, y, z] = [p[0], p[1], p[2] - L.depth / 2];
      const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
      const X = x * cy - y * sy;
      const Y = x * sy + y * cy;
      return [w / 2 + X * k, h / 2 - (z * cp - Y * sp) * k, Y * cp + z * sp];
    };
    const paint = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth || 500, h = canvas.clientHeight || 420;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const k = (cam.zoom * 0.8 * Math.min(w, h)) / (2 * Math.max(R, L.depth + 600));
      const soffit = box.map((p) => project(p, w, h, k));
      ctx.beginPath();
      soffit.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.closePath();
      ctx.fillStyle = COL.concrete;
      ctx.fill();
      const drawn = segs.map((s) => ({ s, a: project(s.a, w, h, k), b: project(s.b, w, h, k) }));
      drawn.sort((p, q) => (p.a[2] + p.b[2]) - (q.a[2] + q.b[2]));
      ctx.lineCap = "round";
      for (const d of drawn) {
        ctx.beginPath();
        ctx.moveTo(d.a[0], d.a[1]);
        ctx.lineTo(d.b[0], d.b[1]);
        ctx.strokeStyle = d.s.col;
        ctx.lineWidth = Math.max(1.2, d.s.w * k);
        ctx.stroke();
      }
    };
    let drag = null;
    canvas.onpointerdown = (e) => { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); };
    canvas.onpointermove = (e) => {
      if (!drag) return;
      cam.yaw += (e.clientX - drag[0]) * 0.01;
      cam.pitch = Math.max(-1.5, Math.min(1.5, cam.pitch + (e.clientY - drag[1]) * 0.01));
      drag = [e.clientX, e.clientY];
      paint();
    };
    canvas.onpointerup = () => (drag = null);
    canvas.onwheel = (e) => { e.preventDefault(); cam.zoom = Math.max(0.3, Math.min(8, cam.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15))); paint(); };
    paint();
    new ResizeObserver(paint).observe(canvas);
  }

  const checksTable = (checks) => !checks?.length ? "" : `<div class="scroll"><table><thead><tr><th>Check</th><th class="num">Utilisation before</th><th class="num">after</th>
    <th class="num">wk before (mm)</th><th class="num">after</th><th>Result</th></tr></thead><tbody>${checks.map((c) => `<tr><td>${esc(c.what)}${c.note ? `<br><span class="status">${esc(c.note)}</span>` : ""}</td>
      <td class="num">${f3(c.before?.utilisation)}</td><td class="num">${f3(c.after?.utilisation)}</td><td class="num">${f3(c.before?.wk_mm)}</td><td class="num">${f3(c.after?.wk_mm)}</td><td>${yes(c.passes)}</td></tr>`).join("")}</tbody></table></div>`;

  const layoutTable = (lay) => `<div class="scroll"><table><thead><tr><th>Perimeter</th><th class="num">Legs</th><th class="num">From the face (mm)</th><th class="num">Round it (mm)</th><th class="num">Limit</th>
    <th class="num">From the one inside (mm)</th><th class="num">Limit</th><th class="num">Asw (mm²)</th><th class="num">Needed</th><th>9.4.3</th></tr></thead><tbody>
    ${lay.rows.map((x) => `<tr><td>${x.perimeter}</td><td class="num">${x.legs}</td><td class="num">${x.from_face_mm.join("–")}</td><td class="num">${x.tangential_mm}</td><td class="num">${x.tangential_limit_mm}</td>
      <td class="num">${x.radial_mm ?? "–"}</td><td class="num">${x.radial_limit_mm}</td><td class="num">${x.asw_mm2}</td><td class="num">${x.asw_needed_mm2 ?? "–"}</td><td>${yes(x.passes)}</td></tr>`).join("")}</tbody></table></div>`;

  function drawHead() {
    const box = out.querySelector("#cl-head");
    const e = st.head;
    if (!e || !box) return;
    const g = data.groups.find((x) => x.key === st.group);
    const sc = e.scene;
    const L = layout();
    const views = [["before", "As designed"], ...(e.solutions || []).map((s) => [s.id, s.title + (s.recommended ? " ★" : "")]), ...(e.punching?.solution ? [["snap", "Punching links set out"]] : [])];
    const sol = (e.solutions || []).find((s) => s.id === st.view);
    const snap = st.view === "snap" ? e.punching.solution : null;
    const q = { group: st.group, index: st.index };
    box.innerHTML = `<div class="panel"><h2>${esc(g.pile)} into ${esc(g.host)}: head <select id="cl-idx">${g.heads.map((x) => `<option value="${x.index}" ${x.index === st.index ? "selected" : ""}>
        ${x.index + 1} at x ${fmt(x.x, 2)}, y ${fmt(x.y, 2)} (${x.count.clash} overlapping, ${x.count.pairs - x.count.clash} close${x.punch_count.pairs ? `, ${x.punch_count.pairs} punching` : ""})</option>`).join("")}</select></h2>
      <p><strong>Connection.</strong> ${esc(sc.connection.text)}.</p>
      ${e.weld ? `<p><strong>Bars welded to the tube.</strong> ${e.weld.leg_mm} mm fillet (throat ${e.weld.throat_mm} mm), fvw,d ${e.weld.fvw_mpa} MPa:
        ${e.weld.rows.map((r) => `row ${r.row} ${esc(r.bars)} needs <strong>${r.length_mm} mm</strong> of weld per bar (${fmt(r.force_kN, 1)} kN)`).join("; ")}.</p>` : ""}
      <p class="status">Calculation of this head: <a href="${calcUrl("docx", q)}">Word</a> · <a href="${calcUrl("pdf", q)}">PDF</a> · <a href="${calcUrl("xlsx", q)}">Excel</a></p>
      <div class="row"><label>Show <select id="cl-view">${views.map(([k, t]) => `<option value="${k}" ${k === st.view ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label>
        <span class="chip" style="background:${COL.clash};color:#fff">clash</span><span class="chip" style="background:${COL.pile};color:#fff">pile bars</span>
        <span class="chip" style="background:${COL.top};color:#fff">top bars</span><span class="chip" style="background:${COL.bottom};color:#fff">bottom bars</span>
        <span class="chip" style="background:${COL.punch};color:#fff">punching links</span><span class="chip" style="background:${COL.trim};color:#fff">trimmers</span>
        <span class="chip" style="background:${COL.pick};color:#fff">picked (what if)</span></div>
      ${sol ? `<p><strong>${esc(sol.title)}</strong>: ${yes(sol.passes)}, ${sol.left.clash} overlapping and ${sol.left.tight} too close left, steel ${f1(sol.delta_kg)} kg${sol.delta_kg_m3_per_head != null ? ` (${fmt(sol.delta_kg_m3_per_head, 3)} kg/m³ of ${esc(sol.element)})` : ""}.</p>` : ""}
      ${snap ? `<p><strong>${esc(snap.title)}</strong>: ${yes(snap.passes)}. ${esc(snap.how)}</p>` : ""}
      <div class="cl-views"><figure><figcaption>Plan${st.picking ? ": click bars to take them out" : ""}</figcaption>${planSvg(L)}</figure>
        <figure><figcaption>Section on X through the pile</figcaption>${sectionSvg(L, "X")}</figure>
        <figure><figcaption>Section on Y through the pile</figcaption>${sectionSvg(L, "Y")}</figure>
        <figure><figcaption>3D (drag to turn, wheel to zoom)</figcaption><canvas class="cl-3d"></canvas></figure></div>
      ${e.what?.length ? `<h3>What clashes</h3><div class="scroll"><table><thead><tr><th>Bars</th><th class="num">Pairs</th><th class="num">Overlapping</th><th class="num">Worst clear gap (mm)</th><th>Levels (m)</th></tr></thead><tbody>
        ${e.what.map((w) => `<tr><td>${esc(w.bars)}</td><td class="num">${w.pairs}</td><td class="num">${w.clash}</td><td class="num">${f1(w.worst_gap_mm)}</td><td>${w.levels_m.map((z) => fmt(z, 3)).join(", ")}</td></tr>`).join("")}</tbody></table></div>` : "<p>No pile bar clashes at this head.</p>"}
    </div>
    ${(e.solutions || []).length ? `<div class="panel"><h2>Solutions, each designed again</h2>${e.solutions.map((s) => `<details ${s.recommended ? "open" : ""}><summary><strong>${esc(s.title)}</strong>${s.recommended ? " ★ recommended" : ""}: ${yes(s.passes)},
        steel ${f1(s.delta_kg)} kg <button class="quiet" data-show="${s.id}" data-free>Show</button></summary><p>${esc(s.how)}</p>
        <p class="status">Left: ${s.left.clash} overlapping, ${s.left.tight} too close.</p>${checksTable(s.checks)}</details>`).join("")}</div>` : ""}
    ${e.punching ? `<div class="panel"><h2>Punching links</h2><p>d ${e.punching.info?.d_mm} mm, Ø${e.punching.info?.phi} legs, ${esc(String(e.punching.info?.counts || ""))} per perimeter;
        utilisation ${f3(e.punching.info?.utilisation)} without links, ${f3(e.punching.info?.utilisation_with_links)} with. As designed: ${yes(e.punching.layout.passes)}.</p>${layoutTable(e.punching.layout)}
        ${e.punching.solution ? `<h3>${esc(e.punching.solution.title)}: ${yes(e.punching.solution.passes)}</h3><p>${esc(e.punching.solution.how)} On site each leg is hooked over the outer bars in the opening shown.</p>
        ${layoutTable(e.punching.solution.layout)}<button class="quiet" data-show="snap" data-free>Show</button>` : ""}</div>` : ""}
    <div class="panel" data-free><h2>What if bars are taken out</h2>
      <p class="status">For a connection the contractor cannot build as drawn: pick the slab or beam bars and pile bars to leave out, then check it again.
        The design is not changed. Keep it to list it above and download its calculation.</p>
      <div class="row"><button id="cl-pick" class="${st.picking ? "" : "quiet"}">${st.picking ? "Picking: click bars in the plan or sections" : "Pick bars to take out"}</button>
        <span>${st.bars.size} bar(s), ${st.pileBars.size} pile bar(s) picked</span> <button class="quiet" id="cl-clear">Clear</button></div>
      <div class="row"><label style="flex:1">Note <input id="cl-note" value="${esc(st.note || "")}" placeholder="e.g. bars cannot pass the pile cage" style="width:100%"></label></div>
      <div class="row"><button id="cl-check" ${st.bars.size || st.pileBars.size ? "" : "disabled"}>Check again</button>
        <button class="quiet" id="cl-keep" ${st.check && !st.check.error ? "" : "disabled"}>Keep${st.keptId ? " (update)" : ""}</button>
        ${st.keptId ? `<a href="${calcUrl("docx", { whatif: st.keptId })}">Calculation (Word)</a> · <a href="${calcUrl("pdf", { whatif: st.keptId })}">PDF</a>` : ""}</div>
      <div id="cl-check-out">${st.check ? whatifHtml(st.check) : ""}</div></div>`;
    mount3d(box.querySelector(".cl-3d"), L);
    box.querySelector("#cl-idx").onchange = (ev) => openGroup(st.group, Number(ev.target.value));
    box.querySelector("#cl-view").onchange = (ev) => { st.view = ev.target.value; drawHead(); };
    box.querySelectorAll("[data-show]").forEach((b) => (b.onclick = (ev) => { ev.preventDefault(); st.view = b.dataset.show; drawHead(); box.scrollIntoView({ behavior: "smooth" }); }));
    box.querySelector("#cl-pick").onclick = () => { st.picking = !st.picking; if (st.picking) st.view = "before"; drawHead(); };
    box.querySelector("#cl-clear").onclick = () => { st.bars.clear(); st.pileBars.clear(); st.check = null; drawHead(); };
    box.querySelector("#cl-note").oninput = (ev) => (st.note = ev.target.value);
    box.querySelectorAll(".cl-svg [data-bar], .cl-svg [data-pile]").forEach((el) => (el.onclick = () => {
      if (!st.picking) return;
      const [set, id] = el.dataset.bar ? [st.bars, el.dataset.bar] : [st.pileBars, el.dataset.pile];
      set.has(id) ? set.delete(id) : set.add(id);
      st.check = null;
      drawHead();
    }));
    const body = () => JSON.stringify({ ...(st.keptId ? { id: st.keptId } : {}), group: st.group, head: st.index, bars: [...st.bars], pile_bars: [...st.pileBars], note: st.note || "" });
    box.querySelector("#cl-check").onclick = async () => {
      box.querySelector("#cl-check-out").innerHTML = '<p class="status">Checking…</p>';
      try {
        st.check = await api(`${secUrl()}/clashes/whatif`, { method: "POST", body: body() });
      } catch (err) {
        box.querySelector("#cl-check-out").innerHTML = `<p class="status">${esc(err.message)}</p>`;
        return;
      }
      drawHead();
    };
    box.querySelector("#cl-keep").onclick = async () => {
      const kept = await api(`${secUrl()}/clashes/whatifs`, { method: "POST", body: body() });
      st.keptId = kept.id;
      st.check = kept;
      data.whatifs = data.whatifs.filter((w) => w.id !== kept.id).concat([kept]);
      draw();
    };
  }

  const whatifHtml = (w) => `<p><strong>${w.passes ? "The connection still works with these bars taken out." : "The connection does not work with these bars taken out."}</strong>
    Steel ${f1(w.delta_kg)} kg.${w.unknown?.length ? ` Not found: ${esc(w.unknown.join(", "))}.` : ""}</p>${checksTable(w.checks)}`;

  draw();
}
