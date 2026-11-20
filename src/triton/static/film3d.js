// The results film's 3D picture: WebGL with sun, shadows, sky and a see-through sea, in the way
// TritoMoor draws its berths (its renderer, carried over and made general: the film builds the
// triangles, this only lights and draws them). World axes are the model's: X, Y on plan, Z up.

const HEAD = `#ifdef GL_FRAGMENT_PRECISION_HIGH
  precision highp float;
  #else
  precision mediump float;
  #endif
  `;
const VS = `attribute vec3 aPos; attribute vec3 aNrm; attribute vec3 aCol;
  uniform mat4 uVP; uniform mat4 uLVP; varying vec3 vN; varying vec3 vC; varying vec3 vW; varying vec4 vL;
  void main(){ vN=aNrm; vC=aCol; vW=aPos; vL=uLVP*vec4(aPos,1.0); gl_Position=uVP*vec4(aPos,1.0); }`;
const VS_DEPTH = `attribute vec3 aPos; uniform mat4 uVP; void main(){ gl_Position=uVP*vec4(aPos,1.0); }`;
const SHADOW = `varying vec4 vL; uniform sampler2D uShadow; uniform float uShadowOn; uniform float uTexel;
  float sunlit(float bias){
    if(uShadowOn<0.5) return 1.0;
    vec3 q=vL.xyz/vL.w*0.5+0.5;
    if(q.x<0.0||q.x>1.0||q.y<0.0||q.y>1.0||q.z>1.0) return 1.0;
    float s=0.0;
    for(int i=-1;i<=1;i++) for(int j=-1;j<=1;j++){
      float d=texture2D(uShadow,q.xy+vec2(float(i),float(j))*uTexel).r;
      s+=(q.z-bias>d)?0.0:1.0;
    }
    return s/9.0;
  }
  `;
// solid things: lit by the sun and the sky, tinted green-blue under the water line, fading into haze
const FS = HEAD + SHADOW + `varying vec3 vN; varying vec3 vC; varying vec3 vW; uniform float uOpac;
  uniform vec3 uSun; uniform vec3 uEye; uniform vec3 uSky; uniform float uFog; uniform float uUnder; uniform float uWL; uniform float uGlow;
  void main(){
    vec3 n=normalize(vN); if(!gl_FrontFacing) n=-n;
    float nl=dot(n,uSun);
    float lit=sunlit(0.0012+0.004*(1.0-clamp(nl,0.0,1.0)));
    float d=max(nl,0.0)*lit; float hemi=0.5+0.5*n.z;
    vec3 col=vC*(0.30+0.30*hemi)+vC*vec3(1.0,0.97,0.90)*(0.66*d);
    vec3 v=normalize(uEye-vW); vec3 h=normalize(uSun+v);
    col+=0.10*lit*pow(max(dot(n,h),0.0),28.0);
    col=mix(col,vC,uGlow);
    if(vW.z<uWL){ float k=clamp((uWL-vW.z)/14.0,0.0,1.0); col=mix(col,vec3(0.05,0.24,0.29),(0.30+0.40*k)*uUnder); }
    float fog=clamp(length(uEye-vW)/uFog,0.0,1.0);
    col=mix(col,uSky,fog*fog*0.85);
    gl_FragColor=vec4(col,uOpac);
  }`;
// the sea: small moving ripples, the sky reflected at grazing angles, sun glitter, see-through by uAlpha
const VSW = `attribute vec3 aPos; attribute vec3 aNrm; attribute vec3 aCol;
  uniform mat4 uVP; uniform mat4 uLVP; uniform float uTime;
  varying vec3 vN; varying vec3 vC; varying vec3 vW; varying vec4 vL;
  void main(){ vec3 p=aPos; p.z+=0.12*sin(p.x*0.09+uTime*0.8)*cos(p.y*0.07+uTime*0.6);
    vN=aNrm; vC=aCol; vW=p; vL=uLVP*vec4(p,1.0); gl_Position=uVP*vec4(p,1.0); }`;
