import { rectScale } from './theme'

// ── The form ────────────────────────────────────────────────────────────────────
// The first-run screens' ornament (see onboarding.tsx): one WebGL2 canvas. Every setup step
// has a form, written as a signed distance field, and the form eases from one to the next on
// springs, so going back halfway through a change just turns it around. It's drawn as dust:
// particles held to the surface by transform feedback and carried along it. The final server
// screen adds memories, small spheres of the same dust that aren't simulated (see brain.ts).

const COMMON = /* glsl */ `
precision highp float;
precision highp int;
uniform float uTime;
uniform int uShape0, uShape1, uShape2;
uniform vec3 uW;
uniform vec4 uVar0, uVar1, uVar2;
uniform float uPulse;
uniform float uFlow;

#define TAU 6.28318531

mat2 rot(float a) { float c = cos(a), s = sin(a); return mat2(c, s, -s, c); }
float smin(float a, float b, float k) { float h = max(k - abs(a - b), 0.0) / k; return min(a, b) - h * h * k * 0.25; }
float smax(float a, float b, float k) { return -smin(-a, -b, k); }
float sdSphere(vec3 p, float r) { return length(p) - r; }
float sdTorus(vec3 p, float R, float r) { vec2 q = vec2(length(p.xz) - R, p.y); return length(q) - r; }
float hash11(float n) { return fract(sin(n) * 43758.5453123); }
vec3 hash33(vec3 p) {
  p = fract(p * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yxz + 33.33);
  return fract((p.xxy + p.yxx) * p.zyx);
}

// A slow, layered warp: the surface breathes without the form changing.
vec3 flow(vec3 p) {
  float t = uTime;
  vec3 q = p;
  q += uFlow * 0.050 * sin(q.yzx * 2.2 + vec3(t * 0.61, t * 0.47, t * 0.73));
  q += uFlow * 0.022 * sin(q.zxy * 4.3 + vec3(t * 0.83, t * 0.71, t * 0.57));
  return q;
}

// Where it runs. A closed pebble for this machine alone; a bead with a way through it for
// this machine, shared; for a server somewhere else, a body drifting up and away with a
// few smaller ones trailing back toward here. v.x eases between 0, 1 and 2.
float shSeed(vec3 p, float m) {
  float dLocal = sdSphere(p * vec3(1.0, 1.12, 1.0), 0.84) / 1.12;
  vec3 pt = p; pt.yz *= rot(1.35); pt.xy *= rot(-0.45);
  float dShared = smax(sdSphere(p, 0.84), -(length(pt.xz) - 0.26), 0.18);
  vec3 head = vec3(0.26, 0.3, 0.0);
  vec3 dir = normalize(vec3(-0.78, -0.58, 0.2));
  float dServer = sdSphere(p - head, 0.44);
  float along = 0.0, r = 0.44;
  for (int i = 0; i < 3; i++) {
    float rn = r * 0.52;
    along += r + rn + 0.09;
    dServer = min(dServer, sdSphere(p - head - dir * along, rn));
    r = rn;
  }
  float a = clamp(m, 0.0, 1.0), b = clamp(m - 1.0, 0.0, 1.0);
  return mix(mix(dLocal, dShared, a), dServer, b);
}

// The bot: a shell opened in six places, with something inside it now.
float shCore(vec3 p) {
  vec3 q = p; q.xy *= rot(0.6); q.yz *= rot(0.5);
  float shell = abs(length(q) - 0.88) - 0.028;
  float holes = min(min(length(q.xy), length(q.yz)), length(q.xz)) - 0.4;
  return min(smax(shell, -holes, 0.06), sdSphere(p, 0.3 + 0.06 * uPulse));
}

// Remote access: the way through opens all the way.
float shRing(vec3 p) {
  vec3 q = p; q.yz *= rot(0.95); q.xy *= rot(-0.35);
  return sdTorus(q, 0.78, 0.21);
}

// Sign-in: a band that arrives back where it left, turned over once on the way round.
// v.x broadens it once Discord lists the redirects.
float shLoop(vec3 p, float lock) {
  vec3 q = p; q.yz *= rot(0.62); q.xy *= rot(0.35);
  float a = atan(q.z, q.x);
  vec2 w = vec2(length(q.xz) - 0.66, q.y);
  w *= rot(a * 0.5 + uTime * 0.12);
  vec2 e = vec2(mix(0.26, 0.32, lock), 0.1);
  return (length(w / e) - 1.0) * min(e.x, e.y) * 0.9;
}

// The server: one body among others on a shared orbit. v.x draws them in once it joins.
float shCluster(vec3 p, float gather) {
  float d = sdSphere(p, 0.36 + 0.06 * gather);
  vec3 q = p; q.xy *= rot(0.4); q.yz *= rot(0.25);
  float rad = mix(1.02, 0.7, gather);
  float k = mix(0.04, 0.24, gather);
  for (int i = 0; i < 7; i++) {
    float fi = float(i);
    float a = fi * TAU / 7.0 + uTime * 0.17;
    vec3 c = vec3(cos(a) * rad, 0.08 * sin(a * 2.0 + fi), sin(a) * rad);
    d = smin(d, sdSphere(q - c, 0.1 + 0.08 * hash11(fi * 7.31 + 1.0)), k);
  }
  return d;
}

// The key: the surface carries waves out from one point. v.x is how strongly.
float shVoice(vec3 p, float amp) {
  float r = length(p);
  vec3 n = p / max(r, 1e-4);
  float ang = acos(clamp(dot(n, normalize(vec3(-0.55, 0.4, 0.73))), -1.0, 1.0));
  float w = sin(ang * 13.0 - uTime * 2.0) * (0.35 + 0.65 * exp(-ang * 0.8));
  return (r - 0.8 - 0.07 * amp * w) * 0.72;
}

// Deploy: one loop with no end to it, turning on its own.
float shKnot(vec3 p) {
  vec3 q = p; q.yz *= rot(1.1); q.xz *= rot(uTime * 0.18);
  float a = atan(q.z, q.x);
  vec2 w = vec2(length(q.xz) - 0.64, q.y);
  float d = 1e9;
  for (int i = 0; i < 2; i++) {
    float ang = (a + TAU * float(i)) * 1.5;
    d = min(d, length(w - 0.3 * vec2(cos(ang), sin(ang))) - 0.12);
  }
  return d * 0.7;
}

// Connecting to a server that already runs it: a small body folding into one that was
// already there. v.x is how far.
float shMerge(vec3 p, float prog) {
  float big = sdSphere(p - vec3(0.12, -0.08, 0.0), 0.64);
  vec3 c = mix(vec3(-0.9, 0.72, 0.25), vec3(-0.32, 0.26, 0.12), prog);
  return smin(big, sdSphere(p - c, 0.3), mix(0.1, 0.42, prog));
}

// Done, and the running server: one whole body. v.x scales it (0 means 1): a stopped server
// draws in on itself.
float shWhole(vec3 p, float s) { return sdSphere(p, 0.74 * (s > 0.01 ? s : 1.0)); }

// Updating: a gap sweeps up through the body and closes behind itself, as a new version rolls
// through it.
float shSweep(vec3 p) {
  float y = mod(uTime * 0.42, 2.3) - 1.15;
  float gap = abs(p.y - y) - 0.045;
  return smax(sdSphere(p, 0.74), -gap, 0.06);
}

float shape(int id, vec3 p, vec4 v) {
  if (id == 0) return shSeed(p, v.x);
  if (id == 1) return shCore(p);
  if (id == 2) return shRing(p);
  if (id == 3) return shLoop(p, v.x);
  if (id == 4) return shCluster(p, v.x);
  if (id == 5) return shVoice(p, v.x);
  if (id == 6) return shKnot(p);
  if (id == 7) return shMerge(p, v.x);
  if (id == 8) return shWhole(p, v.x);
  return shSweep(p);
}

float mapObj(vec3 p) {
  p = flow(p);
  float d = 0.0;
  if (uW.x > 0.001) d += uW.x * shape(uShape0, p, uVar0);
  if (uW.y > 0.001) d += uW.y * shape(uShape1, p, uVar1);
  if (uW.z > 0.001) d += uW.z * shape(uShape2, p, uVar2);
  return d;
}

vec3 nrmObj(vec3 p) {
  const vec2 k = vec2(1.0, -1.0);
  const float h = 0.003;
  return normalize(k.xyy * mapObj(p + k.xyy * h) + k.yyx * mapObj(p + k.yyx * h) +
                   k.yxy * mapObj(p + k.yxy * h) + k.xxx * mapObj(p + k.xxx * h));
}
`

