// The drawing of the Skyfold promo video. draw(t) paints the frame at time t (seconds) and keeps no
// state between frames: the same t always gives the same picture, so seeking, previews and
// re-renders are exact. Everything shown is made up: no real photos, files or notes.

import {
  FPS, BEAT, EVENTS, OUTRO, PHASES, atFrame, clamp, cuts, easeInOut, easeOut, lerp, mark, rng, scenes, smooth,
  spring,
} from './timeline.mjs';

const params = new URLSearchParams(location.search);
export let format = params.get('format') === 'tall' ? 'tall' : 'wide';
let cut = cuts[format];

const canvas = document.querySelector('canvas');
const g = canvas.getContext('2d', { alpha: false });

// --- brand ---------------------------------------------------------------------------------------
const C = {
  bg: '#0d1016', bg2: '#131823', card: '#171c25', card2: '#1c2230', line: '#262e3d', line2: '#323c4f',
  ink: '#edf1f7', muted: '#9ba7b9', soft: '#c3ccd9', accent: '#8fb8ff', blue: '#3b82f6', blue2: '#4f8dff',
  green: '#7ad3a0', amber: '#f3c969', pink: '#f36c8c', win: '#1b1f27', win2: '#20252f', black: '#07090d',
};
const DISPLAY = '"Segoe UI Variable Display", "Segoe UI", sans-serif';
const TEXT = '"Segoe UI Variable Text", "Segoe UI", sans-serif';
const MONO = '"Cascadia Mono", Consolas, monospace';
const SCENE_GLOW = { hook: C.blue, tray: C.blue, photos: C.pink, gallery: C.amber, drive: C.blue2, notes: C.amber,
  safe: C.green, end: C.blue };

const icon = new Image();
icon.src = './icon.png';

// Layouts are designed at 1280x720 (wide) or 720x1280 (tall) and drawn at 1.5x.
const LAYOUT = {
  wide: { W: 1280, H: 720, text: { x: 80, y: 196, w: 430, size: 46, sub: 19 }, card: { x: 895, y: 345, s: 1 },
    rail: { y: 662, x0: 250, x1: 1030 } },
  tall: { W: 720, H: 1280, text: { x: 44, y: 220, w: 630, size: 52, sub: 23 }, card: { x: 360, y: 790, s: 0.97 },
    rail: { y: 1150, x0: 84, x1: 636 } },
};
let L = LAYOUT[format];
export const CW = 700, CH = 460; // a scene's card, in its own units

export function setFormat(f) {
  format = f === 'tall' ? 'tall' : 'wide';
  cut = cuts[format];
  L = LAYOUT[format];
  canvas.width = cut.width;
  canvas.height = cut.height;
}
setFormat(format);