const FSW = HEAD + SHADOW + `varying vec3 vN; varying vec3 vC; varying vec3 vW;
  uniform vec3 uSun; uniform vec3 uEye; uniform vec3 uSky; uniform float uFog; uniform float uTime; uniform float uAlpha;
  void main(){
    vec3 p=vW; float t=uTime;
    float gx = 0.55*cos(p.x*0.11+t*0.9) + 0.35*cos((p.x+p.y)*0.23+t*1.4) + 0.25*cos((p.x-0.6*p.y)*0.51+t*2.1);
    float gy = 0.55*cos(p.y*0.13+t*0.8) + 0.35*cos((p.x+p.y)*0.23+t*1.4) + 0.20*cos((0.4*p.x-p.y)*0.61+t*1.9);
    float rx = 0.25*cos(p.x*1.7+p.y*0.6+t*3.1) + 0.18*cos(p.x*2.9-p.y*1.3+t*4.3);
    float ry = 0.25*cos(p.y*1.9-p.x*0.5+t*2.7) + 0.18*cos(p.y*3.3+p.x*1.1+t*3.9);
    float fade = clamp(length(uEye-p)/400.0,0.0,1.0);
    vec3 n=normalize(vec3(-(gx*0.16+rx*0.10*(1.0-fade)),-(gy*0.16+ry*0.10*(1.0-fade)),1.0));
    vec3 v=normalize(uEye-p);
    float fres=0.10+0.70*pow(1.0-max(dot(n,v),0.0),4.0);
    vec3 deep=mix(vec3(0.05,0.30,0.36),vec3(0.09,0.40,0.44),0.5+0.5*sin(p.x*0.013+p.y*0.021));
    vec3 col=mix(deep,uSky*0.95,fres);
    vec3 h=normalize(uSun+v);
    float lit=sunlit(0.002);
    col*=0.75+0.25*lit;
    col+=vec3(1.0,0.96,0.88)*pow(max(dot(n,h),0.0),220.0)*1.2*lit;
    float fog=clamp(length(uEye-p)/uFog,0.0,1.0);
    col=mix(col,uSky,fog*fog*0.9);
    gl_FragColor=vec4(col,clamp(uAlpha*(0.75+0.22*fres),0.05,0.99));
  }`;
const VS_SKY = `attribute vec3 aPos; varying float vY; void main(){ vY=aPos.y; gl_Position=vec4(aPos.xy,0.9999,1.0); }`;
const FS_SKY = HEAD + `varying float vY; uniform vec3 uSky; uniform float uHorizon;
  void main(){ float t=clamp((vY-uHorizon)/(1.0-uHorizon),0.0,1.0);
    vec3 zenith=vec3(0.30,0.52,0.78); vec3 col=mix(uSky,zenith,pow(t,0.6));
    col=mix(col,vec3(0.93,0.95,0.96),0.55*exp(-t*9.0));
    if(vY<uHorizon) col=uSky*0.98;
    gl_FragColor=vec4(col,1.0); }`;
const SKY = [0.8, 0.88, 0.93];

function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function ortho(l, r, b, t, n, f) {
  return [2 / (r - l), 0, 0, 0, 0, 2 / (t - b), 0, 0, 0, 0, -2 / (f - n), 0, -(r + l) / (r - l), -(t + b) / (t - b), -(f + n) / (f - n), 1];
}
function lookAt(e, c, up) {
  let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2];
  let l = Math.hypot(zx, zy, zz); zx /= l; zy /= l; zz /= l;
  let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
  l = Math.hypot(xx, xy, xz); xx /= l; xy /= l; xz /= l;
  const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
  return [xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0,
    -(xx * e[0] + xy * e[1] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1];
}
function mul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
    o[c * 4 + r] = s;
  }
  return o;
}

export const hex = (h) => [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255];

