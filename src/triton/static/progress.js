// Progress bars that move smoothly, one per cent at a time, instead of jumping from one reported value
// to the next. A bar races to catch up when the work really jumps (20% in a second shows as 20% in
// about a second), and between reports it creeps on at the pace seen so far, never past the next report
// it can expect (`ahead`, e.g. one element's share) and never to 100% before the work says it is done.

const BARS = new Map(); // key -> { shown, target, began, ahead, done, bar, pct, label }
let frame = null;

/**
 * Point the bar `bar` (and the text `pct`, which shows "NN%") at `target` (0..1).
 * `key` keeps the bar's position when its card is drawn again. `ahead` is how far past the last
 * report the bar may creep while waiting (0 to never creep); `done` snaps to the end.
 */
export function smooth(key, bar, target, { pct = null, ahead = 0, done = false, label = (p) => `${p}%` } = {}) {
  let b = BARS.get(key);
  if (!b) {
    b = { shown: 0, target: 0, began: performance.now() };
    BARS.set(key, b);
  }
  if (bar) bar.style.transition = "none"; // moved here, frame by frame
  Object.assign(b, { bar, pct, ahead, done, label, target: Math.max(b.target, done ? 1 : Math.min(target, 1)) });
  if (!frame) frame = requestAnimationFrame(tick);
  paint(b);
}

// Forget a bar (a job closed), so the same key starts from zero next time.
export function forget(prefix) {
  for (const k of [...BARS.keys()]) if (k.startsWith(prefix)) BARS.delete(k);
}

let last = performance.now();
function tick(now) {
  const dt = Math.min((now - last) / 1000, 0.25);
  last = now;
  let moving = false;
  for (const b of BARS.values()) {
    if (!b.bar?.isConnected) continue; // drawn again or closed: picks up where it was next time
    const was = b.shown;
    if (b.target > b.shown) {
      // Catch up: at least 1% per 50 ms, faster the further behind (about a third of the gap per 0.1 s).
      b.shown = Math.min(b.target, b.shown + Math.max(0.2 * dt, (b.target - b.shown) * Math.min(1, 4 * dt)));
    } else if (b.ahead > 0 && !b.done && b.target > 0) {
      // Creep at half the pace so far, up to most of the next expected report.
      const pace = b.target / Math.max(1, (now - b.began) / 1000);
      const cap = Math.min(b.target + b.ahead * 0.9, 0.99);
      b.shown = Math.min(cap, b.shown + pace * 0.5 * dt);
    }
    if (b.shown !== was) paint(b);
    if (b.target > b.shown + 1e-4 || (b.ahead > 0 && !b.done)) moving = true;
  }
  frame = moving ? requestAnimationFrame(tick) : null;
}

function paint(b) {
  const p = Math.floor(b.shown * 100 + 1e-6);
  if (b.bar) {
    b.bar.style.width = `${Math.max(b.shown * 100, 1)}%`;
    // A voyage bar: the ship ties up at the quay once the bar shows 100%.
    b.bar.closest(".voyage")?.classList.toggle("moored", b.shown >= 0.9995);
  }
  if (b.pct) b.pct.textContent = b.label(p);
}

// The big bar as a voyage: a container ship sails along the water with its wake behind it (the
// filled part of the bar) and moors at the quay on the right when the work reaches 100%.
// The ship rides at the end of the bar's <i>, so smooth() drives it like any other bar.
const SHIP = `<svg class="ship" viewBox="0 0 64 26" aria-hidden="true">
  <path class="hull" d="M2 15h58l-5 8H8z"/><path class="boot" d="M5.2 20h52.6l-2 3H8z"/>
  <rect class="bridge" x="6" y="5" width="7" height="10" rx="1"/><rect class="win" x="7" y="7" width="5" height="1.6"/>
  <rect class="funnel" x="8" y="1.5" width="3" height="3.5"/>
  <g class="boxes"><rect x="16" y="10" width="8" height="5" fill="#e0583a"/><rect x="25" y="10" width="8" height="5" fill="#f2b033"/>
  <rect x="34" y="10" width="8" height="5" fill="#2f9e6e"/><rect x="43" y="10" width="8" height="5" fill="#1e8fc0"/>
  <rect x="20" y="5" width="8" height="5" fill="#1e8fc0"/><rect x="29" y="5" width="8" height="5" fill="#e0583a"/>
  <rect x="38" y="5" width="8" height="5" fill="#f2b033"/></g>
  <path class="line" d="M60 14.5Q67 13 74.5 9.5M56 15Q66 14.5 74 10.5"/></svg>`;
export function voyageHtml() {
  return `<div class="bar big voyage"><span class="sea"><i>${SHIP}</i></span><span class="quay" aria-hidden="true"><b></b></span></div>`;
}
