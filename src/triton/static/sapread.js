// Reading SAP2000 results in the browser, for the results film. The file never goes to the site:
// a results export is often hundreds of megabytes, so it is read here as it streams off the disk,
// folded as it goes (largest values per member and case, envelopes kept), and only the compact
// summary is sent. It reads SAP2000's text tables (.s2k, .$2k, .txt), its Excel export (.xlsx) and
// either inside a .zip or .gz. The same folding as triton/sap.py (which the tests hold this to).

const FORCE = { N: 1e-3, KN: 1, MN: 1e3, KGF: 9.80665e-3, TONF: 9.80665, TON: 9.80665, KIP: 4.448222, LB: 4.448222e-3, LBF: 4.448222e-3, T: 9.80665 };
const LENGTH = { MM: 1e-3, CM: 1e-2, M: 1, IN: 0.0254, FT: 0.3048, INCH: 0.0254 };
const MAX_LOAD_ITEMS = 4000;
const MAX_FIELDS = 6;
const MAX_MODES = 12;

export function unitFactor(text) {
  const t = String(text ?? "").trim().toUpperCase().replace(/ /g, "");
  if (!t || ["UNITLESS", "RADIANS", "DEGREES", "SEC", "%"].includes(t)) return null;
  const [num, den = ""] = t.split("/");
  let out = 1;
  for (const [part, sign] of [[num, 1], [den, -1]]) {
    if (!part) continue;
    for (const tok of part.split(/[-*·]/)) {
      const m = /^([A-Z]+)(\d?)$/.exec(tok);
      if (!m) return null;
      const name = m[1], power = +(m[2] || 1);
      const f = name in FORCE && name !== "M" ? FORCE[name] : LENGTH[name];
      if (f == null) return null;
      out *= f ** (power * sign);
    }
  }
  return out;
}

const DIM = {};
["XorR", "X", "Y", "Z", "GlobalX", "GlobalY", "GlobalZ", "U1", "U2", "U3", "t3", "t2", "Thickness", "Thick", "BendThick", "AbsDistA", "AbsDistB", "AbsDist", "Station", "Length"].forEach((k) => (DIM[k] = "L"));
["F1", "F2", "F3", "P", "V2", "V3", "Force", "GlobalFX", "GlobalFY", "GlobalFZ"].forEach((k) => (DIM[k] = "F"));
["M1", "M2", "M3", "T", "GlobalMX", "GlobalMY", "GlobalMZ", "Moment"].forEach((k) => (DIM[k] = "FL"));
["FOverLA", "FOverLB", "F11", "F22", "F12", "FMax", "FMin", "V13", "V23", "VMax"].forEach((k) => (DIM[k] = "F/L"));
["M11", "M22", "M12", "MMax", "MMin"].forEach((k) => (DIM[k] = "F"));
DIM.UnifLoad = "F/L2";

const num = (v) => {
  if (v == null || v === "") return NaN;
  if (typeof v === "number") return v;
  const x = Number(String(v).replace(/,/g, ""));
  return isFinite(x) ? x : NaN;
};
const key = (v) => (typeof v === "number" && Number.isInteger(v) ? String(v) : String(v ?? "").trim());
const isNum = (v) => v === v; // not NaN

class PerCase {
  constructor(width) { this.width = width; this.data = new Map(); }
  row(c, i) {
    let a = this.data.get(c);
    const need = (i + 1) * this.width;
    if (!a) { a = new Float32Array(Math.max(64, (i + 1) * 2) * this.width).fill(NaN); this.data.set(c, a); }
    else if (a.length < need) { const b = new Float32Array(Math.max(need, a.length * 2)).fill(NaN); b.set(a); a = b; this.data.set(c, a); }
    return a;
  }
}

function foldAbs(a, at, vals) {
  for (let k = 0; k < vals.length; k++) {
    const v = vals[k];
    if (v === v) { const o = a[at + k]; if (!(o === o) || Math.abs(v) > Math.abs(o)) a[at + k] = v; }
  }
}

export class Gather {
  constructor() {
    this.f = 1; this.l = 1; this.program = "";
    this.tables = {}; this.ignored = {};
    this.jointIx = new Map(); this.xyz = [];
    this.frameIx = new Map(); this.frames = []; this.frameSect = new Map();
    this.areaIx = new Map(); this.areas = []; this.areaSect = new Map();
    this.sections = {}; this.areaSections = {};
    this.restraints = new Map(); this.springs = new Set();
    this.patterns = {}; this.cases = {}; this.combos = {};
    this.caseIx = new Map(); this.caseTypes = new Map();
    this.loads = {}; this.loadCounts = {};
    this.react = new PerCase(12); this.disp = new PerCase(3); this.fforce = new PerCase(6); this.sforce = new PerCase(5);
    this.design = new Map(); this.base = {}; this.rows = 0;
    this.frameObj = new Map(); this.stress = new PerCase(1); this.modes = new Map(); this.modeIx = new Map();
  }
  joint(n) { const k = key(n); let i = this.jointIx.get(k); if (i == null) { i = this.xyz.length; this.jointIx.set(k, i); this.xyz.push([NaN, NaN, NaN]); } return i; }
  frame(n) { const k = key(n); let i = this.frameIx.get(k); if (i == null) { i = this.frames.length; this.frameIx.set(k, i); this.frames.push([-1, -1]); } return i; }
  area(n) { const k = key(n); let i = this.areaIx.get(k); if (i == null) { i = this.areas.length; this.areaIx.set(k, i); this.areas.push([]); } return i; }
  caseOf(n, kind = "") { const k = key(n); let i = this.caseIx.get(k); if (i == null) { i = this.caseIx.size; this.caseIx.set(k, i); } if (kind && !this.caseTypes.has(i)) this.caseTypes.set(i, kind); return i; }

  handles(table) { return !!handler(String(table).toUpperCase()); }
  add(table, rec, units) {
    const h = handler(String(table).toUpperCase());
    this.rows++;
    if (!h) { this.ignored[table] = (this.ignored[table] || 0) + 1; return; }
    this.tables[table] = (this.tables[table] || 0) + 1;
    const n = units
      ? (k) => { const v = num(rec[k]), f = units[k]; return f != null ? v * f : v; }
      : (k) => { const v = num(rec[k]), d = DIM[k]; if (!d) return v; const F = this.f, L = this.l; return v * (d === "L" ? L : d === "F" ? F : d === "FL" ? F * L : d === "F/L" ? F / L : F / (L * L)); };
    h.call(this, rec, n);
  }