// Triangles with a normal and a colour each, built up shape by shape.
export class Mesh {
  constructor() { this.p = []; this.n = []; this.c = []; }
  tri(a, b, c, col) {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2], vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
    for (const q of [a, b, c]) { this.p.push(q[0], q[1], q[2]); this.n.push(nx, ny, nz); this.c.push(col[0], col[1], col[2]); }
  }
  quad(a, b, c, d, col) { this.tri(a, b, c, col); this.tri(a, c, d, col); }
  // a flat panel seen from both sides
  panel(pts, col) { for (let i = 1; i + 1 < pts.length; i++) { this.tri(pts[0], pts[i], pts[i + 1], col); this.tri(pts[0], pts[i + 1], pts[i], col); } }
  // a box in a frame given by origin o, unit vectors ex, ey on plan; z0..z1 up
  box(o, ex, ey, lx, ly, z0, z1, col, colTop) {
    const P = (s, t, z) => [o[0] + ex[0] * s + ey[0] * t, o[1] + ex[1] * s + ey[1] * t, z];
    const hx = lx / 2, hy = ly / 2;
    const b = [P(-hx, -hy, z0), P(hx, -hy, z0), P(hx, hy, z0), P(-hx, hy, z0)];
    const t = [P(-hx, -hy, z1), P(hx, -hy, z1), P(hx, hy, z1), P(-hx, hy, z1)];
    for (let i = 0; i < 4; i++) { const j = (i + 1) % 4; this.quad(b[i], b[j], t[j], t[i], col); }
    this.quad(t[0], t[1], t[2], t[3], colTop || col);
    this.quad(b[3], b[2], b[1], b[0], col);
  }
  // a member from a to b: round (seg sides) or a w x h box, its depth h upright where it can be
  member(a, b, w, h, col, seg = 0) {
    const tx = b[0] - a[0], ty = b[1] - a[1], tz = b[2] - a[2], tl = Math.hypot(tx, ty, tz);
    if (!(tl > 1e-6)) return;
    const t = [tx / tl, ty / tl, tz / tl], u0 = Math.abs(t[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1];
    let q = [t[1] * u0[2] - t[2] * u0[1], t[2] * u0[0] - t[0] * u0[2], t[0] * u0[1] - t[1] * u0[0]];
    const ql = Math.hypot(...q); q = q.map((v) => v / ql);
    const p = [q[1] * t[2] - q[2] * t[1], q[2] * t[0] - q[0] * t[2], q[0] * t[1] - q[1] * t[0]]; // up-ish
    const ring = [];
    const n = seg || 4;
    for (let k = 0; k < n; k++) {
      let cu, cq;
      if (seg) { const an = (k / n) * 2 * Math.PI; cu = Math.cos(an) * h / 2; cq = Math.sin(an) * w / 2; }
      else { cu = (k === 0 || k === 3 ? 1 : -1) * h / 2; cq = (k < 2 ? 1 : -1) * w / 2; }
      ring.push([p[0] * cu + q[0] * cq, p[1] * cu + q[1] * cq, p[2] * cu + q[2] * cq]);
    }
    const at = (c, r) => [c[0] + r[0], c[1] + r[1], c[2] + r[2]];
    for (let k = 0; k < n; k++) {
      const k2 = (k + 1) % n;
      this.quad(at(a, ring[k]), at(a, ring[k2]), at(b, ring[k2]), at(b, ring[k]), col);
    }
    for (let k = 1; k + 1 < n; k++) { this.tri(at(b, ring[0]), at(b, ring[k]), at(b, ring[k + 1]), col); this.tri(at(a, ring[0]), at(a, ring[k + 1]), at(a, ring[k]), col); }
  }
  // frustum (cone or cylinder) from a to b, radius r0 at a and r1 at b
  frustum(a, b, r0, r1, col, seg = 12) {
    const tx = b[0] - a[0], ty = b[1] - a[1], tz = b[2] - a[2], tl = Math.hypot(tx, ty, tz) || 1;
    const t = [tx / tl, ty / tl, tz / tl], u0 = Math.abs(t[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1];
    let p = [t[1] * u0[2] - t[2] * u0[1], t[2] * u0[0] - t[0] * u0[2], t[0] * u0[1] - t[1] * u0[0]];
    const pl = Math.hypot(...p); p = p.map((v) => v / pl);
    const q = [t[1] * p[2] - t[2] * p[1], t[2] * p[0] - t[0] * p[2], t[0] * p[1] - t[1] * p[0]];
    const at = (c, r, an) => [0, 1, 2].map((k) => c[k] + (p[k] * Math.cos(an) + q[k] * Math.sin(an)) * r);
    for (let i = 0; i < seg; i++) {
      const a1 = (i / seg) * 2 * Math.PI, a2 = ((i + 1) / seg) * 2 * Math.PI;
      this.quad(at(a, r0, a1), at(a, r0, a2), at(b, r1, a2), at(b, r1, a1), col);
      if (r1 > 0) this.tri(b, at(b, r1, a1), at(b, r1, a2), col);
      if (r0 > 0) this.tri(a, at(a, r0, a2), at(a, r0, a1), col);
    }
  }
  // an arrow from tail to tip: shaft and head, sized by its length
  arrow(tail, tip, r, col) {
    const d = [tip[0] - tail[0], tip[1] - tail[1], tip[2] - tail[2]], L = Math.hypot(...d);
    if (!(L > 1e-6)) return;
    const hl = Math.min(L * 0.45, r * 5), k = (L - hl) / L, neck = [tail[0] + d[0] * k, tail[1] + d[1] * k, tail[2] + d[2] * k];
    this.frustum(tail, neck, r, r, col, 8);
    this.frustum(neck, tip, r * 2.4, 0, col, 10);
  }
}

// triangles of a simple polygon [[x, y], ...], by clipping ears: [[i, j, k], ...]
export function earclip(poly) {
  const n = poly.length, out = []; if (n < 3) return out;
  let area = 0; for (let i = 0; i < n; i++) { const a = poly[i], b = poly[(i + 1) % n]; area += a[0] * b[1] - b[0] * a[1]; }
  const idx = []; for (let i = 0; i < n; i++) idx.push(area > 0 ? i : n - 1 - i);
  const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const inside = (p, a, b, c) => cross(a, b, p) >= 0 && cross(b, c, p) >= 0 && cross(c, a, p) >= 0;
  let guard = 0;
  while (idx.length > 3 && guard++ < 10000) {
    let cut = false;
    for (let i = 0; i < idx.length; i++) {
      const ia = idx[(i + idx.length - 1) % idx.length], ib = idx[i], ic = idx[(i + 1) % idx.length], a = poly[ia], b = poly[ib], c = poly[ic];
      if (cross(a, b, c) <= 1e-9) continue;
      if (idx.some((j) => j !== ia && j !== ib && j !== ic && inside(poly[j], a, b, c))) continue;
      out.push([ia, ib, ic]); idx.splice(i, 1); cut = true; break;
    }
    if (!cut) break;
  }
  if (idx.length === 3) out.push([idx[0], idx[1], idx[2]]);
  return out;
}

// The surroundings: land at its level with a slope down to the seabed, and rock breakwaters
// (a crest of the width given, armour slopes of 1 in 1.5 each side, in blocks so the stone reads).
export function surroundings(m, site) {
  const bed = site.seabed, landC = hex("#B9B2A0"), edgeC = hex("#8B8679"), rockC = hex("#7F7A6E"), rockD = hex("#6A655A"), rockT = hex("#8E897D");
  (site.land || []).forEach((L) => {
    const poly = L.pts, zl = L.level;
    if (!poly || poly.length < 3) return;
    earclip(poly).forEach(([i, j, k]) => m.panel([[poly[i][0], poly[i][1], zl], [poly[j][0], poly[j][1], zl], [poly[k][0], poly[k][1], zl]], landC));
    let area = 0; poly.forEach((q, i) => { const r = poly[(i + 1) % poly.length]; area += q[0] * r[1] - r[0] * q[1]; });
    const sg = area > 0 ? 1 : -1, run = Math.max(1, zl - bed) * 1.5;
    poly.forEach((q, i) => {
      if (L.open && i === poly.length - 1) return;
      const r = poly[(i + 1) % poly.length], dx = r[0] - q[0], dy = r[1] - q[1], len = Math.hypot(dx, dy); if (len < 0.01) return;
      const nx = (sg * dy) / len, ny = (-sg * dx) / len;
      m.panel([[q[0], q[1], zl], [r[0], r[1], zl], [r[0] + nx * run, r[1] + ny * run, bed], [q[0] + nx * run, q[1] + ny * run, bed]], edgeC);
    });
  });
  (site.breakwaters || []).forEach((R) => {
    const pts = R.pts || [], w = (R.width || 12) / 2, top = R.crest != null ? R.crest : 5, run = 1.5 * (top - bed);
    for (let i = 0; i + 1 < pts.length; i++) {
      const a = pts[i], b = pts[i + 1], dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy); if (len < 0.01) continue;
      const ux = dx / len, uy = dy / len, nx = -uy, ny = ux, n = Math.max(1, Math.round(len / 9));
      for (let k = 0; k < n; k++) {
        const t0 = k / n, t1 = (k + 1) / n, zt = top + 0.3 * Math.sin(k * 12.9898 + i * 4.1);
        const p0 = [a[0] + dx * t0, a[1] + dy * t0], p1 = [a[0] + dx * t1, a[1] + dy * t1], col = k % 2 ? rockC : rockT;
        const c = (p, sd, z, o) => [p[0] + nx * sd * o, p[1] + ny * sd * o, z];
        m.panel([c(p0, 1, zt, w), c(p1, 1, zt, w), c(p1, -1, zt, w), c(p0, -1, zt, w)], col);
        m.panel([c(p0, 1, zt, w), c(p1, 1, zt, w), c(p1, 1, bed, w + run), c(p0, 1, bed, w + run)], k % 2 ? rockD : rockC);
        m.panel([c(p0, -1, zt, w), c(p1, -1, zt, w), c(p1, -1, bed, w + run), c(p0, -1, bed, w + run)], k % 2 ? rockC : rockD);
      }
      [[a, -1, i === 0], [b, 1, i === pts.length - 2]].forEach(([e, sd, free]) => {
        if (!free) return;
        for (let q = 0; q < 8; q++) {
          const a0 = -Math.PI / 2 + (q * Math.PI) / 8, a1 = a0 + Math.PI / 8;
          const d0 = [sd * ux * Math.cos(a0) + nx * Math.sin(a0), sd * uy * Math.cos(a0) + ny * Math.sin(a0)], d1 = [sd * ux * Math.cos(a1) + nx * Math.sin(a1), sd * uy * Math.cos(a1) + ny * Math.sin(a1)];
          const T0 = [e[0] + d0[0] * w, e[1] + d0[1] * w, top], T1 = [e[0] + d1[0] * w, e[1] + d1[1] * w, top];
          m.panel([[e[0], e[1], top], T0, T1], rockT);
          const G0 = [e[0] + d0[0] * (w + run), e[1] + d0[1] * (w + run), bed], G1 = [e[0] + d1[0] * (w + run), e[1] + d1[1] * (w + run), bed];
          m.panel([T0, T1, G1, G0], q % 2 ? rockC : rockD);
        }
      });
    }
  });
}

