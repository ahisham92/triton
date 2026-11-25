// CDT QuantityUnits and the NumberBox's arithmetic, for the web (CDT.Wpf Controls/QuantityUnits.cs, NumberBox.cs).
// A unit is built from length, force and angle, so compound units work without being listed:
// kN·m, kN*m, kN/m, N/mm², mm2, mm^4. Values are kept in mm, N and rad.

const BASE = {
  // length (mm)
  mm: [1, 1, 0, 0], cm: [10, 1, 0, 0], m: [1000, 1, 0, 0], km: [1e6, 1, 0, 0],
  in: [25.4, 1, 0, 0], ft: [304.8, 1, 0, 0], "'": [304.8, 1, 0, 0], '"': [25.4, 1, 0, 0],
  // force (N)
  N: [1, 0, 1, 0], kN: [1e3, 0, 1, 0], MN: [1e6, 0, 1, 0], kip: [4448.2216152605, 0, 1, 0], lbf: [4.4482216152605, 0, 1, 0],
  t: [9806.65, 0, 1, 0], tf: [9806.65, 0, 1, 0],
  // stress (N/mm²)
  Pa: [1e-6, -2, 1, 0], kPa: [1e-3, -2, 1, 0], MPa: [1, -2, 1, 0], GPa: [1e3, -2, 1, 0],
  psi: [0.00689475729, -2, 1, 0], ksi: [6.89475729, -2, 1, 0], psf: [4.78802589e-5, -2, 1, 0], ksf: [0.0478802589, -2, 1, 0],
  // angle (rad)
  rad: [1, 0, 0, 1], deg: [Math.PI / 180, 0, 0, 1], "°": [Math.PI / 180, 0, 0, 1],
};
// Spellings Triton's forms use for the same units.
const ALIAS = { kNm: "kN·m", "kN m": "kN·m", "kNm/m": "kN·m/m", "kN m/m": "kN·m/m", Nmm: "N·mm", "N.mm": "N·mm" };