  _program(rec) {
    this.program = `${rec.ProgramName || "SAP2000"} ${rec.Version || ""}`.trim();
    if (rec.CurrUnits) { const p = String(rec.CurrUnits).split(",").map((s) => s.trim().toUpperCase()); this.f = FORCE[p[0]] ?? 1; this.l = LENGTH[p[1]] ?? 1; }
  }
  _coords(rec, n) {
    const i = this.joint(rec.Joint);
    this.xyz[i] = "GlobalX" in rec ? [n("GlobalX"), n("GlobalY"), n("GlobalZ")] : ["XorR" in rec ? n("XorR") : n("X"), n("Y"), n("Z")];
  }
  _connFrame(rec) { this.frames[this.frame(rec.Frame)] = [this.joint(rec.JointI), this.joint(rec.JointJ)]; }
  // "Objects And Elements" tables: the analysis model, element by element (an object meshed into several)
  _elemJoint(rec, n) {
    const i = this.joint(rec.JointElem ?? rec.JointObject);
    if (rec.JointObject != null && rec.JointObject !== "" && !this.jointIx.has(key(rec.JointObject))) this.jointIx.set(key(rec.JointObject), i);
    this.xyz[i] = [n("GlobalX"), n("GlobalY"), n("GlobalZ")];
  }
  _elemFrame(rec) { const i = this.frame(rec.FrameElem); this.frames[i] = [this.joint(rec.ElemJtI), this.joint(rec.ElemJtJ)]; if (rec.FrameObject != null) this.frameObj.set(i, key(rec.FrameObject)); }
  _elemArea(rec) {
    const i = this.area(rec.AreaElem), out = [];
    for (let k = 1; k <= 9; k++) { const v = rec[`ElemJt${k}`]; if (v != null && v !== "") out.push(this.joint(v)); }
    this.areas[i] = out;
  }
  _connArea(rec) {
    const i = this.area(rec.Area);
    const count = rec.NumJoints != null && rec.NumJoints !== "" ? num(rec.NumJoints) || 0 : 0;
    const out = [];
    for (let k = 1; k <= (count || 9); k++) { const v = rec[`Joint${k}`]; if (v != null && v !== "") out.push(this.joint(v)); }
    this.areas[i] = out;
  }
  _frameSect(rec) { this.frameSect.set(this.frame(rec.Frame), String(rec.AnalSect || rec.DesignSect || rec.Section || "")); }
  _sectProps(rec, n) { const name = String(rec.SectionName || rec.Name || ""); if (name) this.sections[name] = { shape: String(rec.Shape || ""), d: n("t3"), b: n("t2"), material: String(rec.Material || "") }; }
  _areaSect(rec) { this.areaSect.set(this.area(rec.Area), String(rec.Section || "")); }
  _areaProps(rec, n) { const name = String(rec.Section || ""); if (name) this.areaSections[name] = { t: "Thickness" in rec ? n("Thickness") : n("Thick"), material: String(rec.Material || "") }; }
  _restraint(rec) { this.restraints.set(this.joint(rec.Joint), ["U1", "U2", "U3", "R1", "R2", "R3"].map((k) => (["yes", "true", "1"].includes(String(rec[k] ?? "").toLowerCase()) ? "1" : "0")).join("")); }
  _spring(rec) { this.springs.add(this.joint(rec.Joint)); }
  _pattern(rec) { this.patterns[key(rec.LoadPat)] = { type: String(rec.DesignType || rec.Type || ""), self: num(rec.SelfWtMult) || 0 }; }
  _caseDef(rec) { this.cases[key(rec.Case)] = { type: String(rec.Type || ""), design: String(rec.DesignType || "") }; }
  _combo(rec) {
    const name = key(rec.ComboName);
    const c = (this.combos[name] ??= { type: "", parts: [] });
    if (rec.ComboType) c.type = String(rec.ComboType);
    if (rec.CaseName != null && rec.CaseName !== "") c.parts.push([key(rec.CaseName), num(rec.ScaleFactor)]);
  }
  keepLoad(pat, kind, item) {
    const k = `${pat}|${kind}`;
    this.loadCounts[k] = (this.loadCounts[k] || 0) + 1;
    const b = ((this.loads[pat] ??= {})[kind] ??= []);
    if (b.length < MAX_LOAD_ITEMS) b.push(item);
  }
  _jointLoad(rec, n) { this.keepLoad(key(rec.LoadPat), "joint", [this.joint(rec.Joint), ...["F1", "F2", "F3"].map((k) => { const v = n(k); return isNum(v) ? v : 0; })]); }
  _frameDist(rec, n) { const a = n("FOverLA"), b = n("FOverLB"); this.keepLoad(key(rec.LoadPat), "frame", [this.frame(rec.Frame), String(rec.Dir || "Gravity"), ((isNum(a) ? a : 0) + (isNum(b) ? b : 0)) / 2]); }
  _framePoint(rec, n) { const f = n("Force"), r = num(rec.RelDist); this.keepLoad(key(rec.LoadPat), "point", [this.frame(rec.Frame), String(rec.Dir || "Gravity"), isNum(f) ? f : 0, isNum(r) ? r : 0.5]); }
  _areaLoad(rec, n) { const v = n("UnifLoad"); this.keepLoad(key(rec.LoadPat), "area", [this.area(rec.Area), String(rec.Dir || "Gravity"), isNum(v) ? v : 0]); }
  // the output case of a results row; a mode of a modal case is a case of its own ("MODAL mode 3"),
  // kept for the first few modes only (null for the others)
  resultsCase(rec) {
    const step = String(rec.StepType || "").toLowerCase();
    if (step === "mode") {
      const m = num(rec.StepNum);
      if (!(m >= 1 && m <= MAX_MODES)) return [null, step];
      const name = `${key(rec.OutputCase)} mode ${m}`;
      const c = this.caseOf(name, "Mode");
      this.modeIx.set(c, m);
      return [c, step];
    }
    return [this.caseOf(rec.OutputCase, String(rec.CaseType || "")), step];
  }
  _period(rec, n) {
    if (String(rec.StepType || "").toLowerCase() !== "mode") return;
    const m = num(rec.StepNum), o = this.modes.get(m) || {};
    if (rec.Period != null) o.period = num(rec.Period);
    for (const k of ["UX", "UY", "UZ", "RX", "RY", "RZ", "SumUX", "SumUY", "SumUZ", "SumRZ"]) if (rec[k] != null) o[k.toLowerCase()] = num(rec[k]);
    o.case = key(rec.OutputCase);
    this.modes.set(m, o);
  }
  _stress(rec, n) {
    const [c] = this.resultsCase(rec);
    if (c == null || this.modeIx.has(c)) return;
    const i = this.frame(rec.FrameElem ?? rec.Frame), a = this.stress.row(c, i), v = Math.abs(n("SVM"));
    if (isNum(v) && (!isNum(a[i]) || v > a[i])) a[i] = v;
  }
  _reaction(rec, n) {
    const [c, step] = this.resultsCase(rec);
    if (c == null || this.modeIx.has(c)) return;
    const i = this.joint(rec.Joint), a = this.react.row(c, i), at = i * 12;
    ["F1", "F2", "F3", "M1", "M2", "M3"].forEach((name, k) => {
      const v = n(name);
      if (!isNum(v)) return;
      if (step !== "min" && (!isNum(a[at + k]) || v > a[at + k])) a[at + k] = v;
      if (step !== "max" && (!isNum(a[at + 6 + k]) || v < a[at + 6 + k])) a[at + 6 + k] = v;
    });
  }
  _displacement(rec, n) { const [c] = this.resultsCase(rec); if (c == null) return; const i = this.joint(rec.Joint); foldAbs(this.disp.row(c, i), i * 3, [n("U1"), n("U2"), n("U3")]); }
  _frameForce(rec, n) { const [c] = this.resultsCase(rec); if (c == null || this.modeIx.has(c)) return; const i = this.frame(rec.FrameElem ?? rec.Frame); foldAbs(this.fforce.row(c, i), i * 6, [n("P"), n("V2"), n("V3"), n("T"), n("M2"), n("M3")]); }
  _shellForce(rec, n) {
    const [c] = this.resultsCase(rec);
    if (c == null || this.modeIx.has(c)) return;
    const i = this.area(rec.AreaElem ?? rec.Area);
    const v13 = "V13" in rec ? n("V13") : n("VMax");
    foldAbs(this.sforce.row(c, i), i * 5, [n("M11"), n("M22"), v13, n("V23"), Math.max(Math.abs(n("F11")), Math.abs(n("F22")))]);
  }
  _design(rec) {
    if (rec.Frame == null || rec.Frame === "") return;
    let best = null;
    for (const [k, v] of Object.entries(rec)) if (k.toLowerCase().endsWith("ratio")) { const x = num(v); if (isNum(x) && (!best || x > best[1])) best = [k, x]; }
    if (!best) return;
    const i = this.frame(rec.Frame), old = this.design.get(i);
    if (!old || best[1] > old.ratio)
      this.design.set(i, { ratio: best[1], what: String(rec.RatioType || best[0]), combo: String(rec.Combo || rec.PMMCombo || rec.DesignCombo || ""), section: String(rec.DesignSect || ""), status: String(rec.Status || "") });
  }
  _base(rec, n) {
    const c = key(rec.OutputCase), vals = ["GlobalFX", "GlobalFY", "GlobalFZ"].map(n), old = this.base[c];
    this.base[c] = old ? vals.map((v, k) => (Math.abs(v) > Math.abs(old[k]) ? v : old[k])) : vals;
  }
}