// One canvas drawn with WebGL. setStatic() takes what does not change between frames (the
// surroundings, the seabed, the sea); draw() takes the frame's own meshes and the camera.
export class Film3D {
  constructor(canvas) {
    this.canvas = canvas;
    this.failed = "";
    this.t0 = performance.now();
    this.shadowsOn = true;
    try {
      const gl = canvas.getContext("webgl", { antialias: true, preserveDrawingBuffer: true }) || canvas.getContext("experimental-webgl", { preserveDrawingBuffer: true });
      if (!gl) throw new Error("WebGL is not available in this browser.");
      this.gl = gl;
      this.progs = { solid: this.program(FS, VS), water: this.program(FSW, VSW) };
      const sp = gl.createProgram();
      gl.attachShader(sp, this.compile(VS_SKY, gl.VERTEX_SHADER)); gl.attachShader(sp, this.compile(FS_SKY, gl.FRAGMENT_SHADER));
      gl.bindAttribLocation(sp, 0, "aPos"); gl.linkProgram(sp);
      this.progs.sky = { p: sp, uSky: gl.getUniformLocation(sp, "uSky"), uHorizon: gl.getUniformLocation(sp, "uHorizon"), buf: gl.createBuffer() };
      gl.bindBuffer(gl.ARRAY_BUFFER, this.progs.sky.buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, -1, 0, 1, 1, 0, -1, 1, 0]), gl.STATIC_DRAW);
      try { this.initShadow(); } catch (e) { this.shadow = null; }
    } catch (e) {
      this.failed = e.message;
      this.gl = null;
    }
    this.static = null;
    this.water = null;
    this.dyn = [];
  }
  available() { return !!this.gl; }
  compile(src, type) {
    const gl = this.gl, s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  program(fs, vs) {
    const gl = this.gl, p = gl.createProgram();
    gl.attachShader(p, this.compile(vs, gl.VERTEX_SHADER)); gl.attachShader(p, this.compile(fs, gl.FRAGMENT_SHADER));
    gl.bindAttribLocation(p, 0, "aPos"); gl.bindAttribLocation(p, 1, "aNrm"); gl.bindAttribLocation(p, 2, "aCol");
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = (n) => gl.getUniformLocation(p, n);
    const out = { p };
    ["uLVP", "uShadow", "uShadowOn", "uTexel", "uVP", "uSun", "uEye", "uSky", "uFog", "uTime", "uAlpha", "uOpac", "uUnder", "uWL", "uGlow"].forEach((k) => (out[k] = u(k)));
    return out;
  }
  initShadow() {
    const gl = this.gl;
    const ext = gl.getExtension("WEBGL_depth_texture") || gl.getExtension("WEBKIT_WEBGL_depth_texture");
    if (!ext) return;
    const size = Math.min(4096, gl.getParameter(gl.MAX_TEXTURE_SIZE) || 2048) >= 4096 ? 4096 : 2048;
    const dp = gl.createProgram();
    gl.attachShader(dp, this.compile(VS_DEPTH, gl.VERTEX_SHADER)); gl.attachShader(dp, this.compile(HEAD + "void main(){ gl_FragColor=vec4(1.0); }", gl.FRAGMENT_SHADER));
    gl.bindAttribLocation(dp, 0, "aPos"); gl.linkProgram(dp);
    if (!gl.getProgramParameter(dp, gl.LINK_STATUS)) return;
    const depth = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, depth);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT, size, size, 0, gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
    [gl.TEXTURE_MAG_FILTER, gl.TEXTURE_MIN_FILTER].forEach((k) => gl.texParameteri(gl.TEXTURE_2D, k, gl.NEAREST));
    [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T].forEach((k) => gl.texParameteri(gl.TEXTURE_2D, k, gl.CLAMP_TO_EDGE));
    const colour = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, colour);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, size, size, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    [gl.TEXTURE_MAG_FILTER, gl.TEXTURE_MIN_FILTER].forEach((k) => gl.texParameteri(gl.TEXTURE_2D, k, gl.NEAREST));
    const fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, colour, 0);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, depth, 0);
    const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.bindTexture(gl.TEXTURE_2D, null);
    if (ok) this.shadow = { fb, depth, size, p: dp, uVP: gl.getUniformLocation(dp, "uVP") };
  }
  upload(mesh) {
    const gl = this.gl;
    const mk = (arr) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(arr), gl.STATIC_DRAW); return b; };
    return { pos: mk(mesh.p), nrm: mk(mesh.n), col: mk(mesh.c), count: mesh.p.length / 3 };
  }
  release(buf) { if (buf && this.gl) { this.gl.deleteBuffer(buf.pos); this.gl.deleteBuffer(buf.nrm); this.gl.deleteBuffer(buf.col); } }
  // the scene's fixed part: `solid` a Mesh, the sea a square of half-width R round centre at level wl
  setStatic({ solid, centre, R, wl, scale }) {
    if (!this.gl) return;
    this.release(this.static); this.release(this.water);
    this.static = this.upload(solid);
    const wm = new Mesh(), n = 40;
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
      const x0 = centre[0] - R + (2 * R * i) / n, x1 = centre[0] - R + (2 * R * (i + 1)) / n, y0 = centre[1] - R + (2 * R * j) / n, y1 = centre[1] - R + (2 * R * (j + 1)) / n;
      wm.quad([x0, y0, wl], [x1, y0, wl], [x1, y1, wl], [x0, y1, wl], [0, 0, 0]);
    }
    this.water = this.upload(wm);
    this.centre = centre; this.scale = scale; this.wl = wl;
  }
  // the frame's own meshes: [{mesh, opacity, glow}], uploaded now and dropped at the next call
  setDynamic(list) {
    if (!this.gl) return;
    this.dyn.forEach((d) => this.release(d.buf));
    this.dyn = list.filter((d) => d.mesh && d.mesh.p.length).map((d) => ({ ...d, buf: this.upload(d.mesh) }));
  }
  eyeOf(cam) {
    const yaw = (cam.yaw * Math.PI) / 180, pitch = (cam.pitch * Math.PI) / 180;
    return [cam.tx + cam.dist * Math.cos(pitch) * Math.cos(yaw), cam.ty + cam.dist * Math.cos(pitch) * Math.sin(yaw), cam.tz + cam.dist * Math.sin(pitch)];
  }
  draw(cam, { width, height, time, waterAlpha = 0.55 } = {}) {
    const gl = this.gl, canvas = this.canvas;
    if (width && (canvas.width !== width || canvas.height !== height)) { canvas.width = width; canvas.height = height; }
    if (!gl || !this.static) return false;
    const W = canvas.width, H = canvas.height;
    gl.viewport(0, 0, W, H);
    gl.clearColor(SKY[0], SKY[1], SKY[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    const pitch = (cam.pitch * Math.PI) / 180;
    const eye = this.eyeOf(cam);
    const proj = perspective((40 * Math.PI) / 180, W / H, Math.max(0.3, cam.dist / 500), cam.dist * 40);
    const VP = mul(proj, lookAt(eye, [cam.tx, cam.ty, cam.tz], [0, 0, 1]));
    this.lastVP = VP;
    const sun = [0.42, -0.5, 0.76], sl = Math.hypot(...sun);
    const t = time != null ? time : (performance.now() - this.t0) / 1000;
    let LVP = null;
    const solids = [{ buf: this.static, opacity: 1, glow: 0 }, ...this.dyn];
    if (this.shadow && this.shadowsOn) {
      const r = Math.max(40, this.scale * 0.9), c = [cam.tx, cam.ty, 0], D = 3 * r;
      const le = [c[0] + (sun[0] / sl) * D, c[1] + (sun[1] / sl) * D, c[2] + (sun[2] / sl) * D];
      LVP = mul(ortho(-r, r, -r, r, D - 1.5 * r, D + 1.5 * r), lookAt(le, c, [0, 0, 1]));
      gl.bindFramebuffer(gl.FRAMEBUFFER, this.shadow.fb);
      gl.viewport(0, 0, this.shadow.size, this.shadow.size);
      gl.clearColor(1, 1, 1, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST); gl.disable(gl.BLEND); gl.colorMask(false, false, false, false);
      gl.useProgram(this.shadow.p); gl.uniformMatrix4fv(this.shadow.uVP, false, new Float32Array(LVP));
      gl.disableVertexAttribArray(1); gl.disableVertexAttribArray(2);
      solids.filter((d) => d.opacity >= 1).forEach((d) => {
        gl.bindBuffer(gl.ARRAY_BUFFER, d.buf.pos); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, d.buf.count);
      });
      gl.colorMask(true, true, true, true);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, W, H);
      gl.clearColor(SKY[0], SKY[1], SKY[2], 1);
    }
    const setU = (pr) => {
      gl.useProgram(pr.p);
      gl.uniformMatrix4fv(pr.uVP, false, new Float32Array(VP));
      if (pr.uShadowOn) gl.uniform1f(pr.uShadowOn, LVP ? 1 : 0);
      if (LVP) {
        gl.uniformMatrix4fv(pr.uLVP, false, new Float32Array(LVP));
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.shadow.depth);
        gl.uniform1i(pr.uShadow, 0); gl.uniform1f(pr.uTexel, 1 / this.shadow.size);
      }
      gl.uniform3f(pr.uSun, sun[0] / sl, sun[1] / sl, sun[2] / sl);
      gl.uniform3f(pr.uEye, eye[0], eye[1], eye[2]);
      gl.uniform3f(pr.uSky, SKY[0], SKY[1], SKY[2]);
      gl.uniform1f(pr.uFog, Math.max(cam.dist * 9, this.scale * 6));
      if (pr.uTime) gl.uniform1f(pr.uTime, t);
      if (pr.uUnder) gl.uniform1f(pr.uUnder, Math.min(1, Math.max(0, (waterAlpha - 0.1) / 0.9)));
      if (pr.uWL) gl.uniform1f(pr.uWL, this.wl);
    };
    const bind = (buf) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, buf.pos); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, buf.nrm); gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, buf.col); gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 3, gl.FLOAT, false, 0, 0);
    };
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
    gl.useProgram(this.progs.sky.p);
    gl.uniform3f(this.progs.sky.uSky, SKY[0], SKY[1], SKY[2]);
    gl.uniform1f(this.progs.sky.uHorizon, Math.max(-1, Math.min(0.95, Math.tan(pitch) / Math.tan((20 * Math.PI) / 180))));
    gl.bindBuffer(gl.ARRAY_BUFFER, this.progs.sky.buf); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
    gl.disableVertexAttribArray(1); gl.disableVertexAttribArray(2);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    gl.enable(gl.DEPTH_TEST);
    setU(this.progs.solid);
    solids.filter((d) => d.opacity >= 1).forEach((d) => {
      gl.uniform1f(this.progs.solid.uOpac, 1); gl.uniform1f(this.progs.solid.uGlow, d.glow || 0);
      bind(d.buf); gl.drawArrays(gl.TRIANGLES, 0, d.buf.count);
    });
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    solids.filter((d) => d.opacity < 1).forEach((d) => {
      gl.uniform1f(this.progs.solid.uOpac, d.opacity); gl.uniform1f(this.progs.solid.uGlow, d.glow || 0);
      bind(d.buf); gl.drawArrays(gl.TRIANGLES, 0, d.buf.count);
    });
    setU(this.progs.water);
    gl.uniform1f(this.progs.water.uAlpha, waterAlpha);
    bind(this.water); gl.drawArrays(gl.TRIANGLES, 0, this.water.count);
    gl.depthMask(true); gl.disable(gl.BLEND);
    return true;
  }
  // a world point on the picture last drawn, in canvas pixels; null behind the camera
  project(p) {
    const m = this.lastVP;
    if (!m) return null;
    const cw = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
    if (cw <= 0) return null;
    return [((m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / cw * 0.5 + 0.5) * this.canvas.width,
      (1 - ((m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / cw * 0.5 + 0.5)) * this.canvas.height];
  }
}
