// The CDT SDK controls page (#/ui): every SDK control Triton uses, live, in the current theme, with
// the name of the SDK control it stands for. Like the SDK's own Gallery window, for reviewing the look.

import * as cdt from "./sdk.js";

const { esc } = cdt;
const SECTIONS = ["Section 01a", "Section 01b", "Section 02", "Section 03", "Corner", "Approach"];

const SAMPLE = [
  ["Pile(1)", "Section 01a", "Ø1200", "26T32 + 13T16", 0.93, "ULS-B-Apron"],
  ["Pile(2)", "Section 01a", "Ø1200", "22T25", 0.71, "ULS-B-Yard"],
  ["Pile(3)", "Section 01a", "Ø1000", "20T25", 1.04, "ULS-B-Apron"],
  ["Pile(1)", "Section 03", "Ø1200", "24T32", 0.88, "ULS-STS"],
  ["Pile(2)", "Section 03", "Ø1200", "22T28", 0.97, "ULS-STS"],
  ["Front beam", "Section 01a", "2000 × 2500", "12T32 bottom", 0.82, "ULS-Bollard"],
  ["Rear beam", "Section 01a", "4000 × 2000", "14T25 bottom", 0.64, "ULS-B-Yard"],
  ["Deck", "Section 01a", "700 slab", "T20@150 in X", 0.91, "ULS-B-Apron"],
  ["Deck", "Corner", "750 slab", "T25@150 in Y", 1.12, "ULS-Corner"],
  ["Combi wall", "Section 03", "Ø1420 × 22", "Infill T20@200", 0.77, "ULS-Soil"],
  ["Sheet pile wall", "Section 01a", "AZ 26-700", "S355GP", 2.31, "ULS-Soil"],
  ["Approach slab", "Section 02", "400 slab", "T16@150 in X", 0.43, "ULS-Traffic"],
];