function handler(t) {
  const G = Gather.prototype;
  if (t === "PROGRAM CONTROL") return G._program;
  if (t === "JOINT COORDINATES") return G._coords;
  if (t === "CONNECTIVITY - FRAME") return G._connFrame;
  if (t === "CONNECTIVITY - AREA" || t === "CONNECTIVITY - SHELL") return G._connArea;
  if (t === "FRAME SECTION ASSIGNMENTS") return G._frameSect;
  if (t.startsWith("FRAME SECTION PROPERTIES 01")) return G._sectProps;
  if (t === "AREA SECTION ASSIGNMENTS") return G._areaSect;
  if (t.startsWith("AREA SECTION PROPERTIES")) return G._areaProps;
  if (t === "JOINT RESTRAINT ASSIGNMENTS") return G._restraint;
  if (t.startsWith("JOINT SPRING ASSIGNMENTS")) return G._spring;
  if (t === "LOAD PATTERN DEFINITIONS") return G._pattern;
  if (t === "LOAD CASE DEFINITIONS") return G._caseDef;
  if (t === "COMBINATION DEFINITIONS") return G._combo;
  if (t === "JOINT LOADS - FORCE") return G._jointLoad;
  if (t === "FRAME LOADS - DISTRIBUTED") return G._frameDist;
  if (t === "FRAME LOADS - POINT") return G._framePoint;
  if (t.startsWith("AREA LOADS - UNIFORM")) return G._areaLoad;
  if (t === "JOINT REACTIONS") return G._reaction;
  if (t === "JOINT DISPLACEMENTS") return G._displacement;
  if (t === "OBJECTS AND ELEMENTS - JOINTS") return G._elemJoint;
  if (t === "OBJECTS AND ELEMENTS - FRAMES") return G._elemFrame;
  if (t === "OBJECTS AND ELEMENTS - AREAS") return G._elemArea;
  if (t === "MODAL PERIODS AND FREQUENCIES" || t === "MODAL PARTICIPATING MASS RATIOS") return G._period;
  if (t === "ELEMENT STRESSES - FRAMES") return G._stress;
  if (t === "ELEMENT FORCES - FRAMES" || t === "FRAME FORCES") return G._frameForce;
  if (t === "ELEMENT FORCES - AREA SHELLS" || t === "ELEMENT FORCES - AREA PLANES") return G._shellForce;
  if (t.includes("DESIGN") && (t.includes("SUMMARY") || t.includes("RATIO"))) return G._design;
  if (t === "BASE REACTIONS") return G._base;
  return null;
}

// --- the text format ----------------------------------------------------------------------------
const PAIR = /(\w+)=("([^"]*)"|\S*)/g;
export function record(line) {
  const out = {};
  if (!line.includes('"')) {
    for (const tok of line.split(/\s+/)) { if (!tok) continue; const e = tok.indexOf("="); if (e > 0) out[tok.slice(0, e)] = tok.slice(e + 1); }
    return out;
  }
  for (const m of line.matchAll(PAIR)) out[m[1]] = m[3] !== undefined ? m[3] : m[2];
  return out;
}

export async function readText(stream, g, onBytes) {
  const reader = stream.pipeThrough(new TextDecoderStream()).getReader();
  let rest = "", table = "", keep = false, pending = "";
  const line = (raw) => {
    let ln = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (pending) { ln = pending + " " + ln.trimStart(); pending = ""; }
    if (ln.endsWith(" _")) { pending = ln.slice(0, -2); return; }
    const s = ln.trim();
    if (!s || s[0] === "$" || s.startsWith("File ")) return;
    const up = s.slice(0, 16).toUpperCase();
    if (up.startsWith("TABLE:")) { table = s.slice(6).trim().replace(/^"|"$/g, "").trim(); keep = g.handles(table); if (!keep) g.ignored[table] ??= 0; return; }
    if (up.startsWith("END TABLE DATA")) { table = ""; keep = false; return; }
    if (keep) g.add(table, record(s));
    else if (table) g.ignored[table] = (g.ignored[table] || 0) + 1;
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    onBytes?.(value.length);
    const text = rest + value;
    let at = 0, nl;
    while ((nl = text.indexOf("\n", at)) >= 0) { line(text.slice(at, nl)); at = nl + 1; }
    rest = text.slice(at);
  }
  if (rest) line(rest);
  if (pending) line("");
}

// --- zip files (an .xlsx is one), read entry by entry from the file without loading it --------
async function bytes(file, from, to) { return new Uint8Array(await file.slice(from, to).arrayBuffer()); }