const SIM_VS = /* glsl */ `#version 300 es
${COMMON}
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec4 aSeed;
out vec3 vPos;
out vec4 vNrm;
uniform float uDt, uFrame, uRespawn, uAttract, uDrift, uJolt;

void main() {
  vec3 p = aPos;
  vec3 r = hash33(aSeed.xyz * 97.0 + vec3(uFrame * 0.0137, uFrame * 0.0071, 0.0));
  if (r.x < uRespawn || dot(p, p) > 16.0 || any(isnan(p))) {
    vec3 s = hash33(aSeed.zyx * 53.0 + vec3(uFrame * 0.031, 0.0, uFrame * 0.017));
    float th = TAU * s.x, ph = acos(2.0 * s.y - 1.0);
    p = mix(0.25, 1.35, s.z) * vec3(sin(ph) * cos(th), cos(ph), sin(ph) * sin(th));
  }
  float d = mapObj(p);
  vec3 n = nrmObj(p);
  // A shared current around a near-vertical axis plus each particle's own orbit, kept
  // tangent to the surface so nothing piles up where currents meet.
  vec3 ax = normalize(aSeed.xyz * 2.0 - 1.0);
  vec3 f = cross(normalize(vec3(0.15, 1.0, 0.1)), p) * 0.55 + cross(ax, p) * 0.45;
  f -= n * dot(f, n);
  p += f * uDt * uDrift * (0.6 + 0.8 * aSeed.w);
  p -= n * d * uAttract;
  p += n * uPulse * uPulse * 0.01;      // a confirmation lifts the dust off the surface
  // A wrong value: the dust shivers off the surface for a moment and is pulled back.
  p += (hash33(aSeed.xyz * 71.0 + vec3(uFrame * 0.173)) - 0.5) * 0.035 * uJolt;
  vPos = p;
  vNrm = vec4(n, d);
}`