export function galleryPage(app) {
  const swatches = [["Surface 0", "--cdt-surface0"], ["Surface 1", "--cdt-surface1"], ["Surface 2", "--cdt-surface2"], ["Surface 3", "--cdt-surface3"],
    ["Accent", "--cdt-accent"], ["Accent soft", "--cdt-accent-soft"], ["Accent strong", "--cdt-accent-strong"],
    ["Success", "--cdt-success"], ["Warning", "--cdt-warning"], ["Danger", "--cdt-danger"], ["Text 0", "--cdt-text0"], ["Text 2", "--cdt-text2"]];
  app.innerHTML = `<div class="cdt-gallery">
    <h1>CDT SDK controls</h1>
    <p class="sub">The controls and theme of the Dar Computational Design Team SDK (CDT.Wpf), as Triton uses them on the web.
      Switch the theme and colours at the top right; every page of Triton follows.</p>

    <div class="panel"><h2 style="margin-top:0">Theme <code>ThemeManager · CDT.* tokens</code></h2>
      <div class="specimen">${cdt.themeSwitch()}</div>
      <div class="specimen">${swatches.map(([n, v]) => `<div class="cdt-swatch"><i style="background:var(${v})"></i><span>${esc(n)}</span></div>`).join("")}</div></div>

    <h2>Buttons <code>CDT.PrimaryButton · SecondaryButton · GhostButton · DangerButton · LinkButton</code></h2>
    <div class="panel"><div class="specimen">
      <button type="button" class="cdt-btn primary">Design what changed</button>
      <button type="button" class="cdt-btn secondary">Download the report</button>
      <button type="button" class="cdt-btn ghost">Show all</button>
      <button type="button" class="cdt-btn soft-danger">Remove</button>
      <button type="button" class="cdt-btn danger">Delete</button>
      <button type="button" class="cdt-btn link">How it works</button>
      <button type="button" class="cdt-btn primary" disabled>Disabled</button></div>
      <div class="specimen"><button type="button">Triton button</button><button type="button" class="quiet">Quiet button</button><button type="button" class="danger">Danger button</button></div></div>

    <h2>Utilisation <code>cdt:UtilizationBar</code></h2>
    <div class="panel"><p class="status" style="margin-top:0">Green below 0.95, amber up to the limit, red over it; the mark on the track is the limit.</p>
      <div class="cdt-util-stack">${[["Pile(1) bending", 0.62], ["Pile(1) shear", 0.97], ["Front beam bending", 1.08], ["Deck crack width (QP)", 0.81], ["Sheet pile web shear", 2.31], ["Not designed", null]]
        .map(([l, r]) => cdt.utilBar(r, { label: l })).join("")}</div></div>

    <h2>Status <code>CDT.OutcomeLabel · StatusDot · Chip · Badge · Kbd</code></h2>
    <div class="panel"><div class="specimen">${cdt.outcome("ok", "Passes")} ${cdt.outcome("warning", "Check")} ${cdt.outcome("error", "Fails")} ${cdt.outcome("info", "Estimate")}</div>
      <div class="specimen"><span>${cdt.statusDot("ok", "Saved")}</span><span>${cdt.statusDot("warn", "Saving…")}</span><span>${cdt.statusDot("error", "Not saved")}</span></div>
      <div class="specimen">${cdt.chip("Ø32")} ${cdt.chip("ULS-B-Apron")} ${cdt.badge("Section 01a")} ${cdt.badge("v2")} Press ${cdt.kbd("Enter")} to commit, ${cdt.kbd("Esc")} to undo.</div></div>

    <h2>Messages <code>cdt:InfoBar · toast (AutoCloseAfter)</code></h2>
    ${cdt.infoBar({ severity: "info", title: "Section 01a is locked since the design.", message: "Its inputs are read only, so the results match them." })}
    ${cdt.infoBar({ severity: "success", title: "Designed.", message: "12 elements, all safe." })}
    ${cdt.infoBar({ severity: "warning", title: "3 sheets skipped.", message: "Their names match no element; map them on the Workbook tab.", closable: true })}
    ${cdt.infoBar({ severity: "error", title: "Pile(3) fails.", message: "Utilisation 1.04 in bending at −18.5 m.", action: '<button type="button" class="cdt-btn secondary" data-g-toast="error">Show as a toast</button>' })}
    <div class="specimen"><button type="button" class="cdt-btn secondary" data-g-toast="success">Toast: saved</button><button type="button" class="cdt-btn secondary" data-g-toast="warning">Toast: warning</button></div>

    <h2>Dialogs <code>cdt:DialogHost · CdtMessageBox</code></h2>
    <div class="panel"><div class="specimen"><button type="button" class="cdt-btn secondary" data-g-dlg="confirm">Confirm</button><button type="button" class="cdt-btn secondary" data-g-dlg="danger">Confirm a delete</button>
      <button type="button" class="cdt-btn secondary" data-g-dlg="prompt">Ask for a name</button><span class="status" id="g-dlg-out"></span></div></div>

    <h2>Number fields <code>cdt:NumberBox · QuantityUnits</code></h2>
    <div class="panel"><p class="status" style="margin-top:0">Type arithmetic (<b>6000/4 + 25</b>) or another unit (<b>0.25 m</b>, <b>10 in</b>, <b>1' 6"</b>); the field shows what it will take,
      commits on ${cdt.kbd("Enter")} or when you leave it, and ${cdt.kbd("↑")} ${cdt.kbd("↓")} step it (${cdt.kbd("Shift")} for a tenth). Every number field in Triton works this way.</p>
      <div class="fields">
        <div class="field"><label>Pile diameter</label><div class="inputwrap"><input type="number" step="50" value="1200" id="g-n1"><span class="unit">mm</span></div></div>
        <div class="field"><label>Bollard load</label><div class="inputwrap"><input type="number" step="any" value="1471"><span class="unit">kN</span></div></div>
        <div class="field"><label>Concrete strength</label><div class="inputwrap"><input type="number" step="5" value="40"><span class="unit">MPa</span></div></div>
        <div class="field"><label>Moment</label><div class="inputwrap"><input type="number" step="any" value="2450"><span class="unit">kNm</span></div></div>
      </div><p class="status" id="g-n-out">Committed: 1200 mm</p></div>

    <h2>Lists <code>cdt:SearchableComboBox · CDT.SegmentedList · CDT.ToggleSwitch</code></h2>
    <div class="panel"><div class="fields">
      <div class="field"><label>Governing combination</label><select id="g-combo">${["ULS-B-Apron", "ULS-B-Yard", "ULS-STS", "ULS-Bollard", "ULS-Berthing", "ULS-Soil", "ULS-Traffic", "ULS-Corner", "SLS-Characteristic", "SLS-Frequent", "QP", "QP-Long term", "Accidental-Ship impact"]
        .map((c) => `<option>${esc(c)}</option>`).join("")}</select><div class="hint">Twelve options or more: type to search, "uls b" finds ULS-B-Apron.</div></div>
      <div class="field"><label>Section</label><select>${SECTIONS.map((s) => `<option>${esc(s)}</option>`).join("")}</select><div class="hint">A short list stays a plain list.</div></div>
      <div class="field"><label>View</label><span class="cdt-seg" role="group" id="g-seg">${["Plan", "Section", "3D"].map((v, i) => `<button type="button" aria-pressed="${i === 0}">${v}</button>`).join("")}</span></div>
      <div class="field"><label>Options</label><label class="toggle" style="color:var(--text)"><input type="checkbox" class="cdt-switch" checked> Wood-Armer moments</label>
        <label class="toggle" style="color:var(--text)"><input type="checkbox" class="cdt-switch"> Design the corner zone on its own</label></div>
    </div></div>

    <h2>Grid <code>CDT.DataGrid · DataGridFilter</code></h2>
    <div class="panel"><p class="status" style="margin-top:0">Click a heading to sort (again to reverse, a third time for the original order); the funnel filters by the column's values.
      Every read-only table of six rows or more in Triton sorts; ten rows or more also filter.</p>
      <div class="scroll"><table><tr><th>Element</th><th>Section</th><th>Size</th><th>Bars</th><th>Utilisation</th><th>Governing</th></tr>
      ${SAMPLE.map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td>${cdt.utilCell(r[4])}<td>${esc(r[5])}</td></tr>`).join("")}</table></div></div>

    <div class="two">
      <div><h2>Steps <code>cdt:StepIndicator</code></h2><div class="panel" id="g-steps"></div></div>
      <div><h2>Empty <code>cdt:EmptyState</code></h2><div class="panel">${cdt.emptyState({ glyph: "file", title: "No workbook yet", message: "Upload the Plaxis workbook of this section to design its elements.", action: '<button type="button" class="cdt-btn primary">Upload a workbook</button>' })}</div></div>
      <div><h2>Loading <code>BusyDots · CDT.Skeleton</code></h2><div class="panel"><p class="status">${cdt.busyDots("Loading the results…")}</p>
        <div style="display:grid;gap:8px">${cdt.skeleton("60%", 18)}${cdt.skeleton("100%")}${cdt.skeleton("85%")}</div></div></div>
      <div><h2>Properties <code>CDT.PropertyList · cdt:InfoTip</code></h2><div class="panel">${cdt.propertyList([
        ["Diameter", "1200 mm"], ["Bars", "26T32 + 13T16"], ["Cover", `75 mm ${cdt.infoTip("Piles: 75 mm nominal cover, from the Design settings.")}`],
        ["Reinforcement in X direction", "T20@150"], ["Reinforcement in Y direction", "T16@150"], ["Utilisation", cdt.utilBar(0.93)]])}</div></div>
    </div>
    <h2>Brand <code>CDT.DarLogo</code></h2>
    <div class="panel"><div class="specimen" style="font-size:40px">${cdt.darLogo()}<span style="width:64px;height:36px;display:inline-flex">${cdt.darLogo()}</span></div></div>
  </div>`;

  // Live bits
  app.querySelectorAll("[data-g-toast]").forEach((b) => (b.onclick = () => {
    const k = b.dataset.gToast;
    cdt.toast({ success: "All changes saved.", warning: "Pile(2) is near the limit: 0.97.", error: "Pile(3) fails: utilisation 1.04." }[k], { severity: k, title: { success: "Saved", warning: "Check", error: "Fails" }[k] });
  }));
  const out = app.querySelector("#g-dlg-out");
  app.querySelectorAll("[data-g-dlg]").forEach((b) => (b.onclick = async () => {
    const k = b.dataset.gDlg;
    if (k === "confirm") out.textContent = `Answer: ${await cdt.confirmBox("Unlock Section 01a to edit? As you change an input, the design results it affects are deleted.", { ok: "Unlock", danger: false })}`;
    if (k === "danger") out.textContent = `Answer: ${await cdt.confirmBox('Delete "Section 01b"? This cannot be undone.', { ok: "Delete" })}`;
    if (k === "prompt") out.textContent = `Name: ${(await cdt.promptBox("Name of the copy", "Section 01a copy", { ok: "Duplicate" })) ?? "(cancelled)"}`;
  }));
  const n1 = app.querySelector("#g-n1");
  n1.addEventListener("change", () => (app.querySelector("#g-n-out").textContent = `Committed: ${n1.value} mm`));
  const seg = app.querySelector("#g-seg");
  seg.querySelectorAll("button").forEach((b) => (b.onclick = () => seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)))));
  const steps = ["Workbook", "Map the sheets", "Design", "Check", "Export"];
  let cur = 2;
  const drawSteps = () => {
    const box = app.querySelector("#g-steps");
    box.innerHTML = `${cdt.stepIndicator(steps, cur)}<div class="row"><button type="button" class="cdt-btn secondary" data-s="-1">Back</button><button type="button" class="cdt-btn primary" data-s="1">Next</button></div>`;
    box.querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => { cur = Math.max(-1, Math.min(steps.length, cur + Number(b.dataset.s))); drawSteps(); }));
  };
  drawSteps();
}