export async function zipEntries(file) {
  const tailLen = Math.min(file.size, 65557 + 20);
  const tail = await bytes(file, file.size - tailLen, file.size);
  const dv = new DataView(tail.buffer);
  let e = -1;
  for (let i = tail.length - 22; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) { e = i; break; }
  if (e < 0) throw new Error("This is not a zip file (an .xlsx is one): its directory was not found.");
  let count = dv.getUint16(e + 10, true), cdSize = dv.getUint32(e + 12, true), cdOff = dv.getUint32(e + 16, true);
  // ZIP64: the real numbers are in the zip64 end record the locator points to
  if (e >= 20 && dv.getUint32(e - 20, true) === 0x07064b50) {
    const at = Number(dv.getBigUint64(e - 20 + 8, true));
    const z = new DataView((await bytes(file, at, at + 56)).buffer);
    if (z.getUint32(0, true) === 0x06064b50) { count = Number(z.getBigUint64(32, true)); cdSize = Number(z.getBigUint64(40, true)); cdOff = Number(z.getBigUint64(48, true)); }
  }
  const cd = await bytes(file, cdOff, cdOff + cdSize), cv = new DataView(cd.buffer), out = [];
  let p = 0;
  for (let k = 0; k < count && p + 46 <= cd.length; k++) {
    if (cv.getUint32(p, true) !== 0x02014b50) break;
    const method = cv.getUint16(p + 10, true), nl = cv.getUint16(p + 28, true), xl = cv.getUint16(p + 30, true), cl = cv.getUint16(p + 32, true);
    let csize = cv.getUint32(p + 20, true), usize = cv.getUint32(p + 24, true), local = cv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(cd.subarray(p + 46, p + 46 + nl));
    let x = p + 46 + nl;
    const xEnd = x + xl;
    while (x + 4 <= xEnd) {
      const id = cv.getUint16(x, true), sz = cv.getUint16(x + 2, true);
      if (id === 1) {
        let q = x + 4;
        if (usize === 0xffffffff) { usize = Number(cv.getBigUint64(q, true)); q += 8; }
        if (csize === 0xffffffff) { csize = Number(cv.getBigUint64(q, true)); q += 8; }
        if (local === 0xffffffff) { local = Number(cv.getBigUint64(q, true)); q += 8; }
      }
      x += 4 + sz;
    }
    out.push({ name, method, csize, usize, local });
    p = xEnd + cl;
  }
  return out;
}

export async function entryStream(file, en) {
  const h = new DataView((await bytes(file, en.local, en.local + 30)).buffer);
  const start = en.local + 30 + h.getUint16(26, true) + h.getUint16(28, true);
  const raw = file.slice(start, start + en.csize).stream();
  if (en.method === 0) return raw;
  if (en.method !== 8) throw new Error(`A part of the zip is packed in a way this reader does not know (method ${en.method}).`);
  return raw.pipeThrough(new DecompressionStream("deflate-raw"));
}
async function entryText(file, en) { return await new Response(await entryStream(file, en)).text(); }