const SIM_FS = /* glsl */ `#version 300 es
precision highp float;
out vec4 o;
void main() { o = vec4(0.0); }`

// Where dust may show, in canvas px: a fade in from the left (x to y), keeping it off the
// wizard's half, and a fade at the top and bottom (z). The far edges are 1 - smoothstep, so
// no pair of edges is ever equal (smoothstep with equal edges is undefined).
const MASK = /* glsl */ `
float pmask(vec2 px) {
  return smoothstep(uMask.x, uMask.y, px.x)
       * smoothstep(0.0, uMask.z, px.y) * (1.0 - smoothstep(uRes.y - uMask.z, uRes.y, px.y));
}`

// Memories: small spheres of dust around the form, one per memory the page shows. They aren't
// simulated: each grain has a place on its sphere (a Fibonacci point, carried round an axis of
// its own), so the sphere sits exactly where the page puts its words. Activation brings the
// grains out of the side of the form that faces the memory, each on its own curve and at its
// own moment, and taking it away sends them back the same way.
const SAT_VS = /* glsl */ `#version 300 es
precision highp float;
precision highp int;
uniform vec2 uRes, uCenter;
uniform float uFocal, uCamD, uDpr, uAlpha, uTime, uSize, uTwinkle, uStill;
uniform vec3 uInk, uAccent;
uniform vec4 uMask;
uniform int uSlot, uCount, uStride;
uniform float uWob;
uniform vec4 uSatA[12];   // centre x, y (world units, y up), radius (world units), seed
uniform vec4 uSatB[12];   // activation, brightness, peek, ripple
out vec3 vCol;
out float vA;

${MASK}

vec3 hash33(vec3 p) {
  p = fract(p * vec3(0.1031, 0.1030, 0.0973));
  p += dot(p, p.yxz + 33.33);
  return fract((p.xxy + p.yxx) * p.zyx);
}
vec3 fib(float i, float n) {
  float y = 1.0 - 2.0 * (i + 0.5) / n;
  float r = sqrt(max(0.0, 1.0 - y * y));
  float th = 2.39996323 * i;
  return vec3(r * cos(th), y, r * sin(th));
}
vec3 turn(vec3 v, vec3 k, float a) { float c = cos(a), s = sin(a); return v * c + cross(k, v) * s + k * dot(k, v) * (1.0 - c); }
vec3 bez(vec3 a, vec3 m, vec3 b, float t) { float u = 1.0 - t; return u * u * a + 2.0 * u * t * m + t * t * b; }

void main() {
  // One draw per memory, with as many grains as its size needs.
  int s = uSlot;
  float j = float(gl_VertexID - uSlot * uStride);
  vec4 A = uSatA[s], B = uSatB[s];
  vec3 h = hash33(vec3(j * 0.1371 + 1.3, float(s) * 7.31 + A.w * 13.7, j * 0.0713 + 2.9));
  vec3 C = vec3(A.xy, 0.0);
  vec3 dir = turn(fib(j, float(uCount)), normalize(h - 0.5 + 1e-3), uTime * (0.08 + 0.22 * h.z));
  // A slow wobble (gentler on a large sphere), and a ring that runs out across it when the
  // memory is touched.
  float wob = 1.0 + uWob * sin(dot(dir, vec3(3.1, 2.3, 1.7)) * 1.7 + uTime * 0.8 + A.w * 6.2832);
  float R = A.z * wob * (1.0 + 0.06 * B.w * sin(dir.y * 6.0 - uTime * 7.0));
  vec3 home = C + R * dir;

  // Out of the form's near side, on a curve that bends the same way for the whole memory.
  vec3 toward = normalize(C + vec3(1e-4, 0.0, 0.0));
  vec3 src = 0.74 * normalize(toward + 0.5 * (h - 0.5));
  vec3 side = vec3(-(home - src).y, (home - src).x, 0.0);
  vec3 mid = 0.5 * (src + home) + side * (0.22 * sign(A.w - 0.5) + 0.3 * (h.x - 0.5));
  float k = smoothstep(0.62 * h.y, 0.62 * h.y + 0.38, B.x);
  vec3 p = uStill > 0.5 ? home : bez(src, mid, home, k);

  // Peeked: a little of it runs back to the form as a faint thread.
  float thread = step(j, float(uCount) * 0.07) * B.z;
  if (thread > 0.0) {
    float u = fract(h.x + uTime * 0.12);
    vec3 a = 0.76 * toward, b = C - toward * R * 0.98;
    vec3 m = 0.5 * (a + b) + vec3(-(b - a).y, (b - a).x, 0.0) * 0.08 * sign(A.w - 0.5);
    vec3 q = bez(a, m, b, u) + (h - 0.5) * 0.012;
    p = mix(p, q, thread);
  }

  // Memories are drawn flat (no perspective), so each lands exactly where the page put it at
  // exactly its size, large or small, and is lit as if seen head-on.
  vec2 px = uCenter + uFocal * p.xy / uCamD;
  gl_Position = vec4(px / uRes * 2.0 - 1.0, 0.0, 1.0);
  float facing = dir.z;
  float dif = 0.5 + 0.5 * dot(dir, normalize(vec3(-0.45, 0.55, 0.7)));
  float front = smoothstep(-0.25, 0.35, facing);
  float rim = pow(1.0 - abs(facing), 3.0);
  float tw = mix(1.0, 0.6 + 0.4 * sin(uTime * (0.5 + h.z * 1.3) + h.x * 40.0), uTwinkle);
  vCol = mix(uInk, uAccent, max(step(0.86, h.y), rim * 0.55));
  // Rim-weighted, so the inside stays clear for the words set in it.
  float body = 0.02 + 0.11 * front * dif + 0.8 * rim;
  float lit = mix(body, 0.22, thread) * mix(1.0, sin(3.14159 * fract(h.x + uTime * 0.12)), thread);
  float arrive = uStill > 0.5 ? B.x : smoothstep(0.0, 0.12, k) * (0.45 + 0.55 * k);
  vA = uAlpha * B.y * pmask(px) * lit * tw * arrive * (1.0 + 1.1 * B.w) * 1.9;
  gl_PointSize = uDpr * uSize * (0.6 + 0.9 * h.z);
}`