/** Parse a unit such as "kN·m/m" into [factor, L, F, A], or null when it is not a quantity (years, %, USD). */
export function parseUnit(text) {
  let s = String(text ?? "").trim();
  if (!s) return null;
  s = ALIAS[s] || s;
  s = s.replace(/²/g, "^2").replace(/³/g, "^3").replace(/⁴/g, "^4").replace(/\s*[·*.]\s*|\s+/g, "·");
  const out = [1, 0, 0, 0];
  let sign = 1;
  for (const part of s.split(/(\/)/)) {
    if (part === "/") { sign = -1; continue; }
    for (const tok of part.split("·").filter(Boolean)) {
      const m = tok.match(/^([A-Za-z°'"]+)\^?(-?\d+)?$/);
      if (!m) return null;
      const u = BASE[m[1]];
      if (!u) return null;
      const p = sign * (m[2] ? Number(m[2]) : 1);
      out[0] *= u[0] ** p;
      for (let i = 1; i < 4; i++) out[i] += u[i] * p;
    }
  }
  return out;
}

export const compatible = (a, b) => !!a && !!b && a[1] === b[1] && a[2] === b[2] && a[3] === b[3];

/** Convert value from one unit to another; NaN when they do not match. */
export function convert(value, from, to) {
  const a = parseUnit(from), b = parseUnit(to);
  return compatible(a, b) ? (value * a[0]) / b[0] : NaN;
}

/** Register a unit at start-up, e.g. register("tf", "9.80665 kN"). */
export function register(symbol, definition) {
  const m = String(definition).trim().match(/^([-+\d.eE]+)\s*(.+)$/);
  const u = m && parseUnit(m[2]);
  if (!u) throw new Error(`Cannot read the unit "${definition}"`);
  BASE[symbol] = [Number(m[1]) * u[0], u[1], u[2], u[3]];
}

/**
 * Evaluate what was typed in a number field: arithmetic (+ - * / ^, brackets) and, when the field has a
 * unit, a unit after the number or after the whole expression ("250 mm", "0.25 m", "(2*3) ft", "1' 6\"").
 * Commas are refused with a message rather than misread. Returns { value } or { error }.
 */
export function evaluate(text, unit = "") {
  const src = String(text ?? "").trim();
  if (!src) return { value: null };
  if (/,/.test(src)) return { error: "Use a point for decimals, not a comma" };
  const target = parseUnit(unit);
  // feet and inches: 1' 6"
  const fi = src.match(/^(-?\d+(?:\.\d+)?)\s*'\s*(?:(\d+(?:\.\d+)?)\s*")?$/);
  if (fi && target && target[1] === 1 && target[2] === 0) {
    return { value: (Number(fi[1]) * 304.8 + (fi[2] ? Number(fi[2]) * 25.4 : 0)) / target[0] };
  }
  const toks = [];
  const re = /\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)|([A-Za-z°'"][A-Za-z°'"²³⁴]*(?:\^-?\d+)?(?:[·/][A-Za-z][A-Za-z²³⁴]*(?:\^-?\d+)?)*)|([-+*/^()]))/y;
  let m;
  re.lastIndex = 0;
  while (re.lastIndex < src.length) {
    const at = re.lastIndex;
    m = re.exec(src);
    if (!m || m.index !== at) {
      if (/^\s*$/.test(src.slice(at))) break;
      return { error: `Cannot read "${src.slice(at).trim()}"` };
    }
    if (m[1] !== undefined) toks.push({ n: Number(m[1]) });
    else if (m[2] !== undefined) toks.push({ u: m[2] });
    else toks.push({ o: m[3] });
  }
  let i = 0;
  const peek = () => toks[i];
  const unitFactor = (u) => {
    const pu = parseUnit(u);
    if (!pu) throw new Error(`"${u}" is not a unit`);
    if (!target) throw new Error(`This field takes no unit`);
    if (!compatible(pu, target)) throw new Error(`${u} does not fit ${unit}`);
    return pu[0] / target[0];
  };
  const primary = () => {
    const t = toks[i++];
    if (!t) throw new Error("Unfinished");
    if (t.o === "(") {
      const v = expr();
      if (toks[i++]?.o !== ")") throw new Error("A bracket is not closed");
      return peek()?.u ? v * unitFactor(toks[i++].u) : v;
    }
    if (t.o === "-") return -power();
    if (t.o === "+") return power();
    if (t.n !== undefined) return peek()?.u ? t.n * unitFactor(toks[i++].u) : t.n;
    throw new Error(`Cannot read "${t.o ?? t.u}"`);
  };
  const power = () => {
    const b = primary();
    if (peek()?.o === "^") { i++; return b ** power(); }
    return b;
  };
  const term = () => {
    let v = power();
    while (peek()?.o === "*" || peek()?.o === "/") {
      const op = toks[i++].o, r = power();
      v = op === "*" ? v * r : v / r;
    }
    return v;
  };
  const expr = () => {
    let v = term();
    while (peek()?.o === "+" || peek()?.o === "-") {
      const op = toks[i++].o, r = term();
      v = op === "+" ? v + r : v - r;
    }
    return v;
  };
  try {
    let v = expr();
    // a unit after the whole expression applies to all of it: (2*3) ft
    if (peek()?.u) v *= unitFactor(toks[i++].u);
    if (i < toks.length) throw new Error(`Cannot read "${toks[i].o ?? toks[i].u ?? toks[i].n}"`);
    if (!Number.isFinite(v)) throw new Error("Not a finite number");
    return { value: Math.round(v * 1e9) / 1e9 };
  } catch (e) {
    return { error: e.message };
  }
}

/** True when the text is already a plain number the field would take as it is. */
export const isPlain = (text) => /^\s*-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$/.test(String(text ?? ""));
