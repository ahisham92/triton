// Construction sequence: the section's works in the order they are built, played stage by stage in 3D,
// with the existing structure on site (drawn, demolished where the sequence says, checked for clashes
// with the new piles and walls) and what its tie rods do to the new wall's movement. Never a design input.
// app.js passes its helpers in: api, esc, fmt, tabData, sectionGeometry, sectionSite, saveSite, View3D,
// section() -> the section object (edited in place), markDirty(), save(), existingForm() -> a form element.
import { LABEL as PLANT, frameOf, plant } from "./equipment.js";

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
      <div class="row seq-bar"><label class="toggle" style="flex:1">Through the stage <input type="range" data-t min="0" max="100" value="100" style="flex:1" aria-label="Progress through the stage"></label>
      <label class="toggle"><input type="checkbox" data-crew checked> Plant and workers</label>
      <button class="quiet" data-record title="Plays the whole sequence from the start and saves it as a video">Export video (MP4)</button>
      <span class="status" data-recstat></span></div>
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

  const F = site?.frame ? frameOf(site) : null;
  const WORK_KIND = { steel_pipes: "combi_wall", combi_cages: "combi_wall", combi_infill: "combi_wall", sheet_piles: "sheet_pile_wall",
    pile_cages: "pile", pile_concrete: "pile", pile_heads: "pile", front_beam: "front_beam", rear_beam: "rear_beam",
    transverse_beam: "transverse_beam", slab: "slab" };
  const kindOf = (e) => e.type;
  const sdOf = (x, y) => (F ? F.sd(x, y) : [y, x]);
  const FURN_KINDS = { crane_rail: ["crane_rails"], tie_downs: ["tie_downs"], stow_pins: ["storm_pins"],
    crane_stoppers: ["crane_stoppers"], fenders: ["fenders", "fender_blocks", "ladders"], bollards: ["bollards"],
    sts_crane: ["sts_crane"], furniture: ["crane_rails", "tie_downs", "storm_pins", "crane_stoppers", "fenders", "fender_blocks", "ladders", "bollards", "sts_crane"] };
  // The crane rails' distances in from the face, sea side first.
  const railsD = () => (F ? (site.rails || []).map((r) => F.sd(r.line[0][0], r.line[0][1])[1]).sort((a, b) => a - b) : []);
  // The STS crane: its two leg lines along the berth, and how far out it stands on the ship (its rear
  // legs 2 m inside the ship's side at the fenders, 3 m off the face), the ship wide enough for it.
  const craneOf = () => {
    if (!F || !site?.crane?.lines) return null;
    const pts = site.crane.lines.slice(0, 4).map(([a]) => F.sd(a[0], a[1]));
    const legs = [...new Set(pts.map((p) => Math.round(p[0] * 100) / 100))].sort((a, b) => a - b);
    const ds = pts.map((p) => p[1]);
    const rf = Math.min(...ds);
    const rr = Math.max(...ds);
    return { legs: [legs[0], legs[legs.length - 1]], rf, rr, shift: rr + 3 + 2, beam: Math.max(40, rr - rf + 10) };
  };
  let t = 1; // how far through the stage shown
  let crewOn = true;

  // One frame: stage n, t of the way through it. The active elements are built one pile (or one
  // stretch of pour) after another, the plant and crew at the one being built.
  const frame = () => {
    const st = stages[n - 1];
    if (!st) return null;
    const prev = n > 1 ? stages[n - 2].elements : {};
    const active = new Set(st.active);
    const elements = [];
    const extras = [];
    const solids = [];
    const spots = {};
    const breaking = st.steps.some((s) => s.work === "pile_heads");
    const along = F?.along || "Y";
    // Each work's piles in one order along the berth (one rig or crane goes from one to the next).
    const queue = {};
    if (t < 1)
      for (const e of geo.elements) {
        if (!active.has(e.element) || !e.lines) continue;
        const work = st.steps.map((s) => s.work).find((w) => WORK_KIND[w] === kindOf(e));
        for (const ln of e.lines) (queue[work] ||= []).push({ e, ln, sd: sdOf(ln[0], ln[1]) });
      }
    const place = {};
    for (const [work, q] of Object.entries(queue)) {
      q.sort((a, b) => a.sd[0] - b.sd[0] || a.sd[1] - b.sd[1]);
      const k = t * q.length;
      const i = Math.min(Math.floor(k), q.length - 1);
      q.forEach((o, j) => (o.done = j < Math.floor(k)));
      place[work] = { cur: q[i], frac: k - Math.floor(k) };
    }
    for (const e of geo.elements) {
      const state = st.elements[e.element];
      if (!state) continue;
      const now = active.has(e.element);
      // A combi wall's cage sits in its pipe: the pipe stays drawn, with the bars standing out of it.
      const combi = kindOf(e) === "combi_wall";
      const style = (s, on) => ({
        tint: s === "pipe" || (s === "cage" && combi) ? TINT.pipe : s === "cage" ? TINT.cage : on ? TINT.active : null,
        thin: s === "cage" && !combi,
        tip: `${e.element}: ${STATE_WORDS[s]}${on ? " (this stage)" : ""}` });
      const size = site?.sizes?.[e.element]?.round || null;
      const head = (x, y, top, s) => {
        if (s === "cage" && combi)
          extras.push({ a: [x, y, top], b: [x, y, top + 1.5], color: TINT.cage, width: 2.5, element: e.element,
            tip: `${e.element}: cage in the pipe, its bars standing out to go into the front beam` });
        if (s === "cast_high")
          extras.push({ a: [x, y, top], b: [x, y, top + data.cast_above], color: "#ea580c", size, element: e.element,
            tip: `${e.element}: cast ${fmt(data.cast_above, 1)} m above its cut-off level (${fmt(top, 2)} m)` });
      };
      if (!now || t >= 1) {
        elements.push({ ...e, ...style(state, now) });
        if (e.lines) for (const [x, y, top] of e.lines) head(x, y, top, state);
        continue;
      }
      const work = st.steps.map((s) => s.work).find((w) => WORK_KIND[w] === kindOf(e));
      if (e.lines) {
        const mine = (queue[work] || []).filter((o) => o.e === e);
        const done = mine.filter((o) => o.done).map((o) => o.ln);
        const rest = mine.filter((o) => !o.done).map((o) => o.ln);
        const was = prev[e.element];
        if (done.length) elements.push({ ...e, lines: done, ...style(state, true) });
        if (rest.length && was) elements.push({ ...e, lines: rest, ...style(was, false), nolabel: done.length > 0 });
        for (const [x, y, top] of done) head(x, y, top, state);
        for (const [x, y, top] of rest) {
          head(x, y, top, was);
          if (breaking && state === "done")
            extras.push({ a: [x, y, top], b: [x, y, top + data.cast_above], color: "#dc2626", size, alpha: 0.7, element: e.element,
              tip: `${e.element}: head to be broken down to ${fmt(top, 2)} m` });
        }
        const { cur, frac } = place[work];
        if (cur.e !== e) continue;
        const dia = size || 1.0;
        spots[work] = { s: cur.sd[0], d: cur.sd[1], top: cur.ln[2], bottom: cur.ln[3], dia, progress: work !== "pile_cages" ? frac : frac > 0.6 ? (frac - 0.6) / 0.4 : frac / 0.6,
          phase: work === "pile_cages" && frac > 0.6 ? "cage" : "bore" };
        // The pile being bored or the pipe being driven: part of its length.
        if (!was && work !== "pile_cages")
          elements.push({ ...e, lines: [[cur.ln[0], cur.ln[1], cur.ln[2], cur.ln[2] - (cur.ln[2] - cur.ln[3]) * frac]], ...style(state, true) });
      } else if (e.box) {
        const [lo, hi] = e.box[along];
        const cut = lo + (hi - lo) * t;
        elements.push({ ...e, box: { ...e.box, [along]: [lo, Math.max(cut, lo + 0.05)] }, ...style(state, true) });
        const b = e.box;
        const across = along === "X" ? "Y" : "X";
        const mid = { [along]: cut, [across]: (b[across][0] + b[across][1]) / 2 };
        const [s0, d0] = sdOf(mid.X, mid.Y);
        const ends = [b[across][0], b[across][1]].map((v) => sdOf(along === "X" ? cut : v, along === "X" ? v : cut)[1]);
        spots[work] = { s: s0, d: d0, top: b.Z[1], rear: Math.max(...ends) };
      }
    }
    const L = site?.levels || {};
    const ex = site?.existing;
    const demolishing = st.steps.some((s) => s.work === "demolition") && t < 1;
    const stage = { ...st, demolished: demolishing ? false : st.demolished, demolish_t: demolishing ? t : null };
    // Furniture: the items of the work under way show up to where it has got along the berth; the STS
    // crane comes on its ship and is pushed across (triton/sequence.py FURNITURE).
    const furn = st.steps.map((s) => s.work).filter((w) => FURN_KINDS[w]);
    if (t < 1 && furn.length && site?.frame) {
      const [a0, a1] = site.frame.s;
      stage.furniture_upto = { kinds: furn.filter((w) => w !== "sts_crane").flatMap((w) => FURN_KINDS[w]), s: a0 + (a1 - a0) * t };
    }
    const cr = craneOf();
    if (cr && furn.includes("sts_crane") && t < 1) {
      const come = t < 0.3 ? (1 - t / 0.3) * 70 : 0;
      const k = t < 0.45 ? 0 : t > 0.95 ? 1 : (t - 0.45) / 0.5;
      stage.crane_shift = (cr.shift + come) * (1 - (k * k * (3 - 2 * k)));
    }
    const dredging = st.steps.some((s) => s.work === "dredging");
    const before = n > 1 ? stages[n - 2].seabed : st.seabed;
    if (dredging) stage.seabed = before + (st.seabed - before) * t;
    if (crewOn && F && site?.frame) {
      const deck = prev[Object.keys(prev).find((k) => /slab/i.test(geo.elements.find((g) => g.element === k)?.type || ""))] === "done";
      const platform = ex && !stage.demolished ? ex.cope : deck ? L.cope : L.ground;
      const ctx = { F, platform, cope: L.cope, water: Math.max(...(L.water || [0])), before, after: st.seabed,
        extent: site.frame.s, existing: ex, rails: railsD(), crane: cr, crane_shift: stage.crane_shift || 0 };
      for (const s of st.steps) {
        const sp = spots[s.work];
        const K = plant(s.work, sp ? { ...sp } : null, t, { ...ctx, rear: sp?.rear });
        solids.push(...K.solids);
        extras.push(...K.lines.map((l) => ({ ...l, site: true })));
      }
    }
    return { st, scene: { elements: geo.elements.filter((e) => false), site, stage, extras, solids, pins: t >= 1 ? pins(st) : [] }, elements };
  };

  const show = (fresh = true) => {
    const st = stages[n - 1];
    slider.value = n;
    h.lastStage = n;
    $("[data-t]").value = Math.round(t * 100);
    if (!st) {
      view.setScene({ elements: geo.elements, site });
      $("[data-title]").textContent = "No steps";
      return;
    }
    const f = frame();
    // The stage's elements (some split, some growing) stand in for the section's own list.
    const stageNames = Object.fromEntries(f.elements.map((e) => [e.element, true]));
    const scene = { ...f.scene, elements: f.elements, stage: { ...f.scene.stage, elements: stageNames } };
    scene.caption = [`Stage ${n} of ${stages.length}: ${st.steps.map((x) => x.name).join(" + ")}`, `${h.section().name || ""}`];
    if (fresh) view.setScene(scene);
    else view.update(scene);
    // The STS crane's stage: the view widens to take in the crane on its ship, and back after.
    const cr = craneOf();
    const wide = !!cr && st.steps.some((s) => s.work === "sts_crane");
    if (wide !== !!view.fitExtra?.length || (fresh && wide)) {
      const z = site.levels.cope ?? 0;
      // The ship alongside, and the crane (boom and all) on the quay and on the ship.
      const fr = site.frame;
      const out = (p, sh) => (fr.across === "X" ? [p[0] - fr.inland * sh, p[1], p[2]] : [p[0], p[1] - fr.inland * sh, p[2]]);
      const pts = site.crane.lines.flat();
      view.fitExtra = wide
        ? [...[[cr.legs[0] - 46, -cr.beam - 5, z], [cr.legs[1] + 36, -cr.beam - 5, z]].map(([s, d, zz]) => F.at(s, d, zz)),
            ...pts, ...pts.map((p) => out(p, cr.shift))]
        : [];
      view.fit(view.fitExtra);
    }
    $("[data-title]").textContent = `Stage ${n} of ${stages.length}: ${st.steps.map((s) => s.name).join(" + ")}`;
    const built = Object.entries(st.elements).map(([k, v]) => `${esc(k)}: ${STATE_WORDS[v]}`);
    $("[data-what]").innerHTML = [
      built.length ? built.join(" · ") : "Nothing built yet.",
      st.demolished ? "Existing front beam and slab demolished." : "",
      `Seabed ${fmt(f.scene.stage.seabed, 2)} m.`,
      crewOn ? `<span class="hint">Plant: ${esc(st.steps.map((s) => PLANT[s.work]).filter(Boolean).join("; "))}.</span>` : "",
    ].filter(Boolean).join(" ");
    host.querySelectorAll("[data-row]").forEach((r) => r.classList.toggle("on", st.steps.some((s) => String(s.index) === r.dataset.row)));
  };
  slider.oninput = () => {
    n = Number(slider.value);
    t = 1;
    show(false);
  };
  $("[data-t]").oninput = (e) => {
    t = Number(e.target.value) / 100;
    show(false);
  };
  $("[data-crew]").onchange = (e) => {
    crewOn = e.target.checked;
    show(false);
  };
  $("[data-prev]").onclick = () => ((n = Math.max(1, n - 1)), (t = 1), show(false));
  $("[data-next]").onclick = () => ((n = Math.min(stages.length, n + 1)), (t = 1), show(false));
  // Play: every stage takes the same time, whatever its real duration (the animation is not a
  // programme: the model is only the length analysed), the plant moving from pile to pile.
  let playing = false;
  const SECONDS = 6;
  const playBtn = $("[data-play]");
  const stop = () => {
    playing = false;
    playBtn.textContent = "Play";
  };
  const play = (onEnd) => {
    playing = true;
    playBtn.textContent = "Stop";
    let last = performance.now();
    let drawn = 0;
    const step = (now) => {
      if (!playing || !host.isConnected) return onEnd?.(false);
      t += (now - last) / 1000 / SECONDS;
      last = now;
      if (t >= 1) {
        if (n >= stages.length) {
          t = 1;
          show(false);
          stop();
          return onEnd?.(true);
        }
        n += 1;
        t = 0;
      }
      if (now - drawn > 50) {
        drawn = now;
        show(false);
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  playBtn.onclick = () => {
    if (playing) return stop();
    if (n >= stages.length && t >= 1) n = 1;
    if (t >= 1) t = 0;
    play();
  };

  // Video: the whole sequence played from the start and recorded from the view (MP4 where the
  // browser records MP4: Safari, and Chrome or Edge from 2024; else WebM).
  const recBtn = $("[data-record]");
  const recStat = $("[data-recstat]");
  const kind = ["video/mp4;codecs=avc1.42E01E", "video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"]
    .find((k) => window.MediaRecorder?.isTypeSupported?.(k));
  if (!kind || !view.canvas.captureStream) {
    recBtn.disabled = true;
    recBtn.title = "This browser cannot record the view";
  }
  recBtn.onclick = () => {
    if (playing) stop();
    view.pixelRatio = Math.max(2, window.devicePixelRatio || 1); // about 1400 px wide
    show(false);
    const stream = view.canvas.captureStream(30);
    const rec = new MediaRecorder(stream, { mimeType: kind, videoBitsPerSecond: 12e6 });
    const parts = [];
    rec.ondataavailable = (e) => e.data.size && parts.push(e.data);
    rec.onstop = () => {
      const type = kind.split(";")[0];
      const blob = new Blob(parts, { type });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${(h.section().name || "section").replace(/[^\w.-]+/g, "_")}-construction-sequence.${type === "video/mp4" ? "mp4" : "webm"}`;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      recStat.textContent = type === "video/mp4" ? "Saved as MP4." : "Saved as WebM: this browser does not record MP4 (Safari and recent Chrome do).";
      recBtn.disabled = false;
      view.pixelRatio = null;
      view.draw();
    };
    n = 1;
    t = 0;
    show(false);
    recBtn.disabled = true;
    recStat.textContent = `Recording… ${stages.length} stages, about ${stages.length * SECONDS} s. Keep this tab open.`;
    rec.start(1000);
    play((whole) => {
      setTimeout(() => rec.stop(), whole ? 1500 : 0); // the last frame held a moment
    });
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
  box.querySelector("[data-add]").onclick = () => edit((s) => s.push({ work: "fenders", name: "", with_previous: false }));
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
    if (k >= 0) ((n = k + 1), (t = 1), show(false));
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