const POINT_VS = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec4 aNrm;
layout(location = 2) in vec4 aSeed;
uniform mat3 uRot;
uniform vec2 uRes, uCenter;
uniform float uFocal, uCamD, uDpr, uAlpha, uTime, uPulse, uSize, uTwinkle;
uniform vec3 uInk, uAccent;
uniform vec4 uMask;
out vec3 vCol;
out float vA;

${MASK}

void main() {
  mat3 toW = transpose(uRot);
  vec3 p = toW * aPos;
  vec3 n = toW * aNrm.xyz;
  vec3 v = p - vec3(0.0, 0.0, uCamD);
  float z = -v.z;
  vec2 px = uCenter + uFocal * v.xy / z;
  gl_Position = vec4(px / uRes * 2.0 - 1.0, 0.0, 1.0);
  float facing = dot(n, -normalize(v));
  float dif = 0.5 + 0.5 * dot(n, normalize(vec3(-0.45, 0.55, 0.7)));
  float front = smoothstep(-0.25, 0.35, facing);
  float rim = pow(1.0 - abs(facing), 3.0);
  float tw = mix(1.0, 0.6 + 0.4 * sin(uTime * (0.5 + aSeed.w * 1.3) + aSeed.x * 40.0), uTwinkle);
  vCol = mix(uInk, uAccent, max(step(0.86, aSeed.y), rim * 0.55));
  float settled = exp(-abs(aNrm.w) * 10.0);   // still drifting in: fainter
  vA = uAlpha * pmask(px) * (0.04 + 0.24 * front * dif + 0.58 * rim) * tw
     * (1.0 + 0.8 * uPulse) * (0.3 + 0.7 * settled);
  gl_PointSize = uDpr * uSize * (0.7 + 1.1 * aSeed.z) * (4.2 / z);
}`

const POINT_FS = /* glsl */ `#version 300 es
precision highp float;
in vec3 vCol;
in float vA;
out vec4 o;
void main() {
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(c, c);
  if (r2 > 1.0) discard;
  float a = vA * (1.0 - r2);
  o = vec4(vCol * a, a);
}`

type Program = { p: WebGLProgram; u: Record<string, WebGLUniformLocation | null>; shaders: WebGLShader[] }

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const sh = gl.createShader(type)!
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) || 'shader')
  return sh
}

function program(gl: WebGL2RenderingContext, vs: string, fs: string, feedback?: string[]): Program {
  const p = gl.createProgram()!
  const shaders = [compile(gl, gl.VERTEX_SHADER, vs), compile(gl, gl.FRAGMENT_SHADER, fs)]
  for (const s of shaders) gl.attachShader(p, s)
  if (feedback) gl.transformFeedbackVaryings(p, feedback, gl.SEPARATE_ATTRIBS)
  gl.linkProgram(p)
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) || 'link')
  const u: Program['u'] = {}
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS) as number
  for (let i = 0; i < n; i++) {
    const info = gl.getActiveUniform(p, i)
    if (info) u[info.name.replace(/\[0\]$/, '')] = gl.getUniformLocation(p, info.name)
  }
  return { p, u, shaders }
}

type Slot = { shape: number; v: number[]; vt: number[]; w: number; wv: number; target: number }

// Three slots, each with a weight on a critically damped spring toward 0 or 1. A change takes
// over whichever slot matters least, and nothing ever jumps.
function makeMorph() {
  const slots: Slot[] = [0, 1, 2].map(() => ({ shape: 0, v: [0, 0, 0, 0], vt: [0, 0, 0, 0], w: 0, wv: 0, target: 0 }))
  slots[0].w = 1; slots[0].target = 1
  return {
    slots,
    set(shape: number, variant?: number) {
      const vt = [variant ?? 0, 0, 0, 0]
      let s = slots.find((x) => x.shape === shape && (x.w > 0.001 || x.target > 0))
      if (!s) {
        s = slots.reduce((m, x) => (x.w + x.target < m.w + m.target ? x : m))
        s.shape = shape; s.w = 0; s.wv = 0; s.v = vt.slice()
      }
      s.vt = vt
      for (const x of slots) x.target = x === s ? 1 : 0
    },
    step(dt: number, w0: number, varRate: number): number[] {
      for (const s of slots) {
        s.wv += (-2 * w0 * s.wv - w0 * w0 * (s.w - s.target)) * dt
        s.w += s.wv * dt
        if (s.w < 0) { s.w = 0; s.wv = Math.max(0, s.wv) }
        for (let i = 0; i < 4; i++) s.v[i] += (s.vt[i] - s.v[i]) * (1 - Math.exp(-dt * varRate))
      }
      const sum = slots.reduce((m, s) => m + s.w, 0) || 1
      return slots.map((s) => s.w / sum)
    },
  }
}

// World -> object rotation (the transpose of Rz(roll) Rx(pitch) Ry(yaw)), column-major.
function rotMat(yaw: number, pitch: number, roll: number): number[] {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch)
  const cr = Math.cos(roll), sr = Math.sin(roll)
  const mul = (a: number[], b: number[]) => {
    const o = new Array<number>(9)
    for (let c = 0; c < 3; c++) for (let r = 0; r < 3; r++) o[c * 3 + r] = a[r] * b[c * 3] + a[3 + r] * b[c * 3 + 1] + a[6 + r] * b[c * 3 + 2]
    return o
  }
  const R = mul([cr, sr, 0, -sr, cr, 0, 0, 0, 1], mul([1, 0, 0, 0, cp, sp, 0, -sp, cp], [cy, 0, -sy, 0, 1, 0, sy, 0, cy]))
  return [R[0], R[3], R[6], R[1], R[4], R[7], R[2], R[5], R[8]]
}

const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x))

/** The root's `zoom` (the interface size): layout px to CSS px. */
export const zoomOf = () => parseFloat(getComputedStyle(document.documentElement).zoom) || 1

/** An element's box in layout px, the page's own units under the interface-size zoom. A rect
 *  comes back zoomed or not depending on the engine (the desktop app's Chromium 126 doesn't
 *  zoom it; current Chrome does), so it's divided by what the engine actually does. */
export function layoutRect(el: Element): { x: number; y: number; w: number; h: number } {
  const r = el.getBoundingClientRect(), k = rectScale()
  return { x: r.left / k, y: r.top / k, w: r.width / k, h: r.height / k }
}

/** Where the form sits, in layout px: its centre (cx, cy), the px one world unit spans at the
 *  centre's depth (r), a fade in from the left edge between x0 and x1, and a fade at the top
 *  and bottom over fy. */
export type Framing = { cx: number; cy: number; r: number; x0: number; x1: number; fy: number }
export type View = { x: number; y: number; w: number; h: number }
/** A memory's sphere, in layout px. */
export type Satellite = { x: number; y: number; r: number; act: number; bright?: number; peek?: number; ripple?: number; seed?: number }
export type Mood = { dim?: number; loose?: number; tremor?: number }

/** Each form, by what it's for. The numbers are the shader's `shape()` ids. */
export const SHAPE = {
  where: 0, bot: 1, remote: 2, signin: 3, server: 4, keys: 5, deploy: 6, connect: 7, whole: 8, updating: 9,
} as const

export type Form = {
  start(): void
  destroy(): void
  set(shape: number, variant?: number): void
  pulse(amount?: number): void
  energy(x: number): void
  reject(): void
  mood(m?: Mood): void
  regather(from?: number): void
  framing(fn: (v: View) => Framing): void
  satellites(list: (Satellite | null)[]): void
  onFrame(fn: (dt: number, now: number) => void): () => void
  requalify(): void
  running(): boolean
}

export function createForm(canvas: HTMLCanvasElement, opts: { particles?: number } = {}): Form | null {
  let gl: WebGL2RenderingContext | null = null
  try {
    gl = canvas.getContext('webgl2', { antialias: true, alpha: true, premultipliedAlpha: true, powerPreference: 'high-performance' })
  } catch { gl = null }
  if (!gl) return null
  const g = gl
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
  const state = {
    time: 0, spin: 0,
    pulse: 0,
    jolt: 0,                           // 0..1, decays after a wrong value
    flow: 2.2, flowTarget: 1,          // starts stirred up and settles: the arrival
    intro: 0,                          // 0 → 1 over the first seconds (fade in)
    gather: 0,                         // 0 → 1: how hard the dust is pulled in (the opening)
    // A mood, eased toward its targets: how bright the dust is, how hard it's held to the
    // surface, and a tremor it keeps rather than one that decays.
    dim: 1, dimT: 1, loose: 1, looseT: 1, tremor: 0, tremorT: 0,
    dpr: Math.min(window.devicePixelRatio || 1, 2),
    scale: 1,
    drawShare: 1,
  }
  const morph = makeMorph()
  const ink = [0.616, 0.616, 0.655]      // --text-2
  const accent = [0.357, 0.612, 0.965]   // --accent

  let sim: Program, pts: Program, sat: Program
  try {
    sim = program(g, SIM_VS, SIM_FS, ['vPos', 'vNrm'])
    pts = program(g, POINT_VS, POINT_FS)
    sat = program(g, SAT_VS, POINT_FS)
  } catch (e) {
    console.warn('The form could not start:', e)
    return null
  }
  const bare = g.createVertexArray()     // the memories draw from gl_VertexID alone

  // The particles start scattered wide, so the first thing the form does is gather.
  type Particles = {
    n: number; bufs: { pos: WebGLBuffer; nrm: WebGLBuffer }[]; seed: WebGLBuffer
    simVao: WebGLVertexArrayObject[]; drawVao: WebGLVertexArrayObject[]; tf: WebGLTransformFeedback; cur: number; frame: number
  }
  let P: Particles | null = null
  function initParticles(n: number): Particles {
    const pos = new Float32Array(n * 3), seed = new Float32Array(n * 4), nrm = new Float32Array(n * 4)
    for (let i = 0; i < n; i++) {
      const th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1), r = 1.2 + 1.6 * Math.random()
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th); pos[i * 3 + 1] = r * Math.cos(ph); pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th)
      for (let k = 0; k < 4; k++) seed[i * 4 + k] = Math.random()
      nrm[i * 4 + 3] = 1
    }
    const mk = (data: Float32Array) => { const b = g.createBuffer()!; g.bindBuffer(g.ARRAY_BUFFER, b); g.bufferData(g.ARRAY_BUFFER, data, g.DYNAMIC_COPY); return b }
    const seedBuf = mk(seed)
    const bufs = [0, 1].map(() => ({ pos: mk(pos), nrm: mk(nrm) }))
    const vao = (b: { pos: WebGLBuffer; nrm: WebGLBuffer }, draw: boolean) => {
      const v = g.createVertexArray()!; g.bindVertexArray(v)
      g.bindBuffer(g.ARRAY_BUFFER, b.pos); g.enableVertexAttribArray(0); g.vertexAttribPointer(0, 3, g.FLOAT, false, 0, 0)
      if (draw) { g.bindBuffer(g.ARRAY_BUFFER, b.nrm); g.enableVertexAttribArray(1); g.vertexAttribPointer(1, 4, g.FLOAT, false, 0, 0) }
      g.bindBuffer(g.ARRAY_BUFFER, seedBuf); g.enableVertexAttribArray(draw ? 2 : 1); g.vertexAttribPointer(draw ? 2 : 1, 4, g.FLOAT, false, 0, 0)
      return v
    }
    const out = {
      n, bufs, seed: seedBuf, simVao: bufs.map((b) => vao(b, false)), drawVao: bufs.map((b) => vao(b, true)),
      tf: g.createTransformFeedback()!, cur: 0, frame: 0,
    }
    g.bindVertexArray(null)
    return out
  }

  // The canvas fills a fixed layer the size of the window. Everything the page hands over
  // (framing, memories) is in layout px, the page's own units under its interface-size zoom;
  // the canvas is sized in device px (layout px × zoom × pixel ratio). The layer's box is read
  // when it changes, not every frame.
  let Z = zoomOf(), view: View = { x: 0, y: 0, w: 1, h: 1 }
  function measure() {
    Z = zoomOf()
    state.dpr = Math.min(window.devicePixelRatio || 1, 2)
    view = layoutRect(canvas)
  }
  measure()
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
  ro?.observe(canvas)
  window.addEventListener('resize', measure)
  let W = 0, H = 0
  function resize() {
    const k = Z * state.dpr * state.scale
    const w = Math.max(1, Math.round(view.w * k)), h = Math.max(1, Math.round(view.h * k))
    if (w !== W || h !== H) { W = w; H = h; canvas.width = w; canvas.height = h }
  }

  // The default framing is the stage's: set off the right edge, its left 30% faded.
  let framingFn = (v: View): Framing => ({ cx: v.w - 0.2 * v.h, cy: v.h / 2, r: 0.45 * v.h, x0: 0, x1: 0.3 * v.w, fy: 0.1 * v.h })

  // Memories. Each gets grains in proportion to its size, so a large one's rim is as fine as
  // a small one's.
  const M = 7000, NSAT = 12
  const grainsFor = (r: number) => Math.round(clamp(3000 * Math.pow(r / 90, 0.9), 2200, M))
  const wobFor = (r: number) => 0.045 * clamp(90 / Math.max(1, r), 0.2, 1)
  let sats: (Satellite | null)[] = []
  const satA = new Float32Array(NSAT * 4), satB = new Float32Array(NSAT * 4)

  let weights = [1, 0, 0]
  function common(prog: Program) {
    const u = prog.u, s = morph.slots
    g.uniform1f(u.uTime, state.time)
    g.uniform1i(u.uShape0, s[0].shape); g.uniform1i(u.uShape1, s[1].shape); g.uniform1i(u.uShape2, s[2].shape)
    g.uniform3f(u.uW, weights[0], weights[1], weights[2])
    g.uniform4fv(u.uVar0, s[0].v); g.uniform4fv(u.uVar1, s[1].v); g.uniform4fv(u.uVar2, s[2].v)
    if (u.uPulse) g.uniform1f(u.uPulse, state.pulse)
    if (u.uFlow) g.uniform1f(u.uFlow, state.flow)
  }

  function simulate(dt: number, iterations: number) {
    if (!P) P = initParticles(opts.particles || 60000)
    const Q = P
    g.useProgram(sim.p)
    common(sim)
    g.uniform1f(sim.u.uDt, dt)
    g.uniform1f(sim.u.uRespawn, 0.0022)
    // A slower pull at first, so the opening gather reads as a gather and not a cut.
    const gg = Math.min(1, state.gather)
    g.uniform1f(sim.u.uAttract, (0.018 + 0.112 * gg * gg * (3 - 2 * gg)) * state.loose)
    g.uniform1f(sim.u.uDrift, reduce.matches ? 0.05 : 0.12)
    g.uniform1f(sim.u.uJolt, Math.max(state.jolt, state.tremor))
    g.enable(g.RASTERIZER_DISCARD)
    for (let i = 0; i < iterations; i++) {
      const dst = 1 - Q.cur
      g.uniform1f(sim.u.uFrame, Q.frame++)
      g.bindVertexArray(Q.simVao[Q.cur])
      g.bindTransformFeedback(g.TRANSFORM_FEEDBACK, Q.tf)
      g.bindBufferBase(g.TRANSFORM_FEEDBACK_BUFFER, 0, Q.bufs[dst].pos)
      g.bindBufferBase(g.TRANSFORM_FEEDBACK_BUFFER, 1, Q.bufs[dst].nrm)
      g.beginTransformFeedback(g.POINTS)
      g.drawArrays(g.POINTS, 0, Q.n)
      g.endTransformFeedback()
      g.bindBufferBase(g.TRANSFORM_FEEDBACK_BUFFER, 0, null)
      g.bindBufferBase(g.TRANSFORM_FEEDBACK_BUFFER, 1, null)
      g.bindTransformFeedback(g.TRANSFORM_FEEDBACK, null)
      Q.cur = dst
    }
    g.disable(g.RASTERIZER_DISCARD)
    g.bindVertexArray(null)
  }

  function tick(dt: number) {
    // Reduced motion slows everything to a third rather than stopping it.
    const k = reduce.matches ? 0.35 : 1
    state.time += dt * k
    state.spin += dt * 0.07 * k
    state.intro = Math.min(1, state.intro + dt / 1.8)
    state.gather = Math.min(1, state.gather + dt / 3.2)
    state.pulse *= Math.exp(-dt * 1.6)
    state.jolt *= Math.exp(-dt * 4.5)
    const ease = 1 - Math.exp(-dt * 1.6)
    state.dim += (state.dimT - state.dim) * ease
    state.loose += (state.looseT - state.loose) * ease
    state.tremor += (state.tremorT - state.tremor) * ease
    state.flow += (state.flowTarget - state.flow) * (1 - Math.exp(-dt * 0.9))
    weights = morph.step(dt, reduce.matches ? 2.0 : 3.2, reduce.matches ? 1.4 : 2.4)
  }

  function draw(dt: number) {
    resize()
    g.viewport(0, 0, W, H)
    g.clearColor(0, 0, 0, 0)
    g.clear(g.COLOR_BUFFER_BIT)
    simulate(Math.max(1 / 240, Math.min(1 / 30, dt || 1 / 60)) * (reduce.matches ? 0.35 : 1), 1)
    const Q = P!
    const F = framingFn(view)
    const k = W / Math.max(1, view.w)                       // device px per layout px
    // A smaller form keeps the same grain per area of screen: finer and fainter dust.
    const rr = Math.max(0.2, Math.min(1.2, F.r / (0.45 * Math.max(1, view.h))))
    const x0 = F.x0 * k, x1 = Math.max(F.x1 * k, x0 + 1), fy = Math.max(F.fy * k, 1)
    // A slow sway rather than a spin, so every form keeps the angle it was drawn for.
    const rot = rotMat(0.2 + 0.42 * Math.sin(state.spin * 1.1), 0.2 + 0.07 * Math.sin(state.spin * 0.8 + 1.3), -0.08)
    const u = pts.u, camD = 4.2
    g.useProgram(pts.p)
    common(pts)
    g.uniform2f(u.uRes, W, H)
    g.uniform2f(u.uCenter, F.cx * k, H - F.cy * k)
    g.uniform1f(u.uFocal, F.r * k * camD)
    g.uniform1f(u.uCamD, camD)
    g.uniformMatrix3fv(u.uRot, false, rot)
    g.uniform1f(u.uAlpha, 0.95 * state.intro * state.dim * rr)
    g.uniform3fv(u.uInk, ink)
    g.uniform3fv(u.uAccent, accent)
    g.uniform4f(u.uMask, x0, x1, fy, 0)
    g.uniform1f(u.uDpr, state.dpr * state.scale)
    g.uniform1f(u.uSize, 1.6 * Math.sqrt(rr))
    g.uniform1f(u.uTwinkle, reduce.matches ? 0 : 1)
    g.enable(g.BLEND)
    g.blendFunc(g.ONE, g.ONE)
    g.bindVertexArray(Q.drawVao[Q.cur])
    g.drawArrays(g.POINTS, 0, Math.round(Q.n * state.drawShare))

    // The memories: world units from layout px, y up, at the form's depth.
    const live: [number, number][] = []
    for (let i = 0; i < Math.min(NSAT, sats.length); i++) {
      const m = sats[i]
      if (!m) { satB[i * 4] = 0; continue }
      satA.set([(m.x - F.cx) / F.r, -(m.y - F.cy) / F.r, m.r / F.r, m.seed ?? 0.5], i * 4)
      satB.set([m.act, m.bright ?? 1, m.peek ?? 0, m.ripple ?? 0], i * 4)
      if (m.act > 0.001) live.push([i, m.r])
    }
    if (live.length) {
      const v = sat.u
      g.useProgram(sat.p)
      g.uniform2f(v.uRes, W, H)
      g.uniform2f(v.uCenter, F.cx * k, H - F.cy * k)
      g.uniform1f(v.uFocal, F.r * k * camD)
      g.uniform1f(v.uCamD, camD)
      g.uniform1f(v.uAlpha, 0.95 * state.intro * state.dim)
      g.uniform1f(v.uTime, state.time)
      g.uniform3fv(v.uInk, ink)
      g.uniform3fv(v.uAccent, accent)
      g.uniform4f(v.uMask, x0, x1, fy, 0)
      g.uniform1f(v.uDpr, state.dpr * state.scale)
      g.uniform1f(v.uSize, 1.4)
      g.uniform1f(v.uTwinkle, reduce.matches ? 0 : 1)
      g.uniform1f(v.uStill, reduce.matches ? 1 : 0)
      g.uniform1i(v.uStride, M)
      g.uniform4fv(v.uSatA, satA)
      g.uniform4fv(v.uSatB, satB)
      g.bindVertexArray(bare)
      for (const [i, r] of live) {
        const n = grainsFor(r)
        g.uniform1i(v.uSlot, i)
        g.uniform1i(v.uCount, n)
        g.uniform1f(v.uWob, wobFor(r))
        g.drawArrays(g.POINTS, i * M, n)
      }
    }
    g.bindVertexArray(null)
  }

  // Run only while it can be seen: a hidden tab or a withheld layer costs nothing.
  let raf = 0, last = 0, onScreen = true, dead = false
  const frames: number[] = [], hooks: ((dt: number, now: number) => void)[] = []
  function frame(now: number) {
    raf = 0
    if (dead) return
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60
    last = now
    tick(dt)
    for (const h of hooks.slice()) h(dt, now)
    draw(dt)
    watchCost(dt)
    schedule()
  }
  function schedule() {
    if (!raf && !dead && onScreen && !document.hidden) raf = requestAnimationFrame(frame)
  }
  // A slow GPU gets a smaller canvas and fewer particles instead of a stutter.
  function watchCost(dt: number) {
    if (frames.length > 150 || state.intro < 1) return
    frames.push(dt)
    if (frames.length === 150) {
      const avg = frames.slice(30).reduce((a, b) => a + b, 0) / 120
      if (avg > 1 / 40) { state.scale = 0.7; state.drawShare = 0.55 }
    }
  }
  const onVisibility = () => { last = 0; schedule() }
  document.addEventListener('visibilitychange', onVisibility)
  const io = typeof IntersectionObserver !== 'undefined'
    ? new IntersectionObserver(([e]) => { onScreen = e.isIntersecting && e.boundingClientRect.width > 0; last = 0; schedule() })
    : null
  io?.observe(canvas)

  return {
    start() { schedule() },
    // The canvas outlives a remount (StrictMode mounts twice), so leave the context itself be
    // and only let go of what this instance made.
    destroy() {
      dead = true
      if (raf) cancelAnimationFrame(raf)
      raf = 0
      ro?.disconnect(); io?.disconnect()
      window.removeEventListener('resize', measure)
      document.removeEventListener('visibilitychange', onVisibility)
      if (P) {
        for (const b of P.bufs) { g.deleteBuffer(b.pos); g.deleteBuffer(b.nrm) }
        g.deleteBuffer(P.seed)
        for (const v of [...P.simVao, ...P.drawVao]) g.deleteVertexArray(v)
        g.deleteTransformFeedback(P.tf)
      }
      g.deleteVertexArray(bare)
      for (const pr of [sim, pts, sat]) { for (const s of pr.shaders) g.deleteShader(s); g.deleteProgram(pr.p) }
    },
    set(shape, variant) { morph.set(shape, variant) },
    pulse(amount = 1) { state.pulse = Math.max(state.pulse, amount * (reduce.matches ? 0.5 : 1)) },
    energy(x) { state.flowTarget = 1 + x },
    reject() { state.jolt = reduce.matches ? 0.5 : 1 },
    mood({ dim = 1, loose = 1, tremor = 0 } = {}) {
      state.dimT = dim; state.looseT = loose; state.tremorT = reduce.matches ? tremor * 0.5 : tremor
    },
    // Let the dust go slack and pull it back in, the way it gathers on first load.
    regather(from = 0.25) { state.gather = Math.min(state.gather, from) },
    framing(fn) { framingFn = fn },
    satellites(list) { sats = list },
    onFrame(fn) { hooks.push(fn); return () => { const i = hooks.indexOf(fn); if (i >= 0) hooks.splice(i, 1) } },
    // Measure the frame rate again (the layer and what's on it changed).
    requalify() { if (state.scale === 1) frames.length = 0 },
    running: () => !!raf,
  }
}