// --- the Excel export ---------------------------------------------------------------------------
const xmlText = (s) => s.replace(/&(lt|gt|amp|quot|apos|#\d+|#x[0-9a-f]+);/gi, (m, c) =>
  ({ lt: "<", gt: ">", amp: "&", quot: '"', apos: "'" })[c.toLowerCase()] ?? (c[1] === "x" || c[1] === "X" ? String.fromCodePoint(parseInt(c.slice(2), 16)) : String.fromCodePoint(+c.slice(1))));

function colIndex(ref) { let n = 0; for (let i = 0; i < ref.length; i++) { const c = ref.charCodeAt(i); if (c < 65 || c > 90) break; n = n * 26 + (c - 64); } return n - 1; }

async function sharedStrings(file, entries) {
  const en = entries.find((e) => /^xl\/sharedStrings\.xml$/i.test(e.name));
  if (!en) return [];
  const xml = await entryText(file, en), out = [];
  let at = 0;
  for (;;) {
    const a = xml.indexOf("<si", at);
    if (a < 0) break;
    const b = xml.indexOf("</si>", a);
    const si = xml.slice(a, b);
    let s = "", q = 0;
    for (;;) { const t = si.indexOf("<t", q); if (t < 0) break; const g = si.indexOf(">", t); if (si[g - 1] === "/") { q = g; continue; } const e = si.indexOf("</t>", g); s += si.slice(g + 1, e); q = e; }
    out.push(xmlText(s));
    at = b + 5;
  }
  return out;
}

async function sheetList(file, entries) {
  const wb = await entryText(file, entries.find((e) => /^xl\/workbook\.xml$/i.test(e.name)));
  const relsEn = entries.find((e) => /^xl\/_rels\/workbook\.xml\.rels$/i.test(e.name));
  const rels = relsEn ? await entryText(file, relsEn) : "";
  const target = {};
  for (const m of rels.matchAll(/<Relationship\b[^>]*>/g)) {
    const id = /Id="([^"]+)"/.exec(m[0])?.[1], t = /Target="([^"]+)"/.exec(m[0])?.[1];
    if (id && t) target[id] = t.replace(/^\/?(xl\/)?/, "xl/");
  }
  const out = [];
  for (const m of wb.matchAll(/<sheet\b[^>]*>/g)) {
    const name = xmlText(/name="([^"]*)"/.exec(m[0])?.[1] || ""), rid = /r:id="([^"]+)"/.exec(m[0])?.[1];
    const path = target[rid] || `xl/worksheets/sheet${out.length + 1}.xml`;
    const en = entries.find((e) => e.name.toLowerCase() === path.toLowerCase());
    if (en) out.push({ name, en });
  }
  return out;
}

// the rows of one sheet as arrays of values, handed to onRow as they stream; onRow returns false to stop
async function sheetRows(file, en, strings, onRow, onBytes) {
  const reader = (await entryStream(file, en)).pipeThrough(new TextDecoderStream()).getReader();
  let buf = "", stop = false;
  const cell = (c) => {
    const gt = c.indexOf(">");
    const head = c.slice(0, gt);
    const r = /\br="([A-Z]+)\d+"/.exec(head), t = /\bt="(\w+)"/.exec(head)?.[1];
    if (head.endsWith("/")) return [r ? r[1] : null, null];
    let v = null;
    if (t === "inlineStr") { const a = c.indexOf("<t", gt); if (a >= 0) { const b = c.indexOf(">", a), e = c.indexOf("</t>", b); v = xmlText(c.slice(b + 1, e)); } }
    else { const a = c.indexOf("<v>", gt); if (a >= 0) { const e = c.indexOf("</v>", a); const s = c.slice(a + 3, e); v = t === "s" ? strings[+s] : t === "str" || t === "e" ? xmlText(s) : t === "b" ? s === "1" : Number(s); } }
    return [r ? r[1] : null, v];
  };
  const row = (x) => {
    const out = [];
    let at = 0, auto = 0;
    for (;;) {
      const a = x.indexOf("<c", at);
      if (a < 0) break;
      const ch = x[a + 2];
      if (ch !== " " && ch !== ">" && ch !== "/") { at = a + 2; continue; }
      const gt = x.indexOf(">", a);
      let end;
      if (x[gt - 1] === "/") end = gt + 1;
      else end = x.indexOf("</c>", gt) + 4;
      const [ref, v] = cell(x.slice(a, end));
      const col = ref ? colIndex(ref) : auto;
      out[col] = v;
      auto = col + 1;
      at = end;
    }
    for (let i = 0; i < out.length; i++) if (out[i] === undefined) out[i] = null;
    return out;
  };
  while (!stop) {
    const { value, done } = await reader.read();
    if (done) break;
    onBytes?.(value.length);
    buf += value;
    let at = 0;
    for (;;) {
      const a = buf.indexOf("<row", at);
      if (a < 0) { at = buf.length; break; }
      const gt = buf.indexOf(">", a);
      if (gt < 0) { at = a; break; }
      if (buf[gt - 1] === "/") { at = gt + 1; continue; }
      const e = buf.indexOf("</row>", gt);
      if (e < 0) { at = a; break; }
      if (onRow(row(buf.slice(gt + 1, e))) === false) { stop = true; break; }
      at = e + 6;
    }
    buf = buf.slice(at);
  }
  if (stop) await reader.cancel().catch(() => {});
}

export async function readBook(file, g, onProgress) {
  const entries = await zipEntries(file);
  const strings = await sharedStrings(file, entries);
  const sheets = await sheetList(file, entries);
  const total = sheets.reduce((a, s) => a + s.en.csize, 0) || 1;
  let done = 0;
  for (const sh of sheets) {
    let n = 0, table = sh.name, fields = null, units = null, start = 0, keep = true;
    const scale = sh.en.csize / Math.max(1, sh.en.usize);
    await sheetRows(file, sh.en, strings, (r) => {
      n++;
      if (n === 1 && typeof r[0] === "string" && /^TABLE:/i.test(r[0])) { table = r[0].split(":").slice(1).join(":").trim().replace(/^"|"$/g, ""); keep = g.handles(table); start = 1; if (!keep) { g.ignored[table] ??= 0; return false; } return; }
      if (!fields) { if (!keep && !g.handles(table)) { g.ignored[table] ??= 0; return false; } keep = g.handles(table); if (!keep) return false; fields = r.map((c) => (c == null ? "" : String(c).trim())); return; }
      if (units === null) {
        // SAP2000's export: the units always in the row under the field names; elsewhere only if it reads as units
        const looks = start === 1 ? !r.some((c) => typeof c === "number") : !r.slice(0, 3).some((c) => typeof c === "number") && r.some((c) => c != null && unitFactor(c) != null);
        units = {};
        if (looks) { fields.forEach((k, i) => { const f = r[i] != null ? unitFactor(r[i]) : null; if (k && f != null) units[k] = f; }); return; }
      }
      if (!r.some((c) => c != null)) return;
      const rec = {};
      for (let i = 0; i < fields.length; i++) if (fields[i] && r[i] != null) rec[fields[i]] = r[i];
      g.add(table, rec, units);
    }, (b) => { done += b * scale; onProgress?.(done / total, table); });
  }
}

// read a file the person picked: the summary of its model and results
export async function readSap(file, onProgress = () => {}) {
  const name = file.name.toLowerCase();
  // a small file this page made before (the summary, gzipped): taken as it is
  if (/\.(trsap|json)(\.gz)?$/.test(name) || name.endsWith(".trsap")) {
    const raw = name.endsWith(".gz") || name.endsWith(".trsap") ? file.stream().pipeThrough(new DecompressionStream("gzip")) : file.stream();
    const S = JSON.parse(await new Response(raw).text());
    if (!S.joints || !S.frames) throw new Error("That file is not a results summary this page made.");
    return S;
  }
  const g = new Gather();
  const tell = (f, step) => onProgress(Math.min(0.999, f), step, g.rows);
  let seen = 0;
  const byBytes = (size) => (b) => { seen += b; tell(seen / Math.max(1, size), "Reading"); };
  if (/\.(xlsx|xlsm)$/.test(name)) await readBook(file, g, (f, t) => tell(f, `Reading ${t}`));
  else if (name.endsWith(".gz")) await readText(file.stream().pipeThrough(new DecompressionStream("gzip")), g, (b) => { seen += b; tell(Math.min(0.95, seen / (file.size * 8)), "Reading"); });
  else if (name.endsWith(".zip")) {
    const entries = await zipEntries(file);
    if (entries.some((e) => /^xl\/workbook\.xml$/i.test(e.name))) await readBook(file, g, (f, t) => tell(f, `Reading ${t}`));
    else {
      const inner = entries.filter((e) => /\.(s2k|\$2k|txt|xlsx)$/i.test(e.name)).sort((a, b) => b.usize - a.usize)[0];
      if (!inner) throw new Error("The zip holds no SAP2000 tables (.s2k, .$2k, .txt or an Excel export).");
      if (/\.xlsx$/i.test(inner.name)) {
        const blob = await new Response(await entryStream(file, inner)).blob();
        await readBook(new File([blob], inner.name), g, (f, t) => tell(f, `Reading ${t}`));
      } else await readText(await entryStream(file, inner), g, byBytes(inner.usize));
    }
  } else if (/\.xlsb$/.test(name)) throw new Error("A .xlsb workbook cannot be read here. In SAP2000 export the tables to Excel as .xlsx, or as a .s2k text file.");
  else await readText(file.stream(), g, byBytes(file.size));
  tell(0.999, "Putting the summary together");
  return summarise(g);
}

// --- the summary: the same shape as triton/sap.py summarise -------------------------------------
export const isQuake = (name, type = "") => /RESPSPEC|MODHIST|NONMODHIST|SEISM|QUAKE/i.test(type) || /(^|[^A-Z])(EQ[A-Z]?|EX|EY|EZ|SEIS[A-Z]*|QUAKE|RSA)(?![A-Z])/.test(name.toUpperCase());

// Float32 values as base64, for the full results kept with the summary
function b64(arr) {
  const bytes = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
export function fromB64(text) {
  const s = atob(text), b = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
  return new Float32Array(b.buffer);
}
// every output case's results, element by element (NaN where the file has none): joint movements
// (UX UY UZ), support reactions (F1 F2 F3 M1 M2 M3, the larger of max and min), member forces
// (P V2 V3 T M2 M3, the largest), plate forces (M11 M22 V13 V23 Fmax). Mode shapes are left out.
function fullData(g, caseNames, supports, goodF, goodA, nj) {
  const out = { cases: [], disp: {}, react: {}, frames: {}, areas: {} };
  const pickRows = (a, rows, w, stride = w, off = 0) => {
    const o = new Float32Array(rows.length * w).fill(NaN);
    rows.forEach((i, n) => { for (let k = 0; k < w; k++) { const at = i * stride + off + k; if (at < a.length) o[n * w + k] = a[at]; } });
    return o;
  };
  for (const name of caseNames) {
    const c = g.caseIx.get(name);
    if (g.modeIx.has(c)) continue;
    let any = false;
    const d = g.disp.data.get(c);
    if (d) { out.disp[name] = b64(pickRows(d, Array.from({ length: nj }, (_, i) => i), 3)); any = true; }
    const R = g.react.data.get(c);
    if (R && supports.length) {
      const o = new Float32Array(supports.length * 6).fill(NaN);
      supports.forEach((i, n) => { for (let k = 0; k < 6; k++) { const hi = R[i * 12 + k], lo = R[i * 12 + 6 + k]; const H = hi === hi ? hi : NaN, Lo = lo === lo ? lo : NaN; o[n * 6 + k] = !(Lo === Lo) || Math.abs(H) >= Math.abs(Lo) ? H : Lo; } });
      out.react[name] = b64(o); any = true;
    }
    const F = g.fforce.data.get(c);
    if (F) { out.frames[name] = b64(pickRows(F, goodF, 6)); any = true; }
    const A = g.sforce.data.get(c);
    if (A) { out.areas[name] = b64(pickRows(A, goodA, 5)); any = true; }
    if (any) out.cases.push(name);
  }
  return out;
}
const r = (v, d = 3) => (v == null || !isFinite(v) ? null : Math.round(v * 10 ** d) / 10 ** d);
const isService = (name, info = {}) => /SLS|SERV|QP|CHAR|FREQ/.test(`${name} ${info.type || ""} ${info.design || ""}`.toUpperCase());

export function summarise(g) {
  const nj = g.xyz.length;
  const known = g.xyz.map((p) => p.every((v) => isFinite(v)));
  if (!known.some(Boolean)) throw new Error("No joint coordinates were found. Export the Joint Coordinates and Connectivity tables with the results.");
  const caseNames = [...g.caseIx.keys()].sort((a, b) => g.caseIx.get(a) - g.caseIx.get(b));
  const goodF = g.frames.map((f, i) => (f[0] >= 0 && f[1] >= 0 && known[f[0]] && known[f[1]] ? i : -1)).filter((i) => i >= 0);
  const goodA = g.areas.map((js, i) => (js.length >= 3 && js.every((j) => known[j]) ? i : -1)).filter((i) => i >= 0);
  const goodFSet = new Set(goodF), goodASet = new Set(goodA);
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  g.xyz.forEach((p, i) => { if (known[i]) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); } });
  const sectNames = [...new Set([...goodF.map((i) => g.frameSect.get(i) || ""), ...goodA.map((i) => g.areaSect.get(i) || "")])].sort();
  const sectIx = new Map(sectNames.map((s, k) => [s, k]));
  const sectInfo = (s) => g.sections[s] ? { name: s, shape: g.sections[s].shape, d: r(g.sections[s].d), b: r(g.sections[s].b), material: g.sections[s].material }
    : g.areaSections[s] ? { name: s, shape: "Shell", t: r(g.areaSections[s].t), material: g.areaSections[s].material } : { name: s };
  const sup = new Set([...g.restraints.keys(), ...g.springs]);
  for (const a of g.react.data.values()) for (let i = 0; i < Math.min(nj, a.length / 12); i++) if (isFinite(a[i * 12 + 2]) || isFinite(a[i * 12 + 8])) sup.add(i);
  const supports = [...sup].filter((i) => i < nj && known[i]).sort((a, b) => a - b);
  const info = (n) => g.combos[n] || g.cases[n] || {};
  const isMode = (n) => g.modeIx.has(g.caseIx.get(n));
  const isCombo = (n) => n in g.combos || g.caseTypes.get(g.caseIx.get(n)) === "Combination";
  const cases = caseNames.filter((n) => !isMode(n)).map((n) => ({ name: n, kind: isCombo(n) ? "combo" : "case", type: info(n).type || g.caseTypes.get(g.caseIx.get(n)) || "", service: isService(n, info(n)) }));

  const use = new Set(caseNames.filter((n) => isCombo(n) && !isMode(n)).map((n) => g.caseIx.get(n)));
  if (!use.size) caseNames.filter((n) => !isMode(n)).forEach((n) => use.add(g.caseIx.get(n)));
  const react = { supports, cases: [] };
  if (g.react.data.size && supports.length) {
    const best = { down: [-Infinity, null, null], up: [Infinity, null, null], h: [-Infinity, null, null] };
    for (const name of caseNames) {
      const c = g.caseIx.get(name), a = g.react.data.get(c);
      if (!a) continue;
      const at = (i, k) => (i * 12 + k < a.length ? a[i * 12 + k] : NaN);
      const pick = (x, y) => (isFinite(x) ? x : y);
      const big = (x, y) => { const X = isFinite(x) ? x : 0, Y = isFinite(y) ? y : 0; return Math.abs(X) >= Math.abs(Y) ? X : Y; };
      const fz = supports.map((i) => pick(at(i, 2), at(i, 8))), fzmin = supports.map((i) => pick(at(i, 8), at(i, 2)));
      const h = supports.map((i) => Math.hypot(big(at(i, 0), at(i, 6)), big(at(i, 1), at(i, 7))));
      react.cases.push({ case: name, fz: fz.map((v) => r(v, 1)), fz_min: fzmin.map((v) => r(v, 1)), h: h.map((v) => r(v, 1)), sum_fz: r(fz.reduce((s, v) => s + (isFinite(v) ? v : 0), 0), 1) });
      if (use.has(c)) {
        fz.forEach((v, k) => { if (isFinite(v) && v > best.down[0]) best.down = [v, supports[k], name]; });
        fzmin.forEach((v, k) => { if (isFinite(v) && v < best.up[0]) best.up = [v, supports[k], name]; });
        h.forEach((v, k) => { if (v > best.h[0]) best.h = [v, supports[k], name]; });
      }
    }
    react.max = {};
    for (const [k, v] of Object.entries(best)) if (v[2] != null) react.max[k] = { value: r(v[0], 1), joint: v[1], case: v[2] };
  }

  const disp = { cases: [], max: {} };
  if (g.disp.data.size) {
    const scored = [];
    for (const name of caseNames) {
      const a = g.disp.data.get(g.caseIx.get(name));
      if (!a || isMode(name)) continue;
      let hb = 0, hj = 0, vb = 0, vj = 0;
      const u = [];
      for (let i = 0; i < nj; i++) {
        const x = i * 3 < a.length ? a[i * 3] : NaN, y = i * 3 + 1 < a.length ? a[i * 3 + 1] : NaN, z = i * 3 + 2 < a.length ? a[i * 3 + 2] : NaN;
        const X = isFinite(x) ? x : 0, Y = isFinite(y) ? y : 0, Z = isFinite(z) ? z : 0;
        u.push([X, Y, Z]);
        const hz = Math.hypot(X, Y), vz = Math.abs(Z);
        if (hz > hb) { hb = hz; hj = i; }
        if (vz > vb) { vb = vz; vj = i; }
      }
      scored.push({ name, h: hb, hj, v: vb, vj, u });
    }
    if (scored.length) {
      const pick = [];
      for (const k of ["h", "v"]) for (const service of [true, false]) {
        const pool = scored.filter((s) => isService(s.name, info(s.name)) === service);
        if (pool.length) { const top = pool.reduce((a, b) => (b[k] > a[k] ? b : a)); if (!pick.includes(top.name)) pick.push(top.name); }
      }
      // earthquakes (response spectrum or time history) always, for their own shot
      scored.filter((s) => isQuake(s.name, g.caseTypes.get(g.caseIx.get(s.name)) || info(s.name).type)).slice(0, 2).forEach((s) => { if (!pick.includes(s.name)) pick.push(s.name); });
      for (const s of [...scored].sort((a, b) => Math.max(b.h, b.v) - Math.max(a.h, a.v))) { if (pick.length >= MAX_FIELDS) break; if (!pick.includes(s.name)) pick.push(s.name); }
      const by = Object.fromEntries(scored.map((s) => [s.name, s]));
      for (const name of pick.slice(0, MAX_FIELDS + 2)) {
        const s = by[name];
        disp.cases.push({ case: name, u: s.u.map((p) => p.map((v) => r(v, 5))), h: { value: r(s.h, 5), joint: s.hj }, v: { value: r(s.v, 5), joint: s.vj } });
      }
      const hz = scored.reduce((a, b) => (b.h > a.h ? b : a)), vz = scored.reduce((a, b) => (b.v > a.v ? b : a));
      disp.max = { h: { value: r(hz.h, 5), joint: hz.hj, case: hz.name }, v: { value: r(vz.v, 5), joint: vz.vj, case: vz.name } };
    }
  }

  let forces = {};
  const nf = g.frames.length;
  if (g.fforce.data.size && nf) {
    const env = new Float64Array(nf * 4), which = new Int32Array(nf * 4).fill(-1), comp = new Float64Array(nf), tens = new Float64Array(nf);
    const idx = new Map([...g.caseIx].map(([n, c]) => [c, n]));
    for (const name of caseNames) {
      const c = g.caseIx.get(name), a = g.fforce.data.get(c);
      if (!a) continue;
      for (let i = 0; i < nf; i++) {
        const v = (k) => { const x = i * 6 + k < a.length ? a[i * 6 + k] : NaN; return isFinite(x) ? x : 0; };
        const vals = [Math.abs(v(0)), Math.hypot(v(1), v(2)), Math.hypot(v(4), v(5)), Math.abs(v(3))];
        vals.forEach((x, k) => { if (x > env[i * 4 + k]) { env[i * 4 + k] = x; which[i * 4 + k] = c; } });
        comp[i] = Math.min(comp[i], v(0)); tens[i] = Math.max(tens[i], v(0));
      }
    }
    const col = (k) => Array.from({ length: nf }, (_, i) => r(env[i * 4 + k], 1));
    forces = { N: col(0), V: col(1), M: col(2), T: col(3), compression: Array.from(comp, (v) => r(-v, 1) || 0), tension: Array.from(tens, (v) => r(v, 1) || 0),
      case: Object.fromEntries(["N", "V", "M", "T"].map((k, j) => [k, Array.from({ length: nf }, (_, i) => idx.get(which[i * 4 + j]) ?? "")])) };
  }
  let shells = {};
  const na = g.areas.length;
  if (g.sforce.data.size && na) {
    const env = new Float64Array(na * 3);
    for (const a of g.sforce.data.values()) for (let i = 0; i < na; i++) {
      const v = (k) => { const x = i * 5 + k < a.length ? a[i * 5 + k] : NaN; return isFinite(x) ? x : 0; };
      env[i * 3] = Math.max(env[i * 3], Math.abs(v(0))); env[i * 3 + 1] = Math.max(env[i * 3 + 1], Math.abs(v(1))); env[i * 3 + 2] = Math.max(env[i * 3 + 2], Math.hypot(v(2), v(3)));
    }
    const col = (k) => Array.from({ length: na }, (_, i) => r(env[i * 3 + k], 1));
    shells = { M11: col(0), M22: col(1), V: col(2) };
  }
  // the first modes of vibration: each shape scaled so that its largest movement is 1
  const modes = [];
  for (const [c, m] of [...g.modeIx].sort((a, b) => a[1] - b[1])) {
    const a = g.disp.data.get(c), o = g.modes.get(m) || {};
    if (!a) continue;
    let big = 0;
    for (let i = 0; i < Math.min(nj * 3, a.length); i++) if (isFinite(a[i])) big = Math.max(big, Math.abs(a[i]));
    if (!(big > 0)) continue;
    const u = Array.from({ length: nj }, (_, i) => [0, 1, 2].map((k) => { const v = i * 3 + k < a.length ? a[i * 3 + k] : NaN; return isFinite(v) ? r(v / big, 4) : 0; }));
    modes.push({ mode: m, period: r(o.period, 4), ux: r(o.ux, 4), uy: r(o.uy, 4), uz: r(o.uz, 4), rz: r(o.rz, 4), case: o.case || "", u });
  }
  // every mode's period and mass ratios, for the table
  const modal = [...g.modes].sort((a, b) => a[0] - b[0]).map(([m, o]) => ({ mode: m, period: r(o.period, 4), ux: r(o.ux, 4), uy: r(o.uy, 4), uz: r(o.uz, 4), rx: r(o.rx, 4), ry: r(o.ry, 4), rz: r(o.rz, 4),
    sumux: r(o.sumux, 4), sumuy: r(o.sumuy, 4), sumuz: r(o.sumuz, 4) }));
  // von Mises stress in the members, the largest over the output cases (combinations where there are some)
  let stress = {};
  if (g.stress.data.size && nf) {
    const svm = new Float64Array(nf).fill(NaN), which = new Array(nf).fill("");
    const some = [...g.stress.data.keys()].some((c) => use.has(c));
    for (const name of caseNames) {
      const c = g.caseIx.get(name);
      if (some ? !use.has(c) : isMode(name)) continue;
      const a = g.stress.data.get(c);
      if (!a) continue;
      for (let i = 0; i < Math.min(nf, a.length); i++) if (isFinite(a[i]) && !(a[i] <= svm[i])) { svm[i] = a[i]; which[i] = name; }
    }
    stress = { svm: Array.from(svm, (v) => r(v, 1)), case: which, count: Array.from(svm).filter((v) => isFinite(v)).length };
  }
  const design = {};
  for (const [i, d] of g.design) if (goodFSet.has(i)) design[String(i)] = { ...d, ratio: r(d.ratio, 3) };
  const loads = {};
  for (const [pat, kinds] of Object.entries(g.loads)) {
    const out = {};
    for (const [kind, items] of Object.entries(kinds)) {
      if (kind === "joint") out[kind] = items.filter((it) => it[0] < nj && known[it[0]]).map(([j, a, b, c]) => [j, r(a, 2), r(b, 2), r(c, 2)]);
      else if (kind === "frame" || kind === "point") out[kind] = items.filter((it) => goodFSet.has(it[0])).map(([i, ...rest]) => [i, ...rest.map((v) => (typeof v === "number" ? r(v, 3) : v))]);
      else out[kind] = items.filter((it) => goodASet.has(it[0])).map(([i, d, v]) => [i, d, r(v, 3)]);
    }
    const counts = {};
    for (const [k, v] of Object.entries(g.loadCounts)) { const [p, kind] = k.split("|"); if (p === pat) counts[kind] = v; }
    loads[pat] = { items: out, counts };
  }
  const frameNames = [...g.frameIx].sort((a, b) => a[1] - b[1]).filter(([, i]) => goodFSet.has(i)).map(([n]) => n);
  return {
    program: g.program, units: "kN, m",
    bbox: [lo.map((v) => r(v, 3)), hi.map((v) => r(v, 3))],
    joints: g.xyz.map((p, i) => (known[i] ? p.map((v) => r(v, 3)) : null)),
    frames: { ids: goodF, ends: goodF.map((i) => g.frames[i]), section: goodF.map((i) => sectIx.get(g.frameSect.get(i) || "")), names: frameNames },
    areas: { ids: goodA, joints: goodA.map((i) => g.areas[i]), section: goodA.map((i) => sectIx.get(g.areaSect.get(i) || "")) },
    sections: sectNames.map(sectInfo), supports,
    restraints: Object.fromEntries([...g.restraints].filter(([i]) => i < nj).map(([i, v]) => [String(i), v])),
    patterns: Object.entries(g.patterns).map(([name, v]) => ({ name, ...v })),
    combos: Object.entries(g.combos).map(([name, v]) => ({ name, type: v.type, parts: v.parts })),
    cases, loads, reactions: react, displacements: disp, forces, frame_count: nf, shells, area_count: na, design, modes, modal, stress,
    full: fullData(g, caseNames, supports, goodF, goodA, nj),
    base: Object.fromEntries(Object.entries(g.base).map(([k, v]) => [k, v.map((x) => r(x, 1))])),
    tables: g.tables, ignored: g.ignored, rows: g.rows,
  };
}

// --- a model file put together with a results file ------------------------------------------------
// SAP2000 often exports the results of the meshed model (members cut into pieces at every joint) and
// the model itself (with its sections and loads) as two files. mergeModel(S, T) puts the model T's
// sections, load patterns and loads onto the results S: the joints must be the same (within 2 cm);
// a member cut into pieces takes the sections and loads of the member it is part of, a point load
// the piece it falls on. Returns a new summary, with `model` saying how much was matched.
export function mergeModel(S, T) {
  if (!T?.joints || !S?.joints) throw new Error("Both files must hold the joints of the model.");
  const tol = 0.02, cell = (v) => Math.round(v / tol);
  const grid = new Map();
  S.joints.forEach((p, i) => { if (p) { const k = p.map(cell).join(","); if (!grid.has(k)) grid.set(k, i); } });
  const near = (p) => {
    const c = p.map(cell);
    for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) for (let d = -1; d <= 1; d++) {
      const i = grid.get(`${c[0] + a},${c[1] + b},${c[2] + d}`);
      if (i != null && Math.hypot(...S.joints[i].map((v, k) => v - p[k])) <= tol) return i;
    }
    return null;
  };
  const jmap = T.joints.map((p) => (p ? near(p) : null));
  const known = T.joints.filter(Boolean).length, hit = jmap.filter((j) => j != null).length;
  if (!known || hit < 0.9 * known) throw new Error(`Only ${hit} of the ${known} joints of the model are in the results file: they are not the same model.`);
  // each piece in the results: the model member it lies along, and where along it (0..1)
  const P = (j) => S.joints[j];
  const pieces = T.frames.ends.map(() => []);
  const fOf = new Array(S.frames.ends.length).fill(-1);
  const tEnds = T.frames.ends.map(([a, b]) => (jmap[a] != null && jmap[b] != null ? [P(jmap[a]), P(jmap[b])] : null));
  const along = (A, B, X) => {
    const d = B.map((v, k) => v - A[k]), L2 = d[0] ** 2 + d[1] ** 2 + d[2] ** 2;
    if (!(L2 > 1e-9)) return null;
    const t = ((X[0] - A[0]) * d[0] + (X[1] - A[1]) * d[1] + (X[2] - A[2]) * d[2]) / L2;
    const off = Math.hypot(...X.map((v, k) => v - (A[k] + d[k] * t)));
    return off <= tol && t >= -1e-3 && t <= 1 + 1e-3 ? Math.min(1, Math.max(0, t)) : null;
  };
  S.frames.ends.forEach(([a, b], k) => {
    const X = P(a), Y = P(b);
    if (!X || !Y) return;
    for (let m = 0; m < tEnds.length; m++) {
      const e = tEnds[m];
      if (!e) continue;
      const t0 = along(e[0], e[1], X); if (t0 == null) continue;
      const t1 = along(e[0], e[1], Y); if (t1 == null || Math.abs(t1 - t0) < 1e-6) continue;
      fOf[k] = m; pieces[m].push({ k, t0: Math.min(t0, t1), t1: Math.max(t0, t1), flip: t0 > t1 });
      break;
    }
  });
  // the plates: the same corners
  const aKey = (js, map) => js.map((j) => (map ? map[j] : j)).sort((x, y) => x - y).join(",");
  const aOfT = new Map(T.areas.joints.map((js, m) => [aKey(js, jmap), m]));
  const aOf = S.areas.joints.map((js) => aOfT.get(aKey(js)) ?? -1);
  // the sections: the model's, and one with no name for anything not matched
  const sections = T.sections.map((s) => ({ ...s }));
  let blank = -1;
  const none = () => (blank < 0 ? (blank = sections.push({ name: "" }) - 1) : blank);
  const keep = (old, k) => { const s = S.sections[old]; if (!s?.name) return none(); let i = sections.findIndex((q) => q.name === s.name); if (i < 0) i = sections.push({ ...s }) - 1; return i; };
  const fsec = S.frames.section.map((old, k) => (fOf[k] >= 0 && T.frames.section[fOf[k]] != null ? T.frames.section[fOf[k]] : keep(old, k)));
  const asec = S.areas.section.map((old, k) => (aOf[k] >= 0 && T.areas.section[aOf[k]] != null ? T.areas.section[aOf[k]] : keep(old, k)));
  // the loads, onto the results' joints, pieces and plates
  const tF = new Map(T.frames.ids.map((id, m) => [id, m])), tA = new Map(T.areas.ids.map((id, m) => [id, m]));
  const aBack = new Map(); aOf.forEach((m, k) => { if (m >= 0 && !aBack.has(m)) aBack.set(m, k); });
  const loads = { ...(S.loads || {}) };
  let moved = 0, lost = 0;
  for (const [pat, L] of Object.entries(T.loads || {})) {
    const it = L.items || {}, out = {};
    const push = (kind, v) => { (out[kind] ??= []).push(v); moved++; };
    (it.joint || []).forEach(([j, a, b, c]) => (jmap[j] != null ? push("joint", [jmap[j], a, b, c]) : lost++));
    (it.frame || []).forEach(([id, d, w]) => { const m = tF.get(id); if (m == null || !pieces[m].length) { lost++; return; } pieces[m].forEach((q) => push("frame", [S.frames.ids[q.k], d, w])); });
    (it.point || []).forEach(([id, d, f, r0]) => {
      const m = tF.get(id), q = m == null ? null : pieces[m].find((x) => r0 >= x.t0 - 1e-6 && r0 <= x.t1 + 1e-6);
      if (!q) { lost++; return; }
      const loc = (r0 - q.t0) / Math.max(1e-9, q.t1 - q.t0);
      push("point", [S.frames.ids[q.k], d, f, Math.round((q.flip ? 1 - loc : loc) * 1000) / 1000]);
    });
    (it.area || []).forEach(([id, d, w]) => { const m = tA.get(id), k = m == null ? null : aBack.get(m); if (k == null) { lost++; return; } push("area", [S.areas.ids[k], d, w]); });
    loads[pat] = { items: out, counts: L.counts || {} };
  }
  const have = new Set((S.patterns || []).map((p) => p.name));
  const patterns = [...(S.patterns || []), ...(T.patterns || []).filter((p) => !have.has(p.name))];
  const matched = fOf.filter((m) => m >= 0).length;
  return {
    ...S, sections,
    frames: { ...S.frames, section: fsec },
    areas: { ...S.areas, section: asec },
    patterns, loads,
    model: { joints: hit, members: matched, of_members: S.frames.ends.length, plates: aOf.filter((m) => m >= 0).length, of_plates: S.areas.joints.length, loads: moved, lost },
  };
}
