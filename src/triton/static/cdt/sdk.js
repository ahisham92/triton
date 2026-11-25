// CDT SDK for the web: the controls of the Dar Computational Design Team SDK (CDT.Wpf) as browser
// components, in the SDK's theme (cdt.css). Each export names the SDK control it stands for.
//
// Two kinds of export:
//  - HTML builders a tab calls while it draws (utilBar, infoBar, emptyState, stepIndicator, ...);
//  - behaviours that upgrade what is already on the page (NumberBox on every number field, DataGrid
//    sorting and column filters, SearchableComboBox on long lists, busy dots on "Loading" lines),
//    started once by init(). They keep the page's own handlers and values, so no tab changes behaviour.

import { evaluate, isPlain } from "./units.js";

export { evaluate, parseUnit, convert, register } from "./units.js";

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } },
};

// ------------------------------------------------------------------ theme (ThemeManager)
const media = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
export const theme = {
  /** "auto" | "light" | "dark" as chosen; the page shows `resolved`. */
  get mode() { return store.get("cdt.theme") || "auto"; },
  get brand() { return store.get("cdt.brand") || "dar"; },
  get resolved() { return this.mode === "auto" ? (media?.matches ? "dark" : "light") : this.mode; },
  apply(mode = this.mode, brand = this.brand) {
    store.set("cdt.theme", mode);
    store.set("cdt.brand", brand);
    const root = document.documentElement;
    root.dataset.theme = this.resolved;
    root.dataset.brand = brand;
    document.querySelectorAll("[data-cdt-theme]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cdtTheme === mode)));
    document.querySelectorAll("[data-cdt-brand]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cdtBrand === brand)));
    window.dispatchEvent(new CustomEvent("cdt-theme", { detail: { theme: this.resolved, brand } }));
  },
  toggle() { this.apply(this.resolved === "dark" ? "light" : "dark"); return this.resolved; },
};
media?.addEventListener?.("change", () => theme.mode === "auto" && theme.apply());

const ICON = {
  auto: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor"/></svg>',
  light: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="currentColor"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M3 13l1.4-1.4M11.6 4.4 13 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  dark: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 10.5A6 6 0 0 1 5.5 2.5a6 6 0 1 0 8 8z" fill="currentColor"/></svg>',
  funnel: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1.5h10L7.2 6.2V10L4.8 11V6.2z" fill="currentColor"/></svg>',
};

/** The theme switch (CDT.SegmentedList bound to ThemeManager): Auto / Light / Dark, and the brand. */
export function themeSwitch({ brands = true } = {}) {
  const t = (k, label) => `<button type="button" data-cdt-theme="${k}" aria-pressed="${theme.mode === k}" title="${label} theme">${ICON[k]}<span class="sr">${label}</span></button>`;
  const b = (k, label) => `<button type="button" data-cdt-brand="${k}" aria-pressed="${theme.brand === k}" title="${label} colours">${label}</button>`;
  return `<span class="cdt-seg" role="group" aria-label="Theme">${t("auto", "Auto")}${t("light", "Light")}${t("dark", "Dark")}</span>`
    + (brands ? `<span class="cdt-seg" role="group" aria-label="Colours">${b("dar", "Dar")}${b("cdt", "CDT")}</span>` : "");
}

/** The "dar" wordmark (CDT.DarLogo, CDT.DarLogoGeometry on its 16 × 9 grid). */
export function darLogo(title = "Dar") {
  return `<svg class="cdt-dar-logo" viewBox="0 0 16 9" role="img" aria-label="${esc(title)}"><path fill-rule="evenodd" d="M4,0 H5 V8 L4,9 H1 L0,8 V4 L1,3 H4 Z M1,4 V8 H4 V4 Z M7,3 H10 L11,4 V8 L10,9 H7 L6,8 V6 L7,5 H10 V4 H7 Z M7,6 V8 H10 V6 Z M12,3 H16 V4 H13 V9 H12 Z"/></svg>`;
}

// ------------------------------------------------------------------ status (UtilizationBar, OutcomeLabel, StatusDot)
/**
 * A demand / capacity ratio against its limit (cdt:UtilizationBar): green below `warning` of the limit,
 * amber up to the limit, red over it, with the number, a glyph and the limit marked on the track.
 * Triton's own rule is amber from 0.95, so that is the default here.
 */
export function utilBar(ratio, { limit = 1, warning = 0.95, label = "", digits = 2, title = "" } = {}) {
  if (ratio == null || !Number.isFinite(Number(ratio))) {
    return `<span class="cdt-util none"${title ? ` title="${esc(title)}"` : ""}>${label ? `<span class="cdt-util-label">${esc(label)}</span>` : ""}<span class="cdt-util-track"></span><span class="cdt-util-num">–</span></span>`;
  }
  const r = Number(ratio);
  const tone = r > limit + 1e-6 ? "bad" : r >= warning * limit - 1e-9 ? "warn" : "ok";
  const scale = Math.max(limit * 1.25, r); // the limit sits at 80% of the track until the ratio is over it
  const word = { ok: "safe", warn: "near the limit", bad: "unsafe" }[tone];
  return `<span class="cdt-util ${tone}" role="meter" aria-valuenow="${r}" aria-valuemin="0" aria-valuemax="${limit}"
    title="${esc(title || `${r.toFixed(digits)} of ${limit}: ${word}`)}">${label ? `<span class="cdt-util-label">${esc(label)}</span>` : ""}<span class="cdt-util-track"><i style="width:${Math.min(100, (100 * r) / scale).toFixed(1)}%"></i><b style="left:${((100 * limit) / scale).toFixed(1)}%"></b></span><span class="cdt-util-num">${r.toFixed(digits)}</span></span>`;
}

/** A table cell holding a utilisation bar; keeps Triton's ok / error class for anything that reads it. */
export function utilCell(ratio, { tip = "", limit = 1, digits = 2, extra = "" } = {}) {
  const none = ratio == null || !Number.isFinite(Number(ratio));
  const pass = !none && Number(ratio) <= limit + 1e-6;
  return `<td class="cell cdt-util-cell ${none ? "missing" : pass ? "ok" : "error"}${extra ? ` ${extra}` : ""}"${tip ? ` title="${esc(tip)}"` : ""}>${utilBar(ratio, { limit, digits, title: tip })}</td>`;
}

/** An outcome as glyph + word + colour (CDT.OutcomeLabel). tone: ok | warning | error | info. */
export const outcome = (tone, word) => `<span class="cdt-outcome ${esc(tone)}">${esc(word)}</span>`;
/** A state light (CDT.StatusDot): ok | warn | error, always paired with a word. */
export const statusDot = (tone, word = "") => `<span class="cdt-dot ${esc(tone)}" aria-hidden="true"></span>${word ? ` ${esc(word)}` : ""}`;
export const chip = (text) => `<span class="cdt-chip">${esc(text)}</span>`;
export const badge = (text) => `<span class="cdt-badge">${esc(text)}</span>`;
export const kbd = (text) => `<kbd class="cdt-kbd">${esc(text)}</kbd>`;

// ------------------------------------------------------------------ messages (InfoBar, EmptyState, BusyDots)
const GLYPH = { info: "i", success: "✓", warning: "!", error: "✕" };
/** A message in the page (cdt:InfoBar). `action` is HTML (a button); `closable` adds ×. */
export function infoBar({ severity = "info", title = "", message = "", action = "", closable = false } = {}) {
  return `<div class="cdt-infobar ${esc(severity)}" role="${severity === "error" ? "alert" : "status"}"><span class="cdt-ib-glyph" aria-hidden="true">${GLYPH[severity] || "i"}</span>
    <div class="cdt-ib-body">${title ? `<span class="cdt-ib-title">${esc(title)}</span>` : ""}${message}${action ? `<div style="margin-top:6px">${action}</div>` : ""}</div>${closable ? '<button type="button" class="cdt-ib-close" aria-label="Close" data-cdt-close>×</button>' : ""}</div>`;
}

let toastHost = null;
/** An InfoBar that closes itself (InfoBar.AutoCloseAfter); paused while the mouse is over it. */
export function toast(message, { severity = "info", title = "", ms = 4500 } = {}) {
  if (!toastHost?.isConnected) {
    toastHost = document.createElement("div");
    toastHost.className = "cdt-toasts";
    toastHost.setAttribute("aria-live", "polite");
    document.body.append(toastHost);
  }
  const box = document.createElement("div");
  box.innerHTML = infoBar({ severity, title, message: esc(message).replace(/\n/g, "<br>"), closable: true });
  const el = box.firstElementChild;
  toastHost.append(el);
  let left = ms, began = Date.now(), timer = null;
  const close = () => { clearTimeout(timer); el.remove(); };
  const run = () => { began = Date.now(); timer = setTimeout(close, left); };
  el.addEventListener("mouseenter", () => { clearTimeout(timer); left -= Date.now() - began; });
  el.addEventListener("mouseleave", run);
  el.querySelector("[data-cdt-close]").onclick = close;
  if (ms > 0) run();
  return close;
}

const EMPTY_ICON = {
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4.5 4.5L19 7"/></svg>',
  quay: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3 9h18M5 9v11M10 9v11M15 9v11M20 9v11M2 16c2 1 3 1 5 0s3-1 5 0 3 1 5 0 3-1 5 0"/></svg>',
};
/** What a list shows when it is empty (cdt:EmptyState): glyph, title, message and an action (HTML). */
export function emptyState({ glyph = "list", title = "", message = "", action = "" } = {}) {
  return `<div class="cdt-empty"><span class="cdt-empty-glyph">${EMPTY_ICON[glyph] || EMPTY_ICON.list}</span>${title ? `<b>${esc(title)}</b>` : ""}${message ? `<p>${message}</p>` : ""}${action ? `<div class="cdt-empty-action">${action}</div>` : ""}</div>`;
}

export const busyDots = (text = "") => `<span class="cdt-busy" aria-hidden="true"><i></i><i></i><i></i></span>${esc(text)}`;
/** A loading placeholder sized like what it stands in for (CDT.Skeleton). */
export const skeleton = (w = "100%", h = 14) => `<span class="cdt-skeleton" style="width:${typeof w === "number" ? `${w}px` : w};height:${h}px"></span>`;

/** The steps of a multi-step job (cdt:StepIndicator). current: -1 none started, steps.length all done. */
export function stepIndicator(steps, current, { bad = [] } = {}) {
  return `<ol class="cdt-steps">${steps.map((s, i) => `<li class="${i < current ? "done" : i === current ? "current" : ""}${bad.includes(i) ? " bad" : ""}" data-i="${i}"${i === current ? ' aria-current="step"' : ""}>
    <span class="n"><span>${i + 1}</span></span>${esc(s)}</li>`).join("")}</ol>`;
}

/** Name / value rows (CDT.PropertyList). rows: [[name, valueHtml], ...]. */
export const propertyList = (rows) => `<dl class="cdt-props">${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

/** An "i" whose tip shows a caption and, optionally, a picture (cdt:InfoTip). */
export const infoTip = (caption, image = "") => `<button type="button" class="cdt-infotip" data-cdt-tip="${esc(caption)}"${image ? ` data-cdt-tip-img="${esc(image)}"` : ""} aria-label="${esc(caption)}">i</button>`;

// ------------------------------------------------------------------ dialogs (DialogHost / CdtMessageBox)
/**
 * A dialog inside the page: dims it with the scrim, keeps Tab inside, Esc cancels. Resolves with the
 * value of the button pressed (or null), or the typed text for a prompt.
 */
export function dialog({ title = "", message = "", tone = "info", buttons = [["OK", "ok", "primary"]], input = null } = {}) {
  return new Promise((resolve) => {
    const d = document.createElement("dialog");
    d.className = `cdt-dialog ${tone}`;
    d.innerHTML = `<form method="dialog"><div class="cdt-dlg-head"><span class="cdt-dlg-glyph" aria-hidden="true">${{ danger: "!", warning: "!", info: "i", question: "?" }[tone] || "i"}</span><h3>${esc(title)}</h3></div>
      <div class="cdt-dlg-body">${esc(message)}${input != null ? `<input type="text" name="v" value="${esc(input)}" autocomplete="off">` : ""}</div>
      <div class="cdt-dlg-foot">${buttons.map(([label, value, kind]) => `<button type="submit" class="cdt-btn ${kind || "secondary"}" value="${esc(value)}">${esc(label)}</button>`).join("")}</div></form>`;
    document.body.append(d);
    const field = d.querySelector("input[name=v]");
    d.addEventListener("close", () => {
      const v = d.returnValue;
      d.remove();
      if (input != null) resolve(v === "ok" ? field.value : null);
      else resolve(v || null);
    });
    d.addEventListener("cancel", () => { d.returnValue = ""; });
    d.showModal();
    if (field) { field.focus(); field.select(); }
    else d.querySelector(".cdt-btn.primary, .cdt-btn.danger")?.focus();
  });
}
const firstLine = (m) => {
  const s = String(m);
  const cut = s.search(/[?.!:]\s|\n/);
  return cut > 0 && cut < 90 ? [s.slice(0, cut + 1), s.slice(cut + 1).trim()] : ["", s];
};
/** confirm() as a themed dialog. danger: the OK button is the red one. */
export async function confirmBox(message, { title = "", ok = "OK", cancel = "Cancel", danger = /delete|remove|discard|replace|close without/i.test(String(message)) } = {}) {
  const [t, rest] = title ? [title, message] : firstLine(message);
  const v = await dialog({ title: t || "Please confirm", message: rest, tone: danger ? "danger" : "question",
    buttons: [[cancel, "cancel", "secondary"], [ok, "ok", danger ? "danger" : "primary"]] });
  return v === "ok";
}
/** alert() as a themed dialog. */
export async function alertBox(message, { title = "", tone = "info" } = {}) {
  const [t, rest] = title ? [title, message] : firstLine(message);
  await dialog({ title: t || "Note", message: rest, tone, buttons: [["OK", "ok", "primary"]] });
}
/** prompt() as a themed dialog: the typed text, or null when cancelled. */
export function promptBox(message, value = "", { title = "", ok = "OK" } = {}) {
  return dialog({ title: title || message, message: title ? message : "", tone: "question", input: value ?? "",
    buttons: [["Cancel", "cancel", "secondary"], [ok, "ok", "primary"]] });
}

// ------------------------------------------------------------------ NumberBox
// Every number field takes arithmetic (6000/4 + 25) and, when it shows a unit, a unit after the number
// (0.25 m in a mm field). It shows what it will commit while you type, commits on Enter or when you
// leave it (Esc puts the old value back), and ↑ / ↓ step it (Shift for a tenth). The page's own
// handlers see only plain numbers, exactly as from the browser's number field.
const unitOf = (el) => el.dataset.unit || el.closest(".inputwrap")?.querySelector(".unit")?.textContent?.trim() || "";
let tip = null;
function showTip(el, text, bad) {
  if (!tip) { tip = document.createElement("div"); tip.className = "cdt-number-tip"; document.body.append(tip); }
  const r = el.getBoundingClientRect();
  tip.textContent = text;
  tip.classList.toggle("bad", !!bad);
  tip.style.left = `${Math.max(4, r.left)}px`;
  tip.style.top = `${r.bottom + 6}px`;
  tip.hidden = false;
}
const hideTip = () => { if (tip) tip.hidden = true; };
const fmtNum = (v) => String(Number(v.toPrecision(12)));

function upgradeNumber(el) {
  if (el.dataset.cdtNumber) return;
  el.dataset.cdtNumber = "1";
  el.dataset.step = el.getAttribute("step") || "";
  el.type = "text";
  el.inputMode = "decimal";
  el.autocomplete = "off";
  el.spellcheck = false;
  el.classList.add("cdt-number");
}

function onNumberInput(e) {
  const el = e.target;
  if (!el.dataset?.cdtNumber || e.cdtOwn) return;
  const t = el.value;
  if (t.trim() === "" || isPlain(t)) { el.classList.remove("cdt-invalid"); hideTip(); return; }
  // An expression in progress: the page waits for the result.
  e.stopImmediatePropagation();
  const r = evaluate(t, unitOf(el));
  el.classList.toggle("cdt-invalid", !!r.error && !/Unfinished|not closed/.test(r.error));
  if (r.error) showTip(el, /Unfinished|not closed/.test(r.error) ? "…" : r.error, true);
  else showTip(el, `= ${fmtNum(r.value)}${unitOf(el) ? ` ${unitOf(el)}` : ""}`);
}

function fire(el, type) {
  const ev = new Event(type, { bubbles: true });
  ev.cdtOwn = true;
  el.dispatchEvent(ev);
}

/** Turn what was typed into its number and tell the page. True when the value changed. */
function commitNumber(el) {
  const t = el.value;
  if (t.trim() === "" || isPlain(t)) return false;
  const r = evaluate(t, unitOf(el));
  if (r.error) { el.classList.add("cdt-invalid"); showTip(el, r.error, true); return false; }
  el.value = fmtNum(r.value);
  el.classList.remove("cdt-invalid");
  hideTip();
  fire(el, "input");
  el.dataset.cdtFocusValue = el.value; // Esc goes back to the last committed value
  return true;
}

function onNumberKey(e) {
  const el = e.target;
  if (!el.dataset?.cdtNumber || el.readOnly || el.disabled) return;
  if (e.key === "Enter") {
    if (commitNumber(el)) { fire(el, "change"); el.dataset.cdtCommitted = el.value; }
  } else if (e.key === "Escape") {
    if (el.dataset.cdtFocusValue != null && el.value !== el.dataset.cdtFocusValue) {
      el.value = el.dataset.cdtFocusValue;
      el.classList.remove("cdt-invalid");
      hideTip();
      fire(el, "input");
      e.preventDefault();
    }
  } else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.altKey && !e.ctrlKey && !e.metaKey) {
    commitNumber(el);
    const step = Number(el.dataset.step) > 0 ? Number(el.dataset.step) : 1;
    const by = (e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? step / 10 : step);
    const now = el.value.trim() === "" ? 0 : Number(el.value);
    if (!Number.isFinite(now)) return;
    let v = now + by;
    const min = el.getAttribute("min"), max = el.getAttribute("max");
    if (min !== null && min !== "" && v < Number(min)) v = Number(min);
    if (max !== null && max !== "" && v > Number(max)) v = Number(max);
    el.value = fmtNum(v);
    el.dataset.cdtFocusValue = el.value;
    e.preventDefault();
    fire(el, "input");
    fire(el, "change");
  }
}

function onNumberChange(e) {
  const el = e.target;
  if (!el.dataset?.cdtNumber || e.cdtOwn) return;
  // The browser's own change after Enter already committed: once is enough.
  if (el.dataset.cdtCommitted != null && el.dataset.cdtCommitted === el.value) {
    delete el.dataset.cdtCommitted;
    e.stopImmediatePropagation();
    return;
  }
  if (!isPlain(el.value) && el.value.trim() !== "") {
    if (!commitNumber(el)) e.stopImmediatePropagation(); // a bad expression stays to be fixed
  }
}

// ------------------------------------------------------------------ DataGrid (sorting, DataGridFilter)
// A read-only table of six rows or more gets the SDK grid: sticky header, click a header to sort
// (again to reverse, a third time for the original order), and with ten rows or more an Excel-style
// funnel per column with the column's values, their counts, a search and Select all. Tables with
// inputs, merged cells, clickable rows or group rows keep their own layout.
const cellText = (td) => (td?.textContent || "").trim();
const asNumber = (s) => {
  const m = String(s).replace(/[−–]/g, "-").match(/^[^\d-]*(-?\d+(?:\.\d+)?)/);
  return m && /^[\s\S]{0,4}-?\d/.test(s) ? Number(m[1]) : NaN;
};

function gridParts(table) {
  const rows = [...table.rows];
  const head = rows[0];
  if (!head || ![...head.cells].every((c) => c.tagName === "TH")) return null;
  const body = rows.slice(1);
  const n = head.cells.length;
  if (n < 2 || body.length < 6) return null;
  if ([...head.cells].some((c) => c.colSpan > 1 || c.rowSpan > 1)) return null;
  for (const r of body) {
    if (r.cells.length !== n || r.className || r.onclick || r.dataset.i != null) return null;
    for (const c of r.cells) if (c.colSpan > 1 || c.rowSpan > 1 || c.tagName === "TH") return null;
  }
  if (table.querySelector("input, select, textarea, button, details")) return null;
  return { head, body, n };
}

function upgradeGrid(table) {
  if (table.dataset.cdtGrid || table.closest("[data-cdt-plain], .cdt-gallery-plain")) return;
  const parts = gridParts(table);
  table.dataset.cdtGrid = parts ? "1" : "0";
  if (!parts) return;
  const { head, body } = parts;
  table.classList.add("cdt-grid");
  const original = [...body];
  const filters = new Map(); // column -> Set of allowed values
  let sortCol = -1, sortDir = 0;
  const parent = body[0].parentNode;
  const withFilters = body.length >= 10;
  let foot = null;
  const update = () => {
    let shown = 0;
    for (const r of original) {
      const hide = [...filters].some(([c, set]) => !set.has(cellText(r.cells[c])));
      r.classList.toggle("cdt-hidden", hide);
      if (!hide) shown++;
    }
    if (withFilters) {
      if (!foot) { foot = document.createElement("div"); foot.className = "cdt-grid-foot"; (table.closest(".scroll") || table).after(foot); }
      foot.innerHTML = filters.size
        ? `${shown} of ${original.length} rows shown <button type="button" class="cdt-btn link" data-clear>Show all</button>`
        : "";
      const clear = foot.querySelector("[data-clear]");
      if (clear) clear.onclick = () => { filters.clear(); paint(); update(); };
    }
  };
  const order = () => {
    let rows = [...original];
    if (sortDir) {
      const vals = rows.map((r) => cellText(r.cells[sortCol]));
      const nums = vals.map(asNumber);
      const numeric = nums.filter((x) => !Number.isNaN(x)).length >= vals.filter((v) => v && v !== "–" && v !== "-").length * 0.8;
      const idx = rows.map((_, i) => i);
      idx.sort((a, b) => {
        let d;
        if (numeric) {
          const x = nums[a], y = nums[b];
          d = Number.isNaN(x) ? (Number.isNaN(y) ? 0 : 1) : Number.isNaN(y) ? -1 : (x - y) * sortDir;
        } else d = vals[a].localeCompare(vals[b], undefined, { numeric: true, sensitivity: "base" }) * sortDir;
        return d || a - b;
      });
      rows = idx.map((i) => original[i]);
    }
    for (const r of rows) parent.append(r);
  };
  const paint = () => {
    [...head.cells].forEach((th, c) => {
      const s = th.querySelector(".cdt-sort");
      if (s) s.textContent = c === sortCol && sortDir ? (sortDir > 0 ? "▲" : "▼") : "";
      th.setAttribute("aria-sort", c === sortCol && sortDir ? (sortDir > 0 ? "ascending" : "descending") : "none");
      th.querySelector(".cdt-funnel")?.classList.toggle("on", filters.has(c));
    });
  };
  [...head.cells].forEach((th, c) => {
    if (!cellText(th)) return;
    th.classList.add("cdt-sortable");
    th.tabIndex = 0;
    th.title = th.title || "Sort by this column";
    th.innerHTML = `<span class="cdt-th">${th.innerHTML}<span class="cdt-sort" aria-hidden="true"></span>${withFilters ? `<button type="button" class="cdt-funnel" aria-label="Filter this column">${ICON.funnel}</button>` : ""}</span>`;
    const sort = () => {
      if (sortCol !== c) { sortCol = c; sortDir = 1; } else sortDir = sortDir === 1 ? -1 : sortDir === -1 ? 0 : 1;
      order();
      paint();
    };
    th.addEventListener("click", (e) => { if (!e.target.closest(".cdt-funnel")) sort(); });
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
    th.querySelector(".cdt-funnel")?.addEventListener("click", (e) => {
      e.stopPropagation();
      openFilter(e.currentTarget, original, c, filters.get(c), (set) => {
        if (set) filters.set(c, set); else filters.delete(c);
        paint();
        update();
      });
    });
  });
  paint();
}

let pop = null;
function closePop() { pop?.remove(); pop = null; }
function openFilter(anchor, rows, col, current, done) {
  closePop();
  const counts = new Map();
  for (const r of rows) { const v = cellText(r.cells[col]); counts.set(v, (counts.get(v) || 0) + 1); }
  const vals = [...counts.keys()].sort((a, b) => {
    const x = asNumber(a), y = asNumber(b);
    return !Number.isNaN(x) && !Number.isNaN(y) ? x - y : a.localeCompare(b, undefined, { numeric: true });
  });
  const on = new Set(current || vals);
  pop = document.createElement("div");
  pop.className = "cdt-filter-pop";
  pop.innerHTML = `<input type="search" placeholder="Search the values" aria-label="Search the values"><div class="vals"></div>
    <div class="acts"><button type="button" class="cdt-btn ghost" data-all>Select all</button><span><button type="button" class="cdt-btn secondary" data-x>Cancel</button> <button type="button" class="cdt-btn primary" data-ok>Apply</button></span></div>`;
  document.body.append(pop);
  const box = pop.querySelector(".vals"), q = pop.querySelector("input");
  const draw = () => {
    const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    box.innerHTML = vals.filter((v) => words.every((w) => v.toLowerCase().replace(/\s+/g, "").includes(w)))
      .map((v) => `<label><input type="checkbox" value="${esc(v)}" ${on.has(v) ? "checked" : ""}><span>${esc(v || "(empty)")}</span><span class="c">${counts.get(v)}</span></label>`).join("")
      || '<span class="status">No value matches.</span>';
    box.querySelectorAll("input").forEach((i) => (i.onchange = () => (i.checked ? on.add(i.value) : on.delete(i.value))));
  };
  q.oninput = draw;
  draw();
  pop.querySelector("[data-all]").onclick = () => { const all = vals.every((v) => on.has(v)); vals.forEach((v) => (all ? on.delete(v) : on.add(v))); draw(); };
  pop.querySelector("[data-x]").onclick = closePop;
  pop.querySelector("[data-ok]").onclick = () => { done(on.size === vals.length ? null : new Set(on)); closePop(); };
  const r = anchor.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(r.left - 20, innerWidth - 262))}px`;
  pop.style.top = `${Math.min(r.bottom + 6, innerHeight - 340)}px`;
  q.focus();
}

// ------------------------------------------------------------------ SearchableComboBox
// A list of twelve options or more gets a search box over it: words match anywhere, ignoring spaces
// and case ("pile 1" finds "Pile(1)"), the matched text is marked, options that start with the first
// word come first, and the last five picks are pinned under "Recent". The <select> stays the value,
// so the page's handlers and saved values are untouched.
const RECENT = new Map();
function upgradeCombo(sel) {
  if (sel.dataset.cdtCombo || sel.multiple || sel.size > 1 || sel.options.length < 12 || sel.closest("[data-cdt-plain]")) return;
  sel.dataset.cdtCombo = "1";
  const wrap = document.createElement("span");
  wrap.className = "cdt-combo";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "cdt-combo-input";
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.autocomplete = "off";
  input.spellcheck = false;
  const w = sel.getBoundingClientRect().width;
  if (w > 0) wrap.style.minWidth = `${Math.round(w)}px`;
  sel.before(wrap);
  wrap.append(input, sel);
  sel.tabIndex = -1;
  sel.setAttribute("aria-hidden", "true");
  const key = sel.name || sel.id || sel.closest("[data-path]")?.dataset.path || "";
  const label = () => sel.selectedOptions[0]?.textContent ?? "";
  const sync = () => { input.value = label(); input.disabled = sel.disabled; input.title = label(); };
  sync();
  new MutationObserver(sync).observe(sel, { attributes: true, childList: true, subtree: true });
  sel.addEventListener("change", sync);
  let list = null, active = 0, items = [];
  const close = () => { list?.remove(); list = null; sync(); };
  const choose = (opt) => {
    if (!opt || opt.disabled) return;
    if (sel.value !== opt.value) {
      sel.value = opt.value;
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (key) {
      const r = (RECENT.get(key) || []).filter((v) => v !== opt.value);
      RECENT.set(key, [opt.value, ...r].slice(0, 5));
    }
    close();
  };
  const mark = (text, words) => {
    let out = esc(text);
    for (const wd of words) out = out.replace(new RegExp(`(${wd.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"), "<mark>$1</mark>");
    return out;
  };
  const draw = (typed) => {
    const words = typed ? input.value.toLowerCase().split(/\s+/).filter(Boolean) : [];
    const norm = (s) => s.toLowerCase().replace(/\s+/g, "");
    const opts = [...sel.options];
    let hits = opts.filter((o) => words.every((wd) => norm(o.textContent).includes(wd)));
    if (words.length) hits.sort((a, b) => (norm(b.textContent).startsWith(words[0]) ? 1 : 0) - (norm(a.textContent).startsWith(words[0]) ? 1 : 0));
    const recent = !words.length ? (RECENT.get(key) || []).map((v) => opts.find((o) => o.value === v)).filter(Boolean) : [];
    items = [...recent, ...hits];
    const row = (o, i) => `<div class="opt${o.selected ? " sel" : ""}${i === active ? " on" : ""}" role="option" data-i="${i}"${o.disabled ? ' aria-disabled="true"' : ""}>${mark(o.textContent, words)}</div>`;
    list.innerHTML = (recent.length ? `<div class="grp">Recent</div>${recent.map((o, i) => row(o, i)).join("")}<div class="grp">All</div>` : "")
      + (hits.map((o, i) => row(o, i + recent.length)).join("") || '<div class="none">Nothing matches.</div>');
    list.querySelector(".opt.on")?.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    if (list || sel.disabled) return;
    list = document.createElement("div");
    list.className = "cdt-combo-list";
    list.setAttribute("role", "listbox");
    document.body.append(list);
    const r = input.getBoundingClientRect();
    list.style.left = `${r.left}px`;
    list.style.minWidth = `${r.width}px`;
    const below = innerHeight - r.bottom;
    if (below < 220 && r.top > below) { list.style.bottom = `${innerHeight - r.top + 4}px`; list.style.maxHeight = `${Math.min(320, r.top - 12)}px`; }
    else { list.style.top = `${r.bottom + 4}px`; list.style.maxHeight = `${Math.min(320, below - 12)}px`; }
    active = Math.max(0, [...sel.options].indexOf(sel.selectedOptions[0]));
    draw(false);
    const recentCount = items.length - sel.options.length;
    active += Math.max(0, recentCount);
    draw(false);
    list.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const o = e.target.closest(".opt");
      if (o) choose(items[Number(o.dataset.i)]);
    });
  };
  input.addEventListener("focus", () => { open(); input.select(); });
  input.addEventListener("click", open);
  input.addEventListener("blur", close);
  input.addEventListener("input", () => { open(); active = 0; draw(true); });
  input.addEventListener("keydown", (e) => {
    if (!list && (e.key === "ArrowDown" || e.key === "Enter")) { open(); e.preventDefault(); return; }
    if (!list) return;
    const step = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10 }[e.key];
    if (step) { active = Math.max(0, Math.min(items.length - 1, active + step)); draw(input.value !== label()); e.preventDefault(); }
    else if (e.key === "Enter") { choose(items[active]); e.preventDefault(); }
    else if (e.key === "Escape") { close(); input.blur(); e.preventDefault(); }
    else if (e.key === "Tab") close();
  });
}

// ------------------------------------------------------------------ upgrades and init
function upgrade(root) {
  if (!(root instanceof Element)) return;
  const all = (sel) => (root.matches(sel) ? [root, ...root.querySelectorAll(sel)] : root.querySelectorAll(sel));
  all("input[type=number]").forEach(upgradeNumber);
  all("table").forEach(upgradeGrid);
  all("select").forEach(upgradeCombo);
  all("p.status, div.panel, span.status").forEach((el) => {
    if (el.dataset.cdtBusy || el.children.length || !/^Loading\b.*…$/.test(el.textContent.trim())) return;
    el.dataset.cdtBusy = "1";
    el.insertAdjacentHTML("afterbegin", '<span class="cdt-busy" aria-hidden="true"><i></i><i></i><i></i></span>');
  });
}

let started = false;
/** Start the SDK on the page: theme, number fields, grids, long lists, tips, closable bars. */
export function init(root = document.body) {
  theme.apply();
  if (started) return;
  started = true;
  document.addEventListener("input", onNumberInput, true);
  document.addEventListener("change", onNumberChange, true);
  document.addEventListener("keydown", onNumberKey, true);
  document.addEventListener("focusin", (e) => { if (e.target.dataset?.cdtNumber) e.target.dataset.cdtFocusValue = e.target.value; });
  document.addEventListener("focusout", (e) => { if (e.target.dataset?.cdtNumber) { if (!e.target.classList.contains("cdt-invalid")) hideTip(); } });
  document.addEventListener("click", (e) => {
    const t = e.target.closest?.("[data-cdt-theme]");
    if (t) theme.apply(t.dataset.cdtTheme, theme.brand);
    const b = e.target.closest?.("[data-cdt-brand]");
    if (b) theme.apply(theme.mode, b.dataset.cdtBrand);
    const x = e.target.closest?.("[data-cdt-close]");
    if (x && !x.onclick) x.closest(".cdt-infobar")?.remove();
    if (pop && !pop.contains(e.target) && !e.target.closest?.(".cdt-funnel")) closePop();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  window.addEventListener("scroll", () => { closePop(); hideTip(); }, { passive: true });
  // Info tips
  let tipEl = null;
  const showInfo = (btn) => {
    tipEl?.remove();
    tipEl = document.createElement("div");
    tipEl.className = "cdt-tip";
    tipEl.innerHTML = `${btn.dataset.cdtTipImg ? `<img src="${esc(btn.dataset.cdtTipImg)}" alt="">` : ""}${esc(btn.dataset.cdtTip)}`;
    document.body.append(tipEl);
    const r = btn.getBoundingClientRect();
    tipEl.style.left = `${Math.max(8, Math.min(r.left - 10, innerWidth - tipEl.offsetWidth - 8))}px`;
    tipEl.style.top = `${r.bottom + 6}px`;
  };
  const hideInfo = () => { tipEl?.remove(); tipEl = null; };
  document.addEventListener("mouseover", (e) => { const b = e.target.closest?.("[data-cdt-tip]"); if (b) showInfo(b); else if (tipEl) hideInfo(); });
  document.addEventListener("focusin", (e) => { if (e.target.dataset?.cdtTip != null) showInfo(e.target); });
  document.addEventListener("focusout", hideInfo);

  upgrade(root);
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) upgrade(n);
  }).observe(root, { childList: true, subtree: true });
}