// --- small drawing helpers -----------------------------------------------------------------------
function rr(x, y, w, h, r) {
  g.beginPath();
  g.roundRect(x, y, w, h, r);
}
function fillRR(x, y, w, h, r, color) {
  rr(x, y, w, h, r);
  g.fillStyle = color;
  g.fill();
}
function strokeRR(x, y, w, h, r, color, lw = 1) {
  rr(x, y, w, h, r);
  g.strokeStyle = color;
  g.lineWidth = lw;
  g.stroke();
}
function font(size, weight = 400, family = TEXT) {
  g.font = `${weight} ${size}px ${family}`;
}
function text(s, x, y, { size = 14, weight = 400, color = C.ink, family = TEXT, align = 'left', base = 'alphabetic',
  maxW } = {}) {
  font(size, weight, family);
  g.fillStyle = color;
  g.textAlign = align;
  g.textBaseline = base;
  if (maxW && g.measureText(s).width > maxW) {
    while (s.length > 1 && g.measureText(s + '…').width > maxW) s = s.slice(0, -1);
    s += '…';
  }
  g.fillText(s, x, y);
  return g.measureText(s).width;
}
function wrap(s, maxW, size, weight, family) {
  font(size, weight, family);
  const words = s.split(' ');
  const lines = [];
  let line = '';
  for (const w of words) {
    const next = line ? `${line} ${w}` : w;
    if (line && g.measureText(next).width > maxW) {
      lines.push(line);
      line = w;
    } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}
function withAlpha(a, fn) {
  if (a <= 0.001) return;
  g.save();
  g.globalAlpha *= clamp(a);
  fn();
  g.restore();
}
function num(n) {
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
function mix(a, b, x) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [r1, g1, b1] = p(a), [r2, g2, b2] = p(b);
  return `rgb(${Math.round(lerp(r1, r2, x))},${Math.round(lerp(g1, g2, x))},${Math.round(lerp(b1, b2, x))})`;
}
function rgba(hex, a) {
  const [r, gg, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r},${gg},${b},${a})`;
}

// --- the Skyfold mark (the same geometry as the app's icon, skyfold/ui/icons.py) -------------------
const CLOUD = [[[0.155, 0.465], 0.155], [[0.31, 0.34], 0.19], [[0.6, 0.25], 0.25], [[0.845, 0.465], 0.155]];
function upper(c1, r1, c2, r2) {
  const [x1, y1] = c1, [x2, y2] = c2;
  const dx = x2 - x1, dy = y2 - y1, d = Math.hypot(dx, dy);
  const a = (r1 * r1 - r2 * r2 + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(r1 * r1 - a * a, 0));
  const mx = x1 + (a * dx) / d, my = y1 + (a * dy) / d;
  const p1 = [mx + (h * dy) / d, my - (h * dx) / d], p2 = [mx - (h * dy) / d, my + (h * dx) / d];
  return p1[1] < p2[1] ? p1 : p2;
}
function cloudPath(x, y, w) {
  const cs = CLOUD.map(([[cx, cy], r]) => [[x + cx * w, y + cy * w], r * w]);
  const f = 0.07 * w;
  const fil = [];
  for (let i = 0; i < cs.length - 1; i++) {
    const [c1, r1] = cs[i], [c2, r2] = cs[i + 1];
    const fc = upper(c1, r1 + f, c2, r2 + f);
    const t1 = [c1[0] + ((fc[0] - c1[0]) * r1) / (r1 + f), c1[1] + ((fc[1] - c1[1]) * r1) / (r1 + f)];
    const t2 = [c2[0] + ((fc[0] - c2[0]) * r2) / (r2 + f), c2[1] + ((fc[1] - c2[1]) * r2) / (r2 + f)];
    fil.push([fc, t1, t2]);
  }
  const bottom = y + 0.62 * w;
  const ang = (c, p) => Math.atan2(p[1] - c[1], p[0] - c[0]);
  g.beginPath();
  let pos = [cs[0][0][0], bottom];
  g.moveTo(...pos);
  cs.forEach(([c, r], i) => {
    const end = i < fil.length ? fil[i][1] : [c[0], bottom];
    g.arc(c[0], c[1], r, ang(c, pos), ang(c, end), false);
    if (i < fil.length) {
      const [fc, t1, t2] = fil[i];
      g.arc(fc[0], fc[1], f, ang(fc, t1), ang(fc, t2), true);
      pos = t2;
    }
  });
  g.closePath();
}
// The mark centred on (cx, cy), s wide. draw: 0..1 draws the outline on, arrows: 0..1 the arrows.
function skyMark(cx, cy, s, color = C.blue, { lw = s * 0.045, aw = s * 0.03, draw = 1, arrows = 1, inset = 0.13,
  glow = 0 } = {}) {
  const x = cx - s / 2, y = cy - s / 2;
  const cw = s * (1 - 2 * inset);
  const top = (s - cw * 0.62) / 2 - s * 0.02;
  g.save();
  g.lineCap = 'round';
  g.lineJoin = 'round';
  g.strokeStyle = color;
  if (glow) {
    g.shadowColor = rgba(C.blue, 0.8 * glow);
    g.shadowBlur = s * 0.25;
  }
  g.lineWidth = lw;
  const len = cw * 3.3;
  if (draw < 1) g.setLineDash([len * draw, len]);
  if (draw > 0) {
    cloudPath(x + s * inset, y + top, cw);
    g.stroke();
  }
  g.setLineDash([]);
  if (arrows > 0) {
    const bb = y + top + 0.03 * cw + 0.59 * cw;
    const hi = bb - cw * 0.4, lo = bb - cw * 0.13, head = cw * 0.075;
    g.lineWidth = aw;
    [[cx - cw * 0.1, true], [cx + cw * 0.1, false]].forEach(([ax, down], i) => {
      const p = easeOut(clamp(arrows * 2 - i * 0.6));
      if (p <= 0) return;
      const [a, b] = down ? [hi, lo] : [lo, hi];
      const bEnd = lerp(a, b, p);
      g.beginPath();
      g.moveTo(ax, a);
      g.lineTo(ax, bEnd);
      const d = down ? head : -head;
      g.moveTo(ax - head, bEnd - d);
      g.lineTo(ax, bEnd);
      g.lineTo(ax + head, bEnd - d);
      g.stroke();
    });
  }
  g.restore();
}
function logoTile(cx, cy, s) {
  if (icon.complete && icon.naturalWidth) g.drawImage(icon, cx - s / 2, cy - s / 2, s, s);
}

// --- made-up photos: small painted landscapes, the same for the same seed -------------------------
const photoCache = new Map();
const SKIES = [
  ['#ff9a62', '#f6587a', '#3a2350'], ['#8fd3ff', '#4f8dff', '#1a2a5a'], ['#ffd86b', '#ff8a5b', '#5b2a4a'],
  ['#a7f0d8', '#3fb6a8', '#12424a'], ['#c9b8ff', '#7a6cff', '#221c4a'], ['#ffe3b0', '#f2a65a', '#6a3b2a'],
];
export function photo(seed, w, h) {
  const key = `${seed}:${w}x${h}`;
  let c = photoCache.get(key);
  if (c) return c;
  c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const p = c.getContext('2d');
  const r = rng(seed * 7919 + 17);
  r();
  const kind = Math.floor(r() * 6);
  const [s1, s2, s3] = SKIES[Math.floor(r() * SKIES.length)];
  const sky = p.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, kind === 4 ? '#0b1430' : s2);
  sky.addColorStop(0.65, kind === 4 ? '#1d2a5c' : s1);
  sky.addColorStop(1, s1);
  p.fillStyle = sky;
  p.fillRect(0, 0, w, h);
  const horizon = h * (0.45 + r() * 0.2);
  if (kind === 0 || kind === 2 || kind === 5) { // sun over hills
    p.fillStyle = 'rgba(255,240,200,0.9)';
    p.beginPath();
    p.arc(w * (0.25 + r() * 0.5), horizon - h * 0.08, Math.min(w, h) * (0.08 + r() * 0.06), 0, Math.PI * 2);
    p.fill();
    for (let k = 0; k < 3; k++) {
      p.fillStyle = mixc(s3, '#000000', k * 0.25);
      p.beginPath();
      p.moveTo(0, h);
      const base = horizon + k * h * 0.12;
      for (let x = 0; x <= w; x += w / 8) p.lineTo(x, base - Math.sin(x / w * 6 + seed + k) * h * 0.05 - r() * h * 0.03);
      p.lineTo(w, h);
      p.fill();
    }
  } else if (kind === 1) { // sea
    p.fillStyle = '#1f5fa8';
    p.fillRect(0, horizon, w, h - horizon);
    p.fillStyle = 'rgba(255,255,255,0.55)';
    for (let k = 0; k < 14; k++) p.fillRect(r() * w, horizon + r() * (h - horizon), w * (0.05 + r() * 0.1), 1.5);
    p.fillStyle = '#e9d8a6';
    p.beginPath();
    p.moveTo(0, h);
    p.quadraticCurveTo(w * 0.4, h * 0.82, w, h * (0.88 + r() * 0.08));
    p.lineTo(w, h);
    p.fill();
  } else if (kind === 3) { // mountains
    for (let k = 0; k < 3; k++) {
      const peak = w * (0.15 + r() * 0.7), top = horizon - h * (0.2 + r() * 0.2) + k * h * 0.08;
      p.fillStyle = mixc('#2b3d5a', s3, k * 0.3);
      p.beginPath();
      p.moveTo(peak - w * 0.6, h);
      p.lineTo(peak, top);
      p.lineTo(peak + w * 0.6, h);
      p.fill();
      p.fillStyle = 'rgba(255,255,255,0.85)';
      p.beginPath();
      p.moveTo(peak - w * 0.08, top + h * 0.08);
      p.lineTo(peak, top);
      p.lineTo(peak + w * 0.08, top + h * 0.08);
      p.fill();
    }
  } else { // a city at night
    for (let k = 0; k < 9; k++) {
      const bw = w * (0.08 + r() * 0.1), bh = h * (0.2 + r() * 0.45), bx = (k / 9) * w + r() * 6;
      p.fillStyle = '#121a33';
      p.fillRect(bx, h - bh, bw, bh);
      p.fillStyle = 'rgba(255,214,120,0.8)';
      for (let y = h - bh + 6; y < h - 4; y += 9) for (let x = bx + 3; x < bx + bw - 4; x += 7) if (r() > 0.55) p.fillRect(x, y, 3, 4);
    }
  }
  photoCache.set(key, c);
  return c;
}
function mixc(a, b, x) {
  return mix(a, b, x);
}
// draw a photo covering the rect (cut to fit)
function photoIn(seed, x, y, w, h, r = 0) {
  const pw = Math.max(8, Math.round(w / 8) * 8), ph = Math.max(8, Math.round(h / 8) * 8);
  const img = photo(seed, pw, ph);
  g.save();
  if (r) {
    rr(x, y, w, h, r);
    g.clip();
  }
  g.drawImage(img, x, y, w, h);
  g.restore();
}

// --- little UI pieces ----------------------------------------------------------------------------
function cursor(x, y, press = 0) {
  g.save();
  g.translate(x, y);
  const s = 1 - press * 0.12;
  g.scale(s, s);
  g.beginPath();
  g.moveTo(0, 0);
  g.lineTo(0, 22);
  g.lineTo(5.5, 17);
  g.lineTo(9.5, 26);
  g.lineTo(13, 24.5);
  g.lineTo(9, 16);
  g.lineTo(16, 16);
  g.closePath();
  g.shadowColor = 'rgba(0,0,0,0.45)';
  g.shadowBlur = 6;
  g.shadowOffsetY = 2;
  g.fillStyle = '#ffffff';
  g.fill();
  g.shadowColor = 'transparent';
  g.strokeStyle = '#111';
  g.lineWidth = 1.2;
  g.stroke();
  g.restore();
}
function ripple(x, y, p) {
  if (p <= 0 || p >= 1) return;
  g.save();
  g.strokeStyle = rgba(C.accent, (1 - p) * 0.9);
  g.lineWidth = 2;
  g.beginPath();
  g.arc(x, y, 6 + p * 26, 0, Math.PI * 2);
  g.stroke();
  g.restore();
}
function check(x, y, s, p, color = C.green, lw = s * 0.16) {
  if (p <= 0) return;
  g.save();
  g.strokeStyle = color;
  g.lineWidth = lw;
  g.lineCap = 'round';
  g.lineJoin = 'round';
  const pts = [[x - s * 0.45, y], [x - s * 0.12, y + s * 0.33], [x + s * 0.5, y - s * 0.35]];
  const l1 = Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]);
  const l2 = Math.hypot(pts[2][0] - pts[1][0], pts[2][1] - pts[1][1]);
  g.setLineDash([(l1 + l2) * clamp(p), l1 + l2]);
  g.beginPath();
  g.moveTo(...pts[0]);
  g.lineTo(...pts[1]);
  g.lineTo(...pts[2]);
  g.stroke();
  g.restore();
}
function heartPath(x, y, s) {
  g.beginPath();
  g.moveTo(x, y + s * 0.35);
  g.bezierCurveTo(x - s * 0.55, y - s * 0.05, x - s * 0.35, y - s * 0.5, x, y - s * 0.2);
  g.bezierCurveTo(x + s * 0.35, y - s * 0.5, x + s * 0.55, y - s * 0.05, x, y + s * 0.35);
  g.closePath();
}
function heart(x, y, s, fill, color = C.ink) {
  heartPath(x, y, s);
  if (fill > 0) {
    g.fillStyle = rgba(C.pink, clamp(fill));
    g.fill();
  }
  g.strokeStyle = fill > 0.5 ? C.pink : color;
  g.lineWidth = 1.6;
  g.stroke();
}
function windowFrame(x, y, w, h, title, { bar = 34, bg = C.win } = {}) {
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.5)';
  g.shadowBlur = 40;
  g.shadowOffsetY = 18;
  fillRR(x, y, w, h, 10, bg);
  g.restore();
  strokeRR(x + 0.5, y + 0.5, w - 1, h - 1, 10, C.line2);
  if (bar) {
    text(title, x + 14, y + 22, { size: 12.5, color: C.soft });
    const bx = x + w - 104;
    g.strokeStyle = C.muted;
    g.lineWidth = 1.1;
    g.beginPath();
    g.moveTo(bx + 10, y + 17);
    g.lineTo(bx + 20, y + 17);
    g.rect(bx + 44, y + 12, 9, 9);
    g.moveTo(bx + 78, y + 12);
    g.lineTo(bx + 87, y + 21);
    g.moveTo(bx + 87, y + 12);
    g.lineTo(bx + 78, y + 21);
    g.stroke();
  }
}
function folderIcon(x, y, s, color = C.amber) {
  g.save();
  g.fillStyle = color;
  rr(x, y + s * 0.1, s * 0.45, s * 0.2, s * 0.06);
  g.fill();
  fillRR(x, y + s * 0.22, s, s * 0.68, s * 0.1, color);
  g.fillStyle = 'rgba(0,0,0,0.12)';
  g.fillRect(x, y + s * 0.3, s, s * 0.04);
  g.restore();
}
function fileIcon(x, y, s, color) {
  fillRR(x, y, s * 0.78, s, s * 0.1, '#e8edf5');
  fillRR(x, y + s * 0.55, s * 0.78, s * 0.45, s * 0.1, color);
  g.fillStyle = '#e8edf5';
  g.fillRect(x, y + s * 0.55, s * 0.78, s * 0.08);
}
function phoneFrame(x, y, w, h, fn) {
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.55)';
  g.shadowBlur = 30;
  g.shadowOffsetY = 14;
  fillRR(x, y, w, h, w * 0.16, '#0a0c10');
  g.restore();
  strokeRR(x + 1, y + 1, w - 2, h - 2, w * 0.16, '#3a4354', 2);
  const m = w * 0.05;
  g.save();
  rr(x + m, y + m, w - 2 * m, h - 2 * m, w * 0.12);
  g.clip();
  fillRR(x + m, y + m, w - 2 * m, h - 2 * m, 0, '#11151c');
  fn(x + m, y + m, w - 2 * m, h - 2 * m);
  g.restore();
  fillRR(x + w * 0.36, y + m + 5, w * 0.28, 7, 4, '#000');
}
function monitorFrame(x, y, w, h, fn) {
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.5)';
  g.shadowBlur = 30;
  g.shadowOffsetY = 14;
  fillRR(x, y, w, h, 12, '#0a0c10');
  g.restore();
  strokeRR(x + 1, y + 1, w - 2, h - 2, 12, '#3a4354', 2);
  g.save();
  rr(x + 8, y + 8, w - 16, h - 16, 6);
  g.clip();
  fn(x + 8, y + 8, w - 16, h - 16);
  g.restore();
  fillRR(x + w * 0.44, y + h, w * 0.12, h * 0.14, 0, '#2a303c');
  fillRR(x + w * 0.32, y + h * 1.12, w * 0.36, 8, 4, '#2a303c');
}
// time of an event of this scene, in seconds from the scene's start
function ev(sc, i) {
  return EVENTS[sc.id][i].at * sc.length;
}
// moves through [time, x, y] keyframes (seconds), easing between them
function path(keys, t) {
  if (t <= keys[0][0]) return [keys[0][1], keys[0][2]];
  for (let i = 1; i < keys.length; i++) {
    if (t <= keys[i][0]) {
      const [t0, x0, y0] = keys[i - 1], [t1, x1, y1] = keys[i];
      const p = easeInOut((t - t0) / (t1 - t0));
      return [lerp(x0, x1, p), lerp(y0, y1, p)];
    }
  }
  const k = keys[keys.length - 1];
  return [k[1], k[2]];
}
function clickPress(t, at) {
  const d = t - at;
  return d > -0.08 && d < 0.12 ? 1 - Math.abs(d - 0.02) / 0.1 : 0;
}

// --- background ----------------------------------------------------------------------------------
function backdrop(t, scene) {
  g.fillStyle = C.bg;
  g.fillRect(0, 0, L.W, L.H);
  const glow = SCENE_GLOW[scene.id];
  const prev = SCENE_GLOW[scenes[Math.max(0, scene.index - 1)].id];
  const k = smooth(scene.local / 0.8);
  const blob = (x, y, r, color, a) => {
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, rgba(color, a));
    gr.addColorStop(1, rgba(color, 0));
    g.fillStyle = gr;
    g.fillRect(0, 0, L.W, L.H);
  };
  blob(L.W * (0.22 + 0.05 * Math.sin(t * 0.3)), L.H * (0.28 + 0.04 * Math.cos(t * 0.25)), L.W * 0.55, C.blue, 0.13);
  blob(L.W * (0.8 + 0.04 * Math.cos(t * 0.22)), L.H * (0.75 + 0.05 * Math.sin(t * 0.3)), L.W * 0.5, prev, 0.1 * (1 - k));
  blob(L.W * (0.8 + 0.04 * Math.cos(t * 0.22)), L.H * (0.75 + 0.05 * Math.sin(t * 0.3)), L.W * 0.5, glow, 0.1 * k);
  // dust
  const r = rng(99);
  for (let i = 0; i < 46; i++) {
    const x0 = r() * L.W, speed = 6 + r() * 14, size = 0.6 + r() * 1.6, ph = r() * 10;
    const y = (((r() * L.H - t * speed) % L.H) + L.H) % L.H;
    const x = x0 + Math.sin(t * 0.4 + ph) * 12;
    g.fillStyle = rgba('#c8d8ff', 0.1 + 0.12 * Math.sin(t * 0.7 + ph) ** 2);
    g.beginPath();
    g.arc(x, y, size, 0, Math.PI * 2);
    g.fill();
  }
}

// --- the title column and the phase rail -----------------------------------------------------------
function titleBlock(sc) {
  const T = L.text;
  const out = 1 - smooth(1 - sc.remaining / OUTRO);
  withAlpha(out, () => {
    const a = smooth(mark(sc.local, 0.05, 0.4));
    withAlpha(a, () => {
      const w = text(sc.phase.toUpperCase(), T.x, T.y, { size: 14, weight: 700, color: C.accent, family: TEXT });
      g.save();
      g.globalAlpha *= 1;
      g.restore();
      if (sc.isNew) {
        fillRR(T.x + w + 12, T.y - 15, 46, 20, 10, rgba(C.amber, 0.18));
        text('NEW', T.x + w + 35, T.y - 0.5, { size: 11.5, weight: 700, color: C.amber, align: 'center' });
      }
    });
    const lines = wrap(sc.title, T.w, T.size, 700, DISPLAY);
    const lh = T.size * 1.12;
    let k = 0;
    lines.forEach((line, li) => {
      const words = line.split(' ');
      font(T.size, 700, DISPLAY);
      let x = T.x;
      const y = T.y + 26 + T.size + li * lh;
      words.forEach((word) => {
        const p = easeOut(mark(sc.local, 0.15 + k * 0.07, 0.45));
        k++;
        g.save();
        g.beginPath();
        g.rect(x - 4, y - T.size * 1.05, g.measureText(word).width + 10, T.size * 1.35);
        g.clip();
        withAlpha(p, () => text(word, x, y + (1 - p) * T.size * 0.7, { size: T.size, weight: 700, family: DISPLAY }));
        g.restore();
        font(T.size, 700, DISPLAY);
        x += g.measureText(word + ' ').width;
      });
    });
    const subY = T.y + 26 + T.size + lines.length * lh + 10;
    const sp = smooth(mark(sc.local, 0.7, 0.6));
    withAlpha(sp, () => {
      wrap(sc.sub, T.w, T.sub, 400, TEXT).forEach((line, i) =>
        text(line, T.x, subY + T.sub * 1.5 * i + (1 - sp) * 8, { size: T.sub, color: C.muted }));
    });
  });
}
function railDot(i) {
  const R = L.rail;
  return [lerp(R.x0, R.x1, i / (PHASES.length - 1)), R.y];
}
function rail(scene, show) {
  if (show <= 0) return;
  const R = L.rail;
  const pi = PHASES.findIndex((p) => p.id === scene.id); // -1 in the hook and the end
  const done = scene.id === 'end' ? PHASES.length : pi;
  const fill = pi >= 0 ? smooth(1 - scene.remaining / OUTRO) : 0;
  withAlpha(show, () => {
    g.strokeStyle = C.line2;
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(R.x0, R.y);
    g.lineTo(R.x1, R.y);
    g.stroke();
    const step = (R.x1 - R.x0) / (PHASES.length - 1);
    const reach = done < 0 ? R.x0 : Math.min(R.x1, R.x0 + step * (Math.max(0, done - 1) + (pi >= 0 ? fill : 0)));
    if (done > 0) {
      g.strokeStyle = C.blue;
      g.beginPath();
      g.moveTo(R.x0, R.y);
      g.lineTo(reach, R.y);
      g.stroke();
    }
    PHASES.forEach((p, i) => {
      const [x, y] = railDot(i);
      const passed = i < done || (i === pi && fill > 0.85);
      const current = i === pi;
      g.beginPath();
      g.arc(x, y, current ? 7 : 5, 0, Math.PI * 2);
      g.fillStyle = passed ? C.blue : current ? C.bg : C.bg2;
      g.fill();
      g.strokeStyle = passed || current ? C.blue : C.line2;
      g.lineWidth = 2;
      g.stroke();
      if (current) {
        g.beginPath();
        g.arc(x, y, 12 + 2 * Math.sin(scene.local * Math.PI * 2 * (60 / (BEAT * 4 / FPS * 60))), 0, Math.PI * 2);
        g.strokeStyle = rgba(C.blue, 0.35);
        g.stroke();
      }
      text(p.phase, x, y + 26, { size: format === 'tall' ? 11.5 : 12.5, weight: current ? 650 : 450,
        color: current ? C.ink : C.muted, align: 'center' });
    });
  });
}

// --- the card for a scene: springs in, then flies into its dot on the rail ------------------------
function stage(sc, render) {
  const pi = PHASES.findIndex((p) => p.id === sc.id);
  const enter = spring(mark(sc.local, 0, 0.6));
  const leave = smooth(1 - sc.remaining / OUTRO);
  const [dx, dy] = railDot(pi);
  const beat = sc.frame % BEAT;
  const pulse = Math.exp(-beat / 3) * 0.004;
  const s = L.card.s * lerp(0.9, 1, enter) * (1 + pulse) * lerp(1, 0.04, easeInOut(leave));
  const cx = lerp(L.card.x, dx, easeInOut(leave)), cy = lerp(L.card.y + (1 - enter) * 30, dy, easeInOut(leave));
  g.save();
  g.translate(cx, cy);
  g.rotate((1 - enter) * 0.03 - leave * 0.25);
  g.scale(s, s);
  g.translate(-CW / 2, -CH / 2);
  withAlpha(clamp(enter * 3) * (1 - smooth((leave - 0.75) / 0.25)), () => render(sc));
  g.restore();
}

// --- the scenes ----------------------------------------------------------------------------------
function hook(sc, t) {
  const tall = format === 'tall';
  const P = tall
    ? { phone: [170, 820, 1.05], cloud: [360, 560, 190], pc: [520, 800, 0.72], text: [360, 270], brand: [360, 930] }
    : { phone: [300, 420, 1], cloud: [640, 300, 190], pc: [985, 410, 1], text: [640, 140], brand: [640, 560] };
  const lt = sc.local, len = sc.length;
  const tA = 0.52 * len, tB = 0.78 * len; // the cloud appears; the name appears
  const gone = smooth(mark(lt, tB - 0.2, 0.6));
  // the two questions, word by word
  const lines = ['Your photos, files and notes are in iCloud.', 'Your PC runs Windows.'];
  withAlpha(1 - smooth(mark(lt, tA + 0.5, 0.5)), () => {
    let k = 0;
    lines.forEach((line, li) => {
      const size = tall ? 38 : 40;
      const wrapped = wrap(line, tall ? 620 : 1100, size, 700, DISPLAY);
      wrapped.forEach((wl, wi) => {
        const y = P.text[1] + (li * wrapped.length + wi) * size * 1.25 + (li && tall ? size * 1.25 : 0);
        font(size, 700, DISPLAY);
        const total = g.measureText(wl).width;
        let x = P.text[0] - total / 2;
        wl.split(' ').forEach((word) => {
          const p = easeOut(mark(lt, 0.2 + k * 0.12 + li * 0.5, 0.5));
          k++;
          withAlpha(p, () => text(word, x, y + (1 - p) * 16, { size, weight: 700, family: DISPLAY,
            color: li ? C.accent : C.ink }));
          font(size, 700, DISPLAY);
          x += g.measureText(word + ' ').width;
        });
      });
    });
  });
  // the phone and the PC
  const devIn = easeOut(mark(lt, 0.4, 0.9));
  withAlpha(devIn * (1 - gone), () => {
    const [px, py, ps] = P.phone;
    g.save();
    g.translate(px - (1 - devIn) * 40, py);
    g.scale(ps, ps);
    phoneFrame(-75, -150, 150, 300, (x, y, w) => {
      text('Photos', x + 12, y + 34, { size: 14, weight: 700 });
      for (let i = 0; i < 12; i++) photoIn(i * 3 + 1, x + 8 + (i % 3) * ((w - 16) / 3), y + 46 + Math.floor(i / 3) * 44,
        (w - 16) / 3 - 3, 41, 3);
      fillRR(x + 8, y + 232, w - 16, 34, 8, '#1b212b');
      text('Notes · Garden plan', x + 16, y + 253, { size: 10.5, color: C.soft });
    });
    g.restore();
    const [mx, my, ms] = P.pc;
    g.save();
    g.translate(mx + (1 - devIn) * 40, my);
    g.scale(ms, ms);
    monitorFrame(-190, -125, 380, 240, (x, y, w, h) => {
      const wall = g.createLinearGradient(x, y, x + w, y + h);
      wall.addColorStop(0, '#16233f');
      wall.addColorStop(1, '#2a1838');
      g.fillStyle = wall;
      g.fillRect(x, y, w, h);
      // folders arrive once the cloud links them
      const names = ['iCloud Photos', 'iCloud Drive', 'Apple Notes'];
      names.forEach((n, i) => {
        const p = spring(mark(lt, tA + 0.5 + i * 0.25, 0.6));
        if (p <= 0) return;
        g.save();
        const fx = x + 40 + i * 108, fy = y + 70;
        g.translate(fx + 26, fy + 26);
        g.scale(p, p);
        folderIcon(-26, -26, 52, [C.pink, C.blue2, C.amber][i]);
        g.restore();
        withAlpha(p, () => text(n, fx + 26, fy + 78, { size: 11, align: 'center', color: C.ink }));
      });
      fillRR(x, y + h - 18, w, 18, 0, 'rgba(10,12,16,0.85)');
      withAlpha(1 - smooth(mark(lt, tA + 0.4, 0.5)), () => text('?', x + w / 2, y + h / 2 + 10,
        { size: 46, weight: 700, color: rgba(C.muted, 0.5), align: 'center', family: DISPLAY }));
    });
    g.restore();
    // the link: dots from the phone to the cloud to the PC
    const link = smooth(mark(lt, tA + 0.2, 0.5));
    if (link > 0) {
      const [cx, cy] = P.cloud;
      const a = [px + 75 * ps + 10, py - 20], b = [cx, cy + 10], c = [mx - 190 * ms - 10, my - 20];
      g.save();
      g.setLineDash([2, 9]);
      g.lineCap = 'round';
      g.lineWidth = 3;
      g.strokeStyle = rgba(C.accent, 0.5 * link);
      g.beginPath();
      g.moveTo(...a);
      g.quadraticCurveTo(a[0] + 40, b[1], b[0] - 70, b[1] + 10);
      g.moveTo(b[0] + 70, b[1] + 10);
      g.quadraticCurveTo(c[0] - 40, b[1], ...c);
      g.stroke();
      g.restore();
      for (let i = 0; i < 6; i++) {
        const q = ((lt * 0.6 + i / 6) % 1);
        const left = q < 0.5;
        const u = left ? q * 2 : (q - 0.5) * 2;
        const [x0, y0, x1, y1] = left ? [a[0], a[1], b[0] - 70, b[1] + 10] : [b[0] + 70, b[1] + 10, c[0], c[1]];
        const x = lerp(x0, x1, u), y = lerp(y0, y1, u) - Math.sin(u * Math.PI) * 12;
        g.fillStyle = rgba(C.accent, link * Math.sin(u * Math.PI));
        g.beginPath();
        g.arc(x, y, 3.2, 0, Math.PI * 2);
        g.fill();
      }
    }
  });
  // the cloud draws itself
  const [cx, cy, cs] = P.cloud;
  const draw = easeInOut(mark(lt, tA - 0.3, 1.1));
  const brandMove = easeInOut(mark(lt, tB - 0.2, 0.8));
  const ccy = lerp(cy, tall ? 640 : 300, brandMove);
  skyMark(cx, ccy, lerp(cs, tall ? 200 : 210, brandMove), C.blue, { draw, arrows: mark(lt, tA + 0.5, 0.8), glow: draw });
  // the name
  const nb = spring(mark(lt, tB, 0.7));
  withAlpha(nb, () => {
    const [bx, by] = P.brand;
    text('Skyfold', bx, by + (1 - nb) * 20 - (tall ? 70 : 60), { size: tall ? 86 : 84, weight: 700, family: DISPLAY,
      align: 'center' });
    withAlpha(smooth(mark(lt, tB + 0.35, 0.6)), () =>
      text('iCloud on Windows, in real folders.', bx, by + (tall ? 10 : 6), { size: tall ? 28 : 28, color: C.accent,
        align: 'center', family: DISPLAY, weight: 600 }));
  });
}

function tray(sc) {
  const t = sc.local;
  const tc = ev(sc, 0), tok = ev(sc, 1);
  // desktop
  const wall = g.createLinearGradient(0, 0, CW, CH);
  wall.addColorStop(0, '#1a2747');
  wall.addColorStop(0.6, '#2b1d43');
  wall.addColorStop(1, '#3b2140');
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.5)';
  g.shadowBlur = 40;
  g.shadowOffsetY = 18;
  fillRR(0, 0, CW, CH, 14, '#1a2747');
  g.restore();
  g.save();
  rr(0, 0, CW, CH, 14);
  g.clip();
  g.fillStyle = wall;
  g.fillRect(0, 0, CW, CH);
  for (let i = 0; i < 3; i++) {
    g.fillStyle = rgba('#8fb8ff', 0.06);
    g.beginPath();
    g.ellipse(120 + i * 200, 180 + i * 40, 260, 90, -0.3, 0, Math.PI * 2);
    g.fill();
  }
  // taskbar
  g.fillStyle = 'rgba(16,19,26,0.92)';
  g.fillRect(0, CH - 40, CW, 40);
  for (let i = 0; i < 5; i++) fillRR(220 + i * 44, CH - 32, 26, 26, 6, i === 0 ? '#3b82f6' : 'rgba(255,255,255,0.12)');
  // tray: ^, Skyfold, wifi, sound, clock
  text('^', 470, CH - 14, { size: 14, color: C.soft, align: 'center' });
  const ix = 506, iy = CH - 20;
  const hover = clamp(1 - Math.abs(t - tc) / 0.4);
  fillRR(ix - 16, iy - 15, 32, 30, 6, rgba('#ffffff', 0.12 * hover + (t > tc ? 0.1 : 0)));
  skyMark(ix, iy, 26, '#ffffff', { lw: 1.9, aw: 1.4, inset: 0.03 });
  g.strokeStyle = C.soft;
  g.lineWidth = 1.5;
  for (let k = 0; k < 3; k++) {
    g.beginPath();
    g.arc(544, iy + 6, 4 + k * 4, -Math.PI * 0.75, -Math.PI * 0.25);
    g.stroke();
  }
  g.beginPath();
  g.moveTo(572, iy - 3);
  g.lineTo(576, iy - 3);
  g.lineTo(581, iy - 7);
  g.lineTo(581, iy + 7);
  g.lineTo(576, iy + 3);
  g.lineTo(572, iy + 3);
  g.closePath();
  g.stroke();
  text('14:32', 650, CH - 23, { size: 11.5, color: C.ink, align: 'right' });
  text('25.11.2026', 650, CH - 9, { size: 11.5, color: C.ink, align: 'right' });
  // the panel slides up
  const open = spring(mark(t, tc + 0.05, 0.6));
  if (open > 0) {
    const px = 368, pw = 300, ph = 404, py = CH - 48 - ph + (1 - open) * 40;
    withAlpha(clamp(open * 2), () => panel(px, py, pw, ph, t, tc, tok));
  }
  g.restore();
  const [x, y] = path([[0, 250, 200], [tc - 0.05, ix + 2, iy - 2], [tc + 0.8, 250, 260]], t);
  cursor(x, y, clickPress(t, tc));
  ripple(ix, iy, (t - tc) / 0.5);
}
function panel(x, y, w, h, t, tc, tok) {
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.55)';
  g.shadowBlur = 30;
  g.shadowOffsetY = 12;
  fillRR(x, y, w, h, 12, '#1a1f28');
  g.restore();
  strokeRR(x + 0.5, y + 0.5, w - 1, h - 1, 12, C.line2);
  // header
  fillRR(x + 14, y + 14, 34, 34, 9, '#243049');
  skyMark(x + 31, y + 31, 28, '#a9c4ef', { lw: 1.8, aw: 1.3, inset: 0.12 });
  text('Skyfold', x + 58, y + 30, { size: 17, weight: 700, family: DISPLAY });
  const ok = t >= tok;
  if (ok) {
    check(x + 64, y + 42, 9, (t - tok) / 0.3, C.green, 2);
    text('Everything is in sync', x + 74, y + 46, { size: 11.5, color: C.green });
  } else {
    g.save();
    g.translate(x + 63, y + 42);
    g.rotate(t * 6);
    g.strokeStyle = C.accent;
    g.lineWidth = 1.8;
    g.beginPath();
    g.arc(0, 0, 4.5, 0, Math.PI * 1.4);
    g.stroke();
    g.restore();
    text('Syncing…', x + 74, y + 46, { size: 11.5, color: C.accent });
  }
  // rows
  fillRR(x + 12, y + 62, w - 24, 150, 9, '#212734');
  const p = easeOut(mark(t, tc + 0.3, tok - tc - 0.3));
  const rows = [
    ['Photos', `${num(1284 * p)} photos · ${num(36 * p)} videos`, C.pink],
    ['iCloud Drive', `${num(4210 * p)} files · ${(31 * p).toFixed(1)} GB`, C.blue2],
    ['Notes', `${num(86 * p)} notes`, C.amber],
  ];
  rows.forEach(([name, sub, color], i) => {
    const ry = y + 62 + i * 50;
    fillRR(x + 24, ry + 12, 26, 26, 7, rgba(color, 0.2));
    strokeRR(x + 24.5, ry + 12.5, 25, 25, 7, rgba(color, 0.6));
    if (i === 0) {
      g.strokeStyle = color;
      g.lineWidth = 1.4;
      g.strokeRect(x + 30, ry + 19, 14, 11);
    } else if (i === 1) folderIcon(x + 30, ry + 18, 14, color);
    else {
      g.strokeStyle = color;
      g.lineWidth = 1.4;
      g.strokeRect(x + 32, ry + 18, 10, 14);
    }
    text(name, x + 60, ry + 24, { size: 13, weight: 650 });
    text(sub, x + 60, ry + 40, { size: 11, color: C.muted });
    text('›', x + w - 28, ry + 32, { size: 16, color: C.muted });
    if (i < 2) {
      g.fillStyle = C.line2;
      g.fillRect(x + 60, ry + 50, w - 84, 1);
    }
  });
  // storage
  const sy = y + 230;
  text('iCloud storage', x + 16, sy, { size: 11.5, color: C.soft });
  text('74 GB of 200 GB', x + w - 16, sy, { size: 11.5, color: C.soft, align: 'right' });
  fillRR(x + 16, sy + 9, w - 32, 6, 3, C.line2);
  const bw = w - 32;
  const segs = [[0.19, C.pink], [0.145, C.blue2], [0.035, C.amber]];
  let sx = x + 16;
  segs.forEach(([f, color]) => {
    const ww = bw * f * p;
    g.fillStyle = color;
    g.fillRect(sx, sy + 9, ww, 6);
    sx += ww;
  });
  // recent activity
  text('Recent activity', x + 16, sy + 44, { size: 11.5, color: C.muted, weight: 600 });
  const acts = [['Budget 2026.xlsx', 'Downloaded · iCloud Drive/Projects', 'just now'],
    ['IMG_2048.HEIC', 'Downloaded · Photos/2026/11', '1 min ago']];
  acts.forEach(([n, s, when], i) => {
    const a = easeOut(mark(t, tc + 0.6 + i * 0.25, 0.4));
    withAlpha(a, () => {
      const ay = sy + 68 + i * 38 + (1 - a) * 8;
      text('↓', x + 20, ay + 6, { size: 13, color: C.accent });
      text(n, x + 38, ay, { size: 12 });
      text(s, x + 38, ay + 15, { size: 10.5, color: C.muted });
      text(when, x + w - 16, ay, { size: 10.5, color: C.muted, align: 'right' });
    });
  });
  fillRR(x + 12, y + h - 44, w - 24, 32, 8, '#243049');
  text('⟳  Sync now', x + 26, y + h - 23, { size: 12.5, weight: 650, color: C.accent });
}

function photos(sc) {
  const t = sc.local;
  windowFrame(0, 0, CW, CH, 'iCloud Photos — File Explorer');
  // address bar
  fillRR(14, 40, CW - 28, 28, 6, C.win2);
  const crumbs = ['This PC', 'Pictures', 'iCloud Photos', '2026', '11'];
  let cx = 26;
  crumbs.forEach((c, i) => {
    cx += text(c, cx, 59, { size: 12, color: i === crumbs.length - 1 ? C.ink : C.soft }) + 8;
    if (i < crumbs.length - 1) cx += text('›', cx, 59, { size: 12, color: C.muted }) + 8;
  });
  // nav pane
  const nav = [['Home', 0], ['Pictures', 0], ['iCloud Photos', 1], ['2025', 2], ['2026', 2], ['Albums', 2],
    ['iCloud Drive', 0], ['Documents', 0]];
  nav.forEach(([n, lvl], i) => {
    const y = 96 + i * 30;
    if (n === '2026') fillRR(10, y - 18, 140, 26, 5, rgba(C.blue, 0.22));
    folderIcon(18 + (lvl ? (lvl - 1) * 12 + 12 : 0), y - 13, 15, n.startsWith('iCloud') ? C.blue2 : C.amber);
    text(n, 40 + (lvl ? (lvl - 1) * 12 + 12 : 0), y, { size: 12, color: C.ink });
  });
  g.fillStyle = C.line;
  g.fillRect(160, 78, 1, CH - 110);
  // the grid of photos arriving from iCloud
  const cols = 5, tw = 86, gap = 14, gx = 182, gy = 92;
  const names = [2031, 2032, 2033, 2034, 2035, 2036, 2037, 2038, 2039, 2040, 2041, 2042, 2043, 2044, 2045];
  names.forEach((n, i) => {
    const at = 0.6 + i * 0.16;
    const p = spring(mark(t, at, 0.7));
    if (p <= 0) return;
    const x = gx + (i % cols) * (tw + gap), y = gy + Math.floor(i / cols) * (tw + 26);
    g.save();
    g.translate(x + tw / 2, lerp(-140, y + tw / 2, p));
    g.scale(lerp(0.6, 1, p), lerp(0.6, 1, p));
    withAlpha(clamp(p * 2), () => {
      photoIn(n, -tw / 2, -tw / 2, tw, tw * 0.78, 5);
      if (n % 4 === 1) { // a Live Photo's video sits next to it, in the same folder
        fillRR(-tw / 2 + 4, -tw / 2 + 4, 26, 14, 7, 'rgba(0,0,0,0.5)');
        text('LIVE', -tw / 2 + 17, -tw / 2 + 14.5, { size: 8.5, weight: 700, align: 'center' });
      }
      text(`IMG_${n}.HEIC`, 0, tw * 0.28 + 14, { size: 10.5, color: C.soft, align: 'center' });
    });
    g.restore();
  });
  // status bar
  g.fillStyle = C.line;
  g.fillRect(0, CH - 30, CW, 1);
  const tok = ev(sc, 4);
  if (t < tok) {
    const p = easeInOut(mark(t, 0.5, tok - 0.5));
    text(`Downloading · ${num(lerp(0, 1284, p))} of 1,284 photos`, 16, CH - 11, { size: 11.5, color: C.accent });
    fillRR(CW - 196, CH - 19, 180, 5, 3, C.line2);
    fillRR(CW - 196, CH - 19, 180 * p, 5, 3, C.blue);
  } else {
    check(22, CH - 15, 10, (t - tok) / 0.3, C.green, 2);
    text('1,284 photos · 36 videos · all on this PC', 34, CH - 11, { size: 11.5, color: C.green });
  }
}

function gallery(sc) {
  const t = sc.local, len = sc.length;
  const tClick = ev(sc, 0), tHeart = ev(sc, 1);
  windowFrame(0, 0, CW, CH, 'Photos — Skyfold', { bg: '#15181e' });
  // sidebar
  const sw = 150;
  fillRR(1, 34, sw, CH - 35, 0, '#1a1e26');
  const side = [['Library', true], ['Recently added'], ['Favourites'], ['Videos'], ['Live Photos'], ['Screenshots']];
  side.forEach(([n, sel], i) => {
    const y = 64 + i * 30;
    if (sel) fillRR(8, y - 18, sw - 16, 26, 6, rgba(C.blue, 0.25));
    text(n, 22, y, { size: 12.5, color: sel ? C.ink : C.soft, weight: sel ? 650 : 400 });
  });
  text('Albums', 22, 262, { size: 11, color: C.muted, weight: 700 });
  ['Holidays', 'Family', 'Garden'].forEach((n, i) => text(n, 22, 290 + i * 28, { size: 12.5, color: C.soft }));
  // grid, gliding up as the wheel turns (the smooth scrolling of the new window)
  const gx = sw + 4, gw = CW - gx - 4, cols = 5, ts = gw / cols;
  const glides = [[0.5, 70], [1.2, 110], [1.9, 90]];
  let scroll = 0;
  glides.forEach(([at, d]) => { scroll += d * easeOut(mark(t, at, 0.5)); });
  const target = { row: 5, col: 2 };
  g.save();
  rr(gx, 34, gw, CH - 35, 0);
  g.clip();
  for (let row = 0; row < 9; row++) {
    for (let col = 0; col < cols; col++) {
      const x = gx + col * ts, y = 34 + row * ts - scroll;
      if (y > CH || y + ts < 34) continue;
      photoIn(row * 7 + col * 3 + 11, x, y, ts - 2, ts - 2);
      if ((row * cols + col) % 9 === 4) heart(x + ts - 16, y + ts - 16, 10, 1, '#fff');
    }
  }
  // header over the photos
  const hg = g.createLinearGradient(0, 34, 0, 110);
  hg.addColorStop(0, 'rgba(10,12,16,0.85)');
  hg.addColorStop(1, 'rgba(10,12,16,0)');
  g.fillStyle = hg;
  g.fillRect(gx, 34, gw, 76);
  text(scroll > 150 ? 'November 2026' : 'October – November 2026', gx + 16, 64, { size: 19, weight: 700, family: DISPLAY });
  text('1,284 items', gx + 16, 82, { size: 11.5, color: C.soft });
  // the Years / Months / All Photos pill
  fillRR(gx + gw / 2 - 120, CH - 46, 240, 30, 15, 'rgba(32,37,47,0.92)');
  ['Years', 'Months', 'All Photos'].forEach((n, i) => {
    const x = gx + gw / 2 - 120 + 8 + i * 76;
    if (i === 2) fillRR(x, CH - 42, 72, 22, 11, '#3a4354');
    text(n, x + 36, CH - 27, { size: 11.5, align: 'center', color: i === 2 ? C.ink : C.soft, weight: 600 });
  });
  g.restore();
  // the viewer opens from the photo clicked
  const tile = { x: gx + target.col * ts, y: 34 + target.row * ts - scroll, w: ts - 2, h: ts - 2 };
  const vo = easeInOut(mark(t, tClick + 0.05, 0.5));
  if (vo > 0) {
    const vx = 1, vy = 34, vw = CW - 2, vh = CH - 35;
    withAlpha(clamp(vo * 1.6), () => fillRR(vx, vy, vw, vh, 0, '#0b0d11'));
    const area = { x: 60, y: 84, w: CW - 120, h: CH - 160 };
    const fw = area.h * 1.33;
    const fx = area.x + (area.w - fw) / 2;
    const r = { x: lerp(tile.x, fx, vo), y: lerp(tile.y, area.y, vo), w: lerp(tile.w, fw, vo), h: lerp(tile.h, area.h, vo) };
    photoIn(target.row * 7 + target.col * 3 + 11, r.x, r.y, r.w, r.h, 4 * vo);
    withAlpha(smooth(mark(t, tClick + 0.35, 0.3)), () => {
      text('‹  Library', 18, 64, { size: 13, color: C.accent, weight: 600 });
      text('Tuesday, 18 November 2026', CW / 2, 58, { size: 13, weight: 700, align: 'center' });
      text('14:05 · IMG_2048.HEIC · 812 of 1,284', CW / 2, 74, { size: 10.5, color: C.muted, align: 'center' });
      // heart, info, delete
      const hp = easeOut(mark(t, tHeart, 0.3));
      const burst = mark(t, tHeart, 0.6);
      g.save();
      g.translate(CW - 92, 60);
      const s = 1 + Math.sin(clamp(burst) * Math.PI) * 0.35;
      g.scale(s, s);
      heart(0, 0, 18, hp);
      g.restore();
      if (burst > 0 && burst < 1) {
        for (let i = 0; i < 10; i++) {
          const a = (i / 10) * Math.PI * 2;
          const d = 10 + burst * 26;
          g.fillStyle = rgba(i % 2 ? C.pink : C.amber, 1 - burst);
          g.beginPath();
          g.arc(CW - 92 + Math.cos(a) * d, 60 + Math.sin(a) * d, 2.4, 0, Math.PI * 2);
          g.fill();
        }
      }
      g.strokeStyle = C.ink;
      g.lineWidth = 1.5;
      g.beginPath();
      g.arc(CW - 58, 60, 8, 0, Math.PI * 2);
      g.stroke();
      text('i', CW - 58, 64.5, { size: 11, weight: 700, align: 'center' });
      g.strokeRect(CW - 30, 54, 10, 12);
      g.beginPath();
      g.moveTo(CW - 33, 54);
      g.lineTo(CW - 17, 54);
      g.stroke();
      // film strip
      for (let i = -6; i <= 6; i++) {
        const sz = i === 0 ? 34 : 26;
        const x = CW / 2 + i * 30 - sz / 2 + (i > 0 ? 6 : i < 0 ? -6 : 0);
        photoIn(target.row * 7 + target.col * 3 + 11 + i * 3, x, CH - 52 + (34 - sz) / 2, sz, sz, 3);
      }
    });
    // the toast and the iPhone that gets the heart too
    const toast = smooth(mark(t, tHeart + 0.15, 0.3)) * (1 - smooth(mark(t, len - 0.7, 0.3)));
    withAlpha(toast, () => {
      fillRR(CW / 2 - 90, CH - 108, 180, 30, 15, 'rgba(40,46,58,0.95)');
      text('Added to Favourites', CW / 2, CH - 88.5, { size: 12, align: 'center', weight: 600 });
    });
  }
  const ph = spring(mark(t, tHeart + 0.4, 0.7));
  if (ph > 0) {
    g.save();
    g.translate(CW - 30 + (1 - ph) * 140, CH - 40);
    g.rotate(0.06);
    phoneFrame(-110, -230, 120, 240, (x, y, w, h) => {
      text('18 November', x + 10, y + 30, { size: 11, weight: 700 });
      photoIn(target.row * 7 + target.col * 3 + 11, x, y + 50, w, w * 0.75);
      heart(x + w / 2, y + 50 + w * 0.75 + 30, 14, 1);
      text('Favourite', x + w / 2, y + 50 + w * 0.75 + 58, { size: 10, color: C.pink, align: 'center', weight: 600 });
    });
    g.restore();
    withAlpha(smooth(mark(t, tHeart + 0.8, 0.4)), () => {
      fillRR(CW - 150, 140, 124, 24, 12, 'rgba(60,24,36,0.95)');
      text('on your iPhone too', CW - 88, 156.5, { size: 11.5, color: C.pink, align: 'center', weight: 650 });
    });
  }
  // the cursor: wheel over the grid, a click on the photo, a click on the heart
  const keys = [[0, 420, 360], [0.45, 430, 300], [2.3, 440, 300], [tClick - 0.05, tile.x + tile.w * 0.55 + 0,
    34 + target.row * ts - (70 + 110 + 90) + tile.h * 0.5], [tClick + 0.6, 480, 240], [tHeart - 0.05, CW - 92, 62],
  [tHeart + 0.8, CW - 150, 120]];
  const [cx, cy] = path(keys, t);
  if (t < 2.5) { // the wheel
    const turn = glides.some(([at]) => t > at - 0.05 && t < at + 0.25);
    if (turn) {
      g.strokeStyle = rgba(C.accent, 0.8);
      g.lineWidth = 2;
      g.beginPath();
      g.moveTo(cx + 22, cy + 6);
      g.lineTo(cx + 22, cy - 8);
      g.moveTo(cx + 18, cy - 4);
      g.lineTo(cx + 22, cy - 9);
      g.lineTo(cx + 26, cy - 4);
      g.stroke();
    }
  }
  cursor(cx, cy, clickPress(t, tClick) + clickPress(t, tHeart));
  ripple(tile.x + tile.w * 0.55, tile.y + tile.h * 0.5, (t - tClick) / 0.45);
  ripple(CW - 92, 60, (t - tHeart) / 0.45);
}

function drive(sc) {
  const t = sc.local;
  const tSave = ev(sc, 0), tOk = ev(sc, 1);
  const ww = 440;
  windowFrame(0, 0, ww, CH, 'Projects — File Explorer');
  fillRR(12, 40, ww - 24, 28, 6, C.win2);
  text('iCloud Drive  ›  Projects', 24, 59, { size: 12, color: C.soft });
  text('Name', 46, 92, { size: 11, color: C.muted });
  text('Date modified', 250, 92, { size: 11, color: C.muted });
  text('Status', ww - 22, 92, { size: 11, color: C.muted, align: 'right' });
  const newIn = easeOut(mark(t, tOk + 0.9, 0.5));
  const files = [
    ['Budget 2026.xlsx', '#21a366'], ['Letter.docx', '#2b7cd3'], ['House plans.pdf', '#e5533d'],
    ['Trip photos.zip', '#c9a227'], ['Ideas.txt', '#8a94a6'],
  ];
  const rowsY = 104;
  // a new file arrives from the iPhone
  withAlpha(newIn, () => {
    const y = rowsY;
    fillRR(10, y, ww - 20, 34, 6, rgba(C.green, 0.1 * (1 - smooth(mark(t, tOk + 1.6, 0.6)))));
    fileIcon(20, y + 8, 18, '#8a94a6');
    text('Packing list.txt', 46, y + 22, { size: 12.5 });
    text('just now', 250, y + 22, { size: 11.5, color: C.soft });
    check(ww - 32, y + 17, 10, mark(t, tOk + 1.2, 0.3), C.green, 2);
  });
  files.forEach(([n, color], i) => {
    const y = rowsY + (i + newIn) * 38;
    const isBudget = i === 0;
    const sel = isBudget && t > tSave - 0.1;
    if (sel) fillRR(10, y, ww - 20, 34, 6, rgba(C.blue, 0.18));
    fileIcon(20, y + 8, 18, color);
    text(n, 46, y + 22, { size: 12.5 });
    text(isBudget && t > tSave ? 'just now' : `2${i}.11.2026 09:1${i}`, 250, y + 22, { size: 11.5, color: C.soft });
    if (isBudget && t > tSave && t < tOk) {
      g.save();
      g.translate(ww - 32, y + 17);
      g.rotate(t * 5);
      g.strokeStyle = C.accent;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(0, 0, 6, 0, Math.PI * 1.4);
      g.stroke();
      g.restore();
      const p = easeInOut(mark(t, tSave + 0.15, tOk - tSave - 0.2));
      fillRR(46, y + 28, 180, 3, 2, C.line2);
      fillRR(46, y + 28, 180 * p, 3, 2, C.blue);
    } else check(ww - 32, y + 17, 10, isBudget && t > tOk ? (t - tOk) / 0.3 : 1, C.green, 2);
  });
  withAlpha(t > tSave && t < tOk + 0.3 ? 1 : 0, () =>
    text('Uploading · 4.2 MB/s', 18, CH - 14, { size: 11.5, color: C.accent }));
  // the iPhone, with the same folder
  const px = 500, py = 40, pw = 190, ph = 380;
  phoneFrame(px, py, pw, ph, (x, y, w) => {
    text('‹ iCloud Drive', x + 10, y + 34, { size: 10.5, color: C.accent });
    text('Projects', x + 10, y + 60, { size: 17, weight: 700, family: DISPLAY });
    const list = [['Packing list.txt', '#8a94a6', 'Just now'], ...files.map(([n, c]) => [n, c, 'Yesterday'])];
    const phoneNew = 1; // it was written on the phone
    list.forEach(([n, color, when], i) => {
      if (i === 0 && t < tOk + 0.6) return;
      const yy = y + 78 + (i - (t < tOk + 0.6 ? 1 : 0)) * 40 + (i === 0 ? (1 - easeOut(mark(t, tOk + 0.6, 0.4))) * -10 : 0);
      const flash = i === 1 ? Math.max(0, 1 - Math.abs(t - tOk - 0.15) / 0.5) : 0;
      if (flash > 0) fillRR(x + 4, yy - 4, w - 8, 36, 6, rgba(C.green, 0.25 * flash));
      fileIcon(x + 10, yy + 2, 20, color);
      text(n, x + 36, yy + 13, { size: 10.5, maxW: w - 46 });
      text(i === 1 && t > tOk ? 'Just now' : when, x + 36, yy + 26, { size: 9, color: C.muted });
    });
    void phoneNew;
  });
  // the link between them, through iCloud
  const up = t > tSave && t < tOk + 0.2, down = t > tOk + 0.5 && t < tOk + 1.3;
  const cx = 470, cy = 26;
  skyMark(cx, cy, 44, C.blue, { lw: 2.2, aw: 1.6, glow: up || down ? 1 : 0.3 });
  const dots = (from, to, phase) => {
    for (let i = 0; i < 5; i++) {
      const q = (t * 1.2 + i / 5) % 1;
      const x = lerp(from[0], to[0], q), y = lerp(from[1], to[1], q) - Math.sin(q * Math.PI) * 18;
      g.fillStyle = rgba(C.accent, Math.sin(q * Math.PI) * phase);
      g.beginPath();
      g.arc(x, y, 3, 0, Math.PI * 2);
      g.fill();
    }
  };
  if (up) {
    dots([ww - 30, rowsY + 17], [cx - 6, cy + 14], 1);
    dots([cx + 6, cy + 14], [px + 30, py + 120], 1);
  }
  if (down) {
    dots([px + 30, py + 120], [cx + 6, cy + 14], 1);
    dots([cx - 6, cy + 14], [ww - 30, rowsY + 17], 1);
  }
  // the cursor saves the budget (Ctrl+S)
  const [kx, ky] = path([[0, 300, 330], [tSave - 0.05, 120, rowsY + 20], [tSave + 1, 160, 300]], t);
  cursor(kx, ky, clickPress(t, tSave));
  withAlpha(clamp(1 - Math.abs(t - tSave - 0.25) / 0.5), () => {
    fillRR(kx + 18, ky + 22, 62, 24, 6, 'rgba(32,37,47,0.95)');
    text('Ctrl + S', kx + 49, ky + 38, { size: 11.5, weight: 650, align: 'center' });
  });
}

function notes(sc) {
  const t = sc.local;
  const tSearch = ev(sc, 0), tPick = ev(sc, 1), tTick = ev(sc, 2);
  windowFrame(0, 0, CW, CH, 'Notes — Skyfold');
  const query = 'garden';
  const typed = query.slice(0, Math.floor(clamp((t - tSearch - 0.15) / 0.6) * query.length + 0.0001));
  strokeRR(12, 42, CW - 24, 30, 7, t > tSearch ? C.accent : C.line2, 1.2);
  if (typed) {
    const w = text(typed, 24, 62, { size: 12.5 });
    if (Math.floor(t * 2) % 2 === 0) g.fillRect(25 + w, 50, 1.2, 15);
  } else text('Search notes (Ctrl+K)', 24, 62, { size: 12.5, color: C.muted });
  // folders
  strokeRR(12, 82, 150, CH - 96, 8, C.line);
  const folders = [['All notes', 8], ['Notes', 2], ['Home', 2], ['Recipes', 1], ['Travel', 1], ['Work', 1]];
  folders.forEach(([n, c], i) => {
    const y = 108 + i * 30;
    if (i === 0) fillRR(18, y - 18, 138, 26, 6, rgba(C.blue, 0.22));
    folderIcon(26 + (i === 3 ? 12 : 0), y - 12, 14, C.amber);
    text(n, 48 + (i === 3 ? 12 : 0), y, { size: 12 });
    text(String(c), 148, y, { size: 11.5, color: C.muted, align: 'right' });
  });
  // the list, filtered as you type
  strokeRR(170, 82, 190, CH - 96, 8, C.line);
  const list = [['Garden plan', 'Jobs for the garden in spring', '24.11.2026'], ['Shopping list', 'Milk, bread, coffee, apples.', '22.11.2026'],
    ['Trip to Mostar', 'Bridge at sunset, then dinner…', '18.11.2026'], ['Book ideas', 'A story told by a lighthouse…', '12.11.2026']];
  const filt = smooth(mark(t, tSearch + 0.6, 0.35));
  list.forEach(([n, s, d], i) => {
    const a = i === 0 ? 1 : 1 - filt;
    withAlpha(a, () => {
      const y = 92 + i * 62;
      const sel = i === 0 && t > tPick;
      if (sel) {
        fillRR(176, y, 178, 54, 7, '#243049');
        strokeRR(176.5, y + 0.5, 177, 53, 7, C.accent, 1.2);
      }
      text(n, 188, y + 20, { size: 12.5, weight: 700 });
      text(d, 344, y + 20, { size: 10, color: C.muted, align: 'right' });
      const sub = s;
      text(sub, 188, y + 40, { size: 11, color: C.soft, maxW: 156 });
      if (i === 0 && filt > 0) { // the match, marked
        font(11, 400, TEXT);
        const pre = g.measureText('Jobs for the ').width;
        const mw = g.measureText('garden').width;
        g.fillStyle = rgba(C.amber, 0.35 * filt);
        g.fillRect(188 + pre - 1, y + 29, mw + 2, 15);
      }
    });
  });
  // the note
  strokeRR(368, 82, CW - 380, CH - 96, 8, C.line);
  const open = smooth(mark(t, tPick + 0.05, 0.35));
  withAlpha(open, () => {
    const x = 388, y = 116 + (1 - open) * 8;
    text('Home · 24.11.2026. 07:15', x, y - 10, { size: 10.5, color: C.muted });
    text('Garden plan', x, y + 20, { size: 22, weight: 700, family: DISPLAY });
    font(12.5, 400, TEXT);
    const pre = 'Spring jobs for the back ';
    text(pre, x, y + 48, { size: 12.5 });
    const pw = g.measureText(pre).width;
    g.fillStyle = rgba(C.amber, 0.35);
    font(12.5, 400, TEXT);
    g.fillRect(x + pw - 1, y + 35, g.measureText('garden').width + 2, 18);
    text('garden', x + pw, y + 48, { size: 12.5 });
    text(', before April.', x + pw + g.measureText('garden').width, y + 48, { size: 12.5 });
    text('To do', x, y + 82, { size: 15, weight: 700 });
    const items = ['Prune the apple tree', 'Buy tomato seedlings', 'Fix the fence by the shed', 'Plant beans'];
    items.forEach((it, i) => {
      const yy = y + 108 + i * 26;
      strokeRR(x + 6, yy - 12, 14, 14, 3, C.accent, 1.3);
      if (i < 2) check(x + 13, yy - 5, 9, mark(t, tTick + i * 0.25, 0.3), C.accent, 2);
      text(it, x + 30, yy, { size: 12.5 });
    });
    text('Budget', x, y + 228, { size: 15, weight: 700 });
    const rows = [['Item', 'Price'], ['Seedlings', '12 €'], ['Fence boards', '48 €']];
    rows.forEach((r, i) => {
      const yy = y + 240 + i * 24;
      strokeRR(x, yy, 110, 24, 0, C.line2);
      strokeRR(x + 110, yy, 60, 24, 0, C.line2);
      text(r[0], x + 8, yy + 16, { size: 11.5, weight: i ? 400 : 700 });
      text(r[1], x + 118, yy + 16, { size: 11.5, weight: i ? 400 : 700 });
    });
  });
  const [cx, cy] = path([[0, 300, 300], [tSearch - 0.05, 160, 58], [tPick - 0.05, 250, 118], [tPick + 1.2, 470, 380]], t);
  cursor(cx, cy, clickPress(t, tSearch) + clickPress(t, tPick));
  ripple(160, 58, (t - tSearch) / 0.45);
  ripple(250, 118, (t - tPick) / 0.45);
}

function safe(sc) {
  const t = sc.local;
  const t1 = ev(sc, 0), t2 = ev(sc, 1), tOk = ev(sc, 2);
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.5)';
  g.shadowBlur = 40;
  g.shadowOffsetY = 18;
  fillRR(0, 0, CW, CH, 14, C.card);
  g.restore();
  strokeRR(0.5, 0.5, CW - 1, CH - 1, 14, C.line2);
  const lane = (y, from, to, label, file, at, fromIcon, toIcon) => {
    fillRR(24, y, CW - 48, 112, 12, C.card2);
    fromIcon(80, y + 50);
    toIcon(CW - 110, y + 50);
    g.strokeStyle = C.line2;
    g.lineWidth = 2;
    g.setLineDash([4, 6]);
    g.beginPath();
    g.moveTo(140, y + 50);
    g.lineTo(CW - 170, y + 50);
    g.stroke();
    g.setLineDash([]);
    text(from, 80, y + 98, { size: 11.5, color: C.muted, align: 'center' });
    text(to, CW - 110, y + 98, { size: 11.5, color: C.ink, align: 'center', weight: 650 });
    const p = easeInOut(mark(t, at - 0.35, 0.7));
    const fx = lerp(150, CW - 230, p), fy = y + 50 - Math.sin(p * Math.PI) * 16;
    withAlpha(p > 0 && p < 1 ? 1 : 0, () => {
      fillRR(fx, fy - 13, 120, 26, 13, '#2a3242');
      text(file, fx + 60, fy + 4.5, { size: 11, align: 'center' });
    });
    withAlpha(smooth(mark(t, at + 0.3, 0.4)), () => {
      text(label, CW / 2 - 20, y + 30, { size: 12, color: C.green, align: 'center', weight: 650 });
    });
  };
  const pcIcon = (x, y) => {
    strokeRR(x - 26, y - 22, 52, 36, 5, C.soft, 2);
    g.fillStyle = C.soft;
    g.fillRect(x - 8, y + 16, 16, 3);
  };
  const phoneIcon = (x, y) => strokeRR(x - 14, y - 26, 28, 50, 6, C.soft, 2);
  const binIcon = (x, y) => {
    g.strokeStyle = C.accent;
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(x - 18, y - 16);
    g.lineTo(x + 18, y - 16);
    g.moveTo(x - 14, y - 16);
    g.lineTo(x - 11, y + 22);
    g.lineTo(x + 11, y + 22);
    g.lineTo(x + 14, y - 16);
    g.moveTo(x - 6, y - 21);
    g.lineTo(x + 6, y - 21);
    g.stroke();
  };
  const cloudBin = (x, y) => skyMark(x, y - 4, 64, C.accent, { lw: 2.4, aw: 1.8 });
  lane(22, 'Deleted on this PC', 'Recently Deleted in iCloud', '30 days to change your mind', 'IMG_2051.HEIC', t1,
    pcIcon, cloudBin);
  lane(150, 'Deleted on the iPhone', 'Recycle Bin on this PC', 'Restore it any time', 'Letter.docx', t2, phoneIcon, binIcon);
  // a conflict keeps both
  fillRR(24, 278, CW - 48, 160, 12, C.card2);
  text('Changed on both sides?', 48, 308, { size: 14, weight: 700 });
  const a = spring(mark(t, tOk - 0.3, 0.6));
  [['Budget 2026.xlsx', 'iCloud\'s version'], ['Budget 2026 (PC conflict …).xlsx', 'this PC\'s version']].forEach(([n, who], i) => {
    const p = i ? a : 1;
    withAlpha(clamp(p * 2), () => {
      const y = 330 + i * 46 + (1 - p) * -20;
      fillRR(48, y, 380, 36, 8, '#252c3a');
      fileIcon(60, y + 8, 20, '#21a366');
      text(n, 88, y + 23, { size: 12.5 });
      text(who, 418, y + 23, { size: 11, color: C.muted, align: 'right' });
    });
  });
  withAlpha(smooth(mark(t, tOk, 0.3)), () => {
    check(470, 368, 16, mark(t, tOk, 0.35), C.green, 3);
    text('Both copies kept', 490, 374, { size: 14, color: C.green, weight: 700 });
  });
}

function end(sc) {
  const t = sc.local;
  const tall = format === 'tall';
  const cx = L.W / 2, cy = tall ? 470 : 230;
  const p = spring(mark(t, 0.1, 0.8));
  g.save();
  g.translate(cx, cy);
  g.scale(lerp(0.6, 1, p), lerp(0.6, 1, p));
  withAlpha(clamp(p * 2), () => {
    g.shadowColor = rgba(C.blue, 0.5);
    g.shadowBlur = 50;
    logoTile(0, 0, tall ? 190 : 150);
  });
  g.restore();
  const n = easeOut(mark(t, 0.35, 0.6));
  withAlpha(n, () => text('Skyfold', cx, cy + (tall ? 190 : 150) + (1 - n) * 14, { size: tall ? 84 : 72, weight: 700,
    family: DISPLAY, align: 'center' }));
  const s = smooth(mark(t, 0.65, 0.6));
  withAlpha(s, () => {
    const lines = wrap('iCloud Photos, Drive and Notes on Windows, in real folders.', tall ? 600 : 900, tall ? 26 : 24,
      500, DISPLAY);
    lines.forEach((l, i) => text(l, cx, cy + (tall ? 250 : 200) + i * 36, { size: tall ? 26 : 24, color: C.soft,
      align: 'center', family: DISPLAY, weight: 500 }));
  });
  const chips = ['Free and open source', 'Windows 10 & 11', 'No iCloud for Windows needed'];
  const cy2 = cy + (tall ? 370 : 262);
  font(15, 600, TEXT);
  const widths = chips.map((c) => g.measureText(c).width + 36);
  const total = widths.reduce((a, b) => a + b, 0) + 14 * (chips.length - 1);
  let x = cx - total / 2;
  chips.forEach((c, i) => {
    const a = spring(mark(t, 1.0 + i * 0.15, 0.6));
    withAlpha(clamp(a * 2), () => {
      const w = widths[i];
      const bx = tall ? cx - w / 2 : x, by = tall ? cy2 + i * 52 : cy2;
      g.save();
      g.translate(bx + w / 2, by + 17);
      g.scale(lerp(0.8, 1, a), lerp(0.8, 1, a));
      fillRR(-w / 2, -17, w, 34, 17, C.card2);
      strokeRR(-w / 2 + 0.5, -16.5, w - 1, 33, 17, C.line2);
      text(c, 0, 5, { size: 15, weight: 600, align: 'center' });
      g.restore();
    });
    x += widths[i] + 14;
  });
  const u = smooth(mark(t, 1.6, 0.6));
  withAlpha(u, () => {
    const uy = tall ? cy2 + 3 * 52 + 70 : cy2 + 100;
    text('zoranmod.github.io/skyfold', cx, uy, { size: tall ? 30 : 28, weight: 650, color: C.accent, align: 'center',
      family: MONO });
    text('Not affiliated with Apple.', cx, uy + (tall ? 44 : 38), { size: 13, color: C.muted, align: 'center' });
  });
}

const RENDER = { tray, photos, gallery, drive, notes, safe };

// --- the frame -----------------------------------------------------------------------------------
export function draw(t) {
  const frame = Math.round(t * FPS);
  const sc = atFrame(cut, Math.min(frame, cut.frames - 1));
  g.setTransform(cut.width / L.W, 0, 0, cut.height / L.H, 0, 0);
  backdrop(t, sc);
  if (sc.id === 'hook') {
    hook(sc, t);
    rail(sc, smooth(mark(sc.progress, 0.85, 0.15)));
  } else if (sc.id === 'end') {
    rail(sc, 1 - smooth(mark(sc.local, 0, 0.5)));
    end(sc);
  } else {
    titleBlock(sc);
    stage(sc, RENDER[sc.id]);
    rail(sc, 1);
  }
  // fade from and to black
  const fin = 1 - smooth(t / 0.5), fout = smooth((t - (cut.frames / FPS - 0.6)) / 0.6);
  const k = Math.max(fin, fout);
  if (k > 0) {
    g.fillStyle = `rgba(0,0,0,${k})`;
    g.fillRect(0, 0, L.W, L.H);
  }
}

export function duration() {
  return cut.frames / FPS;
}

window.draw = draw;
window.setFormat = setFormat;
window.duration = duration;
// the fonts are Windows' own (Segoe UI Variable): check they are really there, not a fallback
window.fontsOk = () => {
  const probe = (fam) => {
    g.font = `700 40px ${fam}, monospace`;
    const a = g.measureText('Skyfold Wgy 1,284').width;
    g.font = '700 40px monospace';
    return Math.abs(a - g.measureText('Skyfold Wgy 1,284').width) > 1;
  };
  return { display: probe('"Segoe UI Variable Display"'), text: probe('"Segoe UI Variable Text"'), mono: probe('"Cascadia Mono"') };
};
Promise.all([document.fonts.ready, icon.decode().catch(() => {})]).then(() => {
  window.promoReady = true;
  window.dispatchEvent(new Event('promo-ready'));
});
