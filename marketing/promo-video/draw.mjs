// The drawing of the Skyfold promo video. draw(t) paints the frame at time t (seconds) and keeps no
// state between frames: the same t always gives the same picture, so seeking, previews and
// re-renders are exact. Everything shown is made up: no real photos, files or notes.
//
// The app's own surfaces (the tray panel, its notification card, the Photos and Notes windows) follow
// the 1.1 design in mockups/premium-combined.html: they are drawn in the design's own pixels and
// scaled into the card, so its sizes (14 px text, 32 px tiles, 34 px buttons, corners 6 / 8 / 12)
// can be copied as they are.

import {
  FPS, BEAT, EVENTS, OUTRO, PHASES, atFrame, clamp, cuts, easeInOut, easeOut, lerp, mark, rng, scenes, smooth,
  spring,
} from './timeline.mjs';

const params = new URLSearchParams(location.search);
export let format = params.get('format') === 'tall' ? 'tall' : 'wide';
let cut = cuts[format];

const canvas = document.querySelector('canvas');
const g = canvas.getContext('2d', { alpha: false });

// --- brand and the 1.1 design's dark tokens ---------------------------------------------------------
const C = {
  // the video's own backdrop, title column and rail
  bg: '#0d1016', bg2: '#131823', card: '#171c25', card2: '#1c2230', line: '#262e3d', line2: '#323c4f',
  ink: '#f3f5f8', muted: '#9ba7b9', soft: '#c3ccd9', blue: '#3b82f6', blue2: '#4f8dff', black: '#07090d',
  // the app (mockups/premium-combined.html, [data-theme="dark"])
  t2: 'rgba(243,245,248,0.74)', t3: 'rgba(243,245,248,0.52)',
  accent: '#6fa8ff', accentStrong: '#4a90ff', onAccent: '#06142b', accentSoft: 'rgba(111,168,255,0.16)',
  green: '#6fd39b', amber: '#f3c969', pink: '#f6809e', drive: '#7dacff', err: '#ff7a85',
  mica: 'rgba(30,33,39,0.80)', micaSolid: '#1e2127', layer: 'rgba(255,255,255,0.045)', layer2: 'rgba(255,255,255,0.08)',
  hover: 'rgba(255,255,255,0.09)', stroke: 'rgba(255,255,255,0.07)', stroke2: 'rgba(255,255,255,0.12)',
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
const CARD_SIZE = { tray: [700, 530] }; // the desktop is a little taller, for the panel
const K = 0.75; // app windows: the design's pixels per card unit (a 933 x 613 px window)
const KT = 0.76; // the tray panel and its card

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
function measure(s, size, weight = 400, family = TEXT) {
  font(size, weight, family);
  return g.measureText(s).width;
}
function text(s, x, y, { size = 14, weight = 400, color = C.ink, family = TEXT, align = 'left', base = 'alphabetic',
  maxW, strike = 0 } = {}) {
  font(size, weight, family);
  g.fillStyle = color;
  g.textAlign = align;
  g.textBaseline = base;
  if (maxW && g.measureText(s).width > maxW) {
    while (s.length > 1 && g.measureText(s + '…').width > maxW) s = s.slice(0, -1);
    s += '…';
  }
  g.fillText(s, x, y);
  const w = g.measureText(s).width;
  if (strike > 0) {
    const x0 = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
    g.fillRect(x0, y - size * 0.3, w * clamp(strike), Math.max(1, size * 0.075));
  }
  return w;
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
// a word marked by search (the design's <mark>: the warning colour at 35 %)
function markBg(x, base, w, size) {
  fillRR(x - 1, base - size * 0.92, w + 2, size * 1.25, 2, 'rgba(243,201,105,0.35)');
}

// --- the design's line icons (skyfold/ui/svgicons.py: a 20 x 20 grid, 1.6 px round strokes) --------
const circ = (cx, cy, r) => `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
const rect = (x, y, w, h, r) => `M${x + r} ${y}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}v${h - 2 * r}a${r} ${r} 0 0 1 ${-r} ${r}` +
  `h${-(w - 2 * r)}a${r} ${r} 0 0 1 ${-r} ${-r}v${-(h - 2 * r)}a${r} ${r} 0 0 1 ${r} ${-r}z`;
const ICON_D = {
  photo: rect(2.5, 3.5, 15, 13, 2.5) + circ(7.2, 8, 1.6) + 'M17 13.5l-4-4-7.5 7',
  folder: 'M2.5 6.2a2 2 0 0 1 2-2h3.3l1.8 1.8h5.9a2 2 0 0 1 2 2v6.8a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2z',
  note: rect(4, 2.5, 12, 15, 2) + 'M7 7h6M7 10h6M7 13h4',
  // the app's cog with eight teeth (not the mockup's rays)
  gear: circ(10, 10, 2.6) + 'M8.17 4.08 L8.63 2.02 L11.37 2.02 L11.83 4.08 L12.89 4.52 L14.68 3.39 L16.61 5.32 L15.48 7.11 ' +
    'L15.92 8.17 L17.98 8.63 L17.98 11.37 L15.92 11.83 L15.48 12.89 L16.61 14.68 L14.68 16.61 L12.89 15.48 L11.83 15.92 ' +
    'L11.37 17.98 L8.63 17.98 L8.17 15.92 L7.11 15.48 L5.32 16.61 L3.39 14.68 L4.52 12.89 L4.08 11.83 L2.02 11.37 ' +
    'L2.02 8.63 L4.08 8.17 L4.52 7.11 L3.39 5.32 L5.32 3.39 L7.11 4.52Z',
  sync: 'M16 9a6 6 0 0 0-10.6-3.4M4 4v3h3M4 11a6 6 0 0 0 10.6 3.4M16 16v-3h-3',
  check: 'M4.5 10.5l3.5 3.5 7.5-8',
  chev: 'M8 5l5 5-5 5',
  back: 'M12 5l-5 5 5 5',
  chevUp: 'M5 12.5l5-5 5 5',
  pin: 'M10 17.5s-5.5-4.8-5.5-9a5.5 5.5 0 0 1 11 0c0 4.2-5.5 9-5.5 9z' + circ(10, 8.5, 2),
  more: circ(5, 10, 0.9) + circ(10, 10, 0.9) + circ(15, 10, 0.9),
  stop: rect(5.5, 5.5, 9, 9, 1.5),
  up: 'M10 15V5M6 9l4-4 4 4',
  arrowL: 'M15 10H5M9 6l-4 4 4 4',
  arrowR: 'M5 10h10M11 6l4 4-4 4',
  dl: 'M10 4v9M6 9.5l4 4 4-4M4.5 16h11',
  info: circ(10, 10, 7.5) + 'M10 9v5M10 6.3v.2',
  clock: circ(10, 10, 7.5) + 'M10 6v4.3l2.8 1.7',
  heart: 'M10 16.5s-6.5-3.9-6.5-8.6A3.6 3.6 0 0 1 10 5.8a3.6 3.6 0 0 1 6.5 2.1c0 4.7-6.5 8.6-6.5 8.6z',
  trash: 'M4 6h12M8 6V4h4v2M5.5 6l.8 10h7.4l.8-10',
  search: circ(9, 9, 5) + 'M13 13l4 4',
  device: rect(6, 2.5, 8, 15, 2) + 'M9 14.8h2',
  monitor: rect(2.5, 3.5, 15, 10.5, 1.5) + 'M7 17h6M10 14v3',
  file: 'M5.5 2.5h6l3 3v11a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1z M11.5 2.5v3h3',
};
const ICON = {};
for (const [k, d] of Object.entries(ICON_D)) ICON[k] = new Path2D(d);
// an icon with its top left at (x, y), size wide; dash: 0..1 draws the stroke on
function ico(name, x, y, size, color, { lw = 1.6, fill = null, dash = 1 } = {}) {
  g.save();
  g.translate(x, y);
  g.scale(size / 20, size / 20);
  g.lineCap = 'round';
  g.lineJoin = 'round';
  g.lineWidth = lw;
  g.strokeStyle = color;
  if (name === 'more') {
    g.fillStyle = color;
    g.fill(ICON[name]);
  } else {
    if (fill) {
      g.fillStyle = fill;
      g.fill(ICON[name]);
    }
    if (dash < 1) g.setLineDash([16 * clamp(dash), 40]);
    if (dash > 0) g.stroke(ICON[name]);
  }
  g.restore();
}
function spinIcon(cx, cy, size, color, angle, lw = 1.6) {
  g.save();
  g.translate(cx, cy);
  g.rotate(angle);
  ico('sync', -size / 2, -size / 2, size, color, { lw });
  g.restore();
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
function logoTile(cx, cy, s, r = 0) {
  if (!(icon.complete && icon.naturalWidth)) return;
  g.save();
  if (r) {
    rr(cx - s / 2, cy - s / 2, s, s, r);
    g.clip();
  }
  g.drawImage(icon, cx - s / 2, cy - s / 2, s, s);
  g.restore();
}

// --- the painted wallpaper "Glasswater Inlet" and its blurred copy (the acrylic behind the glass) -----
// The same scene as the mockup's wallpaper (an original painting in code), in its 1380 x 550 units.
const WALL = { sky: '#719793', haze: '#f3c893', pale: '#efefdf', coast: '#416b6b', water: '#658b84', fore: '#22464b' };
function lcg(seed) {
  return () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
}
let wallCache = null;
function walls() {
  if (wallCache) return wallCache;
  const S = 2;
  const sharp = document.createElement('canvas');
  sharp.width = 1380 * S;
  sharp.height = 550 * S;
  const c = sharp.getContext('2d');
  c.scale(S, S);
  const rand = lcg(714);
  const sky = c.createLinearGradient(0, 0, 193, 550);
  sky.addColorStop(0, WALL.sky);
  sky.addColorStop(0.69, WALL.haze);
  sky.addColorStop(1, WALL.pale);
  c.fillStyle = sky;
  c.fillRect(0, 0, 1380, 550);
  c.globalAlpha = 0.88;
  c.fillStyle = WALL.pale;
  c.beginPath();
  c.arc(215, 125, 37, 0, Math.PI * 2);
  c.fill();
  c.fillStyle = WALL.coast;
  [[0.24, 'M0 184L48 151 92 160 159 119 217 170 272 145 325 169 385 139 451 195 522 142 605 184 680 163 790 203 900 117 1050 194 1160 158 1380 201V315H0Z'],
    [0.43, 'M0 215L36 230 83 198 120 188 176 217 258 168 282 173 314 150 366 182 409 169 467 204 517 182 610 228 720 190 810 235 980 172 1100 218 1380 171V315H0Z'],
    [0.77, 'M0 245L68 213 132 229 180 223 254 261 330 242 397 221 448 226 510 200 550 215 609 205 690 238 734 223 840 271 948 228 1090 267 1220 199 1380 233V309H0Z'],
  ].forEach(([a, d]) => {
    c.globalAlpha = a;
    c.fill(new Path2D(d));
  });
  c.globalAlpha = 0.8;
  c.fillStyle = WALL.fore;
  c.fill(new Path2D('M0 252Q60 235 125 259T240 273Q280 278 335 269T455 271Q520 281 640 280T812 285L990 296H0Z'));
  c.globalAlpha = 1;
  const lake = c.createLinearGradient(0, 296, 207, 551);
  lake.addColorStop(0, WALL.pale);
  lake.addColorStop(0.35, WALL.water);
  lake.addColorStop(1, WALL.coast);
  c.fillStyle = lake;
  c.fillRect(0, 296, 1380, 255);
  c.strokeStyle = WALL.pale;
  c.lineCap = 'round';
  c.lineWidth = 1;
  for (let i = 0; i < 225; i++) {
    const y = 302 + rand() * 246, x = rand() * 1450 - 30, len = 2 + rand() * 46;
    c.globalAlpha = 0.06 + rand() * 0.22;
    c.beginPath();
    c.moveTo(Math.round(x), Math.round(y));
    c.lineTo(Math.round(x) + Math.round(len), Math.round(y));
    c.stroke();
  }
  c.globalAlpha = 1;
  c.fillStyle = WALL.fore;
  const edge = (x) => 369 + Math.sin(x / 66) * 10 + x * 0.2;
  for (let i = 0; i < 113; i++) {
    const x = -28 + i * 4.4, h = (17 + rand() * 40) * (1 - x / 600), y = edge(x);
    c.beginPath();
    c.moveTo(x, y - h);
    for (const side of [1, -1]) {
      const pts = [[0.32, 0.12], [0.29, 0.045], [0.56, 0.19], [0.49, 0.09], [0.8, 0.25], [0.7, 0.13], [1, 0.3]];
      for (const [yy, xx] of (side === 1 ? pts : pts.slice().reverse())) c.lineTo(x + xx * h * side, y - h + h * yy);
    }
    c.closePath();
    c.fill();
  }
  c.beginPath();
  for (let i = 0; i < 58; i++) c[i ? 'lineTo' : 'moveTo'](i * 8, edge(i * 8) - 2);
  [[510, 480], [420, 481], [365, 477], [291, 497], [216, 496], [143, 520], [0, 542]].forEach(([x, y]) => c.lineTo(x, y));
  c.closePath();
  c.fill();
  // acrylic: blur(40px) saturate(160%) of what is behind, at half size (it is soft anyway)
  const blur = document.createElement('canvas');
  blur.width = 690;
  blur.height = 275;
  const b = blur.getContext('2d');
  b.fillStyle = '#6d8f8a';
  b.fillRect(0, 0, 690, 275);
  b.filter = 'blur(20px) saturate(160%)';
  b.drawImage(sharp, -40, -40, 770, 355);
  wallCache = { sharp, blur };
  return wallCache;
}
// draws the crop [sx, sy, sw, sh] (wallpaper units) of the wallpaper or its blurred copy into a rect
function wallTo(kind, crop, x, y, w, h) {
  const img = walls()[kind];
  const f = img.width / 1380;
  g.drawImage(img, crop[0] * f, crop[1] * f, crop[2] * f, crop[3] * f, x, y, w, h);
}
// a floating surface's shadow (the design's shadow-3), under glass that covers it
function floatShadow(x, y, w, h, r, blur = 48, dy = 18, a = 0.5) {
  g.save();
  g.shadowColor = `rgba(0,0,0,${a})`;
  g.shadowBlur = blur;
  g.shadowOffsetY = dy;
  fillRR(x, y, w, h, r, C.micaSolid);
  g.restore();
}
// Windows 11 acrylic: the wallpaper behind, blurred, under the design's mica tint
function glass(x, y, w, h, r, crop, area, tint = C.mica) {
  g.save();
  rr(x, y, w, h, r);
  g.clip();
  wallTo('blur', crop, ...area);
  g.fillStyle = tint;
  g.fillRect(x, y, w, h);
  g.restore();
}

// --- made-up photos: small paintings (the mockup's painting(), the same for the same index) --------
const KINDS = ['inlet', 'dusk', 'coast', 'snow', 'orchard', 'city'];
const PAL = { inlet: ['#8cbcb9', '#f3d69c', '#668e85', '#214f59', '#426d68'], dusk: ['#7c7d9e', '#f6a47b', '#a37874', '#4c5067', '#70747e'],
  coast: ['#88b9c6', '#e0d5a9', '#a7a37f', '#3e6960', '#4c9c98'], snow: ['#829db5', '#ebe8db', '#8196a4', '#4b6273', '#c6d1d2'],
  orchard: ['#8dada1', '#f6d0a0', '#af975b', '#465b42', '#829258'], city: ['#404a6a', '#c38679', '#796d79', '#344055', '#485569'] };
const PS = 1.5; // paintings are 400 x 280, kept at 1.5x for the big viewer
const paintCache = new Map();
export function painting(index) {
  if (paintCache.has(index)) return paintCache.get(index);
  const cv = document.createElement('canvas');
  cv.width = 400 * PS;
  cv.height = 280 * PS;
  const c = cv.getContext('2d');
  c.scale(PS, PS);
  const rand = lcg(index * 723 + 55), kind = KINDS[index % 6];
  const [sky, haze, back, fore, water] = PAL[kind], shift = Math.floor(rand() * 44);
  const box = (x, y, w, h, col) => { c.fillStyle = col; c.fillRect(x, y, w, h); };
  const poly = (pts, col) => {
    c.fillStyle = col;
    c.beginPath();
    pts.forEach(([x, y], j) => (j ? c.lineTo(x, y) : c.moveTo(x, y)));
    c.closePath();
    c.fill();
  };
  const ridge = (y, amp, col) => {
    const pts = [[-16, 280]];
    for (let x = -16; x <= 422; x += 16) pts.push([x, y + Math.sin(x / 48 + shift) * amp + Math.sin(x / 20 + shift) * amp * 0.24 + rand() * 7]);
    pts.push([422, 280]);
    poly(pts, col);
  };
  const gr = c.createLinearGradient(0, 0, 0, 150);
  gr.addColorStop(0, sky);
  gr.addColorStop(1, haze);
  box(0, 0, 400, 280, gr);
  c.fillStyle = '#f8dfaa';
  c.beginPath();
  c.arc(78 + shift * 3, 58 + shift / 2, kind === 'city' ? 10 : 20, 0, 7);
  c.fill();
  ridge(126, 24, back);
  ridge(153, 20, fore);
  if (kind === 'snow') {
    for (let x = -70; x < 435; x += 65) {
      const h = 64 + rand() * 58;
      poly([[x, 185], [x + 50, h], [x + 116, 187]], back);
      poly([[x + 25, (185 + h) / 2], [x + 50, h], [x + 71, h + 30], [x + 52, h + 20], [x + 42, h + 27], [x + 39, h + 18]], '#f1efe5');
    }
    ridge(207, 10, water);
  } else if (kind === 'orchard') {
    box(0, 166, 400, 114, water);
    poly([[174, 280], [239, 165], [247, 166], [255, 280]], haze);
    for (let j = 0; j < 65; j++) {
      const x = rand() * 400, y = 161 + rand() * 110;
      if (x > 162 && x < 265) continue;
      const s = (y - 130) * 0.21;
      box(x, y - s, 3, s, fore);
      for (let t = 0; t < 7; t++) {
        c.fillStyle = t % 3 === 0 ? '#c48855' : back;
        c.beginPath();
        c.arc(x + (rand() - 0.5) * s * 1.7, y - s - rand() * s, s * 0.48, 0, 7);
        c.fill();
      }
    }
  } else {
    box(0, 171, 400, 110, water);
    for (let i = 0; i < 330; i++) {
      c.globalAlpha = 0.1 + rand() * 0.35;
      box(rand() * 400, 173 + rand() * 108, 2 + rand() * 22, 1, haze);
    }
    c.globalAlpha = 1;
    if (kind === 'city') {
      for (let x = -5; x < 407;) {
        const w = 9 + rand() * 22, h = 18 + rand() * 68;
        box(x, 170 - h, w, h, fore);
        for (let y = 177 - h; y < 165; y += 8) for (let xx = x + 3; xx < x + w - 2; xx += 6) if (rand() > 0.55) box(xx, y, 2, 3, haze);
        x += w + 2;
      }
    } else if (kind === 'coast') {
      poly([[0, 111], [68, 127], [135, 171], [160, 192], [103, 193], [61, 213], [0, 225]], fore);
      poly([[0, 117], [65, 135], [140, 176], [124, 173], [70, 152], [0, 134]], back);
    } else {
      poly([[0, 174], [55, 158], [88, 169], [124, 174], [90, 178], [0, 183]], fore);
      for (let i = 0; i < 35; i++) {
        const x = rand() * 90, h = rand() * 14 + 2;
        poly([[x, 170], [x + 3, 169 - h], [x + 6, 170]], fore);
      }
    }
  }
  for (let i = 0; i < 2600; i++) {
    c.globalAlpha = 0.025 + rand() * 0.07;
    box(rand() * 400, rand() * 280, 1 + rand() * 2, 1, rand() > 0.5 ? '#fff' : '#13292c');
  }
  c.globalAlpha = 1;
  paintCache.set(index, cv);
  return cv;
}
// draw a photo covering the rect (cut to fit); 48 paintings, like the mockup's library
function photoIn(seed, x, y, w, h, r = 0) {
  const img = painting(((seed % 48) + 48) % 48);
  const iw = img.width, ih = img.height, a = w / h;
  let sx = 0, sy = 0, sw = iw, sh = ih;
  if (a > iw / ih) { sh = iw / a; sy = (ih - sh) / 2; } else { sw = ih * a; sx = (iw - sw) / 2; }
  g.save();
  if (r) {
    rr(x, y, w, h, r);
    g.clip();
  }
  g.drawImage(img, sx, sy, sw, sh, x, y, w, h);
  g.restore();
}
const libraryPhoto = (i) => (i * 5 + 1) % 48; // the Photos window's 48 pictures, in the mockup's order

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
// the design's status badge: a soft disc with the check drawing itself
function okBadge(cx, cy, r, p, color = C.green) {
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fillStyle = rgba(color, 0.16);
  g.fill();
  const s = r;
  ico('check', cx - s / 2, cy - s / 2, s, color, { lw: 2, dash: p });
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
// buttons (px): the plain one on a layer, the main one in the accent with a little light at the top
function btn(x, y, w, h, label, iconName, { primary = false, size = 13, iconSize = 18, color } = {}) {
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.3)';
  g.shadowBlur = 2;
  g.shadowOffsetY = 1;
  if (primary) {
    const fillG = g.createLinearGradient(0, y, 0, y + h);
    fillG.addColorStop(0, '#5d9cff');
    fillG.addColorStop(1, C.accentStrong);
    fillRR(x, y, w, h, 8, fillG);
  } else fillRR(x, y, w, h, 8, C.layer2);
  g.restore();
  if (primary) {
    const edge = g.createLinearGradient(0, y, 0, y + h);
    edge.addColorStop(0, '#8cbcff');
    edge.addColorStop(0.2, C.accentStrong);
    edge.addColorStop(0.85, C.accentStrong);
    edge.addColorStop(1, '#3b74cf');
    strokeRR(x + 0.5, y + 0.5, w - 1, h - 1, 7.5, edge);
  } else strokeRR(x + 0.5, y + 0.5, w - 1, h - 1, 7.5, C.stroke2);
  const ink = color ?? (primary ? C.onAccent : C.ink);
  const tw = label ? measure(label, size, 600) : 0;
  const total = (iconName ? iconSize : 0) + (iconName && label ? 8 : 0) + tw;
  let cx = x + (w - total) / 2;
  if (iconName) {
    ico(iconName, cx, y + (h - iconSize) / 2, iconSize, ink);
    cx += iconSize + 8;
  }
  if (label) text(label, cx, y + h / 2 + size * 0.36, { size, weight: 600, color: ink });
}
// an app window in the design's pixels: glass, a hairline edge, the title bar with its caption buttons
const WIN_CROP = [210, 0, 837, 550];
function appWindow(w, h, title, drawIcon) {
  floatShadow(0, 0, w, h, 12, 56, 22, 0.55);
  glass(0, 0, w, h, 12, WIN_CROP, [0, 0, w, h]);
  strokeRR(0.5, 0.5, w - 1, h - 1, 12, C.stroke2);
  drawIcon(14, 12, 16);
  text(title, 40, 24.5, { size: 12, color: C.t2 });
  g.strokeStyle = C.t2;
  g.lineWidth = 1;
  const cx = w - 138;
  g.beginPath();
  g.moveTo(cx + 18, 20.5);
  g.lineTo(cx + 28, 20.5);
  g.rect(cx + 64.5, 15.5, 9, 9);
  g.moveTo(cx + 110, 15);
  g.lineTo(cx + 120, 25);
  g.moveTo(cx + 120, 15);
  g.lineTo(cx + 110, 25);
  g.stroke();
}
const skyfoldIcon = (x, y, s) => logoTile(x + s / 2, y + s / 2, s, 4);
// the shared content layer of the app windows: radius 8 8 12 8
function pane(x, y, w, h) {
  rr(x, y, w, h, [8, 8, 12, 8]);
  g.fillStyle = C.layer;
  g.fill();
  rr(x + 0.5, y + 0.5, w - 1, h - 1, [8, 8, 12, 8]);
  g.strokeStyle = C.stroke;
  g.lineWidth = 1;
  g.stroke();
}
// a sidebar row (Photos and Notes): 34 px, the accent marker when it is the selected one
function sideRow(x, y, w, label, iconName, iconColor, count, on) {
  if (on) {
    fillRR(x, y, w, 34, 6, C.layer2);
    fillRR(x, y + 9, 3, 16, 1.5, C.accent);
  }
  ico(iconName, x + 10, y + 8, 18, iconColor);
  text(label, x + 38, y + 22, { size: 14, weight: on ? 600 : 400 });
  if (count !== undefined) text(String(count), x + w - 10, y + 21.5, { size: 12, family: MONO, color: C.t3, align: 'right' });
}
function sideLabel(x, y, s) {
  text(s, x + 10, y, { size: 12, weight: 600, color: C.t3 });
}
// a search box (px), with the focus line when typing
function searchBox(x, y, w, h, s, { placeholder = 'Search', focus = 0, caret = false, bg = C.layer2 } = {}) {
  fillRR(x, y, w, h, 6, bg);
  strokeRR(x + 0.5, y + 0.5, w - 1, h - 1, 6, C.stroke2);
  if (focus > 0) withAlpha(focus, () => {
    g.save();
    rr(x, y, w, h, 6);
    g.clip();
    g.fillStyle = C.accent;
    g.fillRect(x, y + h - 2, w, 2);
    g.restore();
  });
  ico('search', x + 10, y + (h - 18) / 2, 18, C.t3);
  if (s) {
    const tw = text(s, x + 36, y + h / 2 + 5, { size: 13.5 });
    if (caret) {
      g.fillStyle = C.ink;
      g.fillRect(x + 37 + tw, y + h / 2 - 8, 1.2, 16);
    }
  } else text(placeholder, x + 36, y + h / 2 + 5, { size: 13.5, color: C.t3 });
}
// File Explorer's folders (filled, as Windows draws them)
function folderIcon(x, y, s, color = C.amber) {
  g.save();
  fillRR(x, y + s * 0.1, s * 0.48, s * 0.24, s * 0.06, mix(color, '#000000', 0.22));
  fillRR(x, y + s * 0.2, s, s * 0.7, s * 0.08, mix(color, '#000000', 0.22));
  fillRR(x, y + s * 0.3, s, s * 0.6, s * 0.08, color);
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
  fillRR(x + m, y + m, w - 2 * m, h - 2 * m, 0, '#111418');
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
  const [w, h] = CARD_SIZE[sc.id] ?? [CW, CH];
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
  g.translate(-w / 2, -h / 2);
  withAlpha(clamp(enter * 3) * (1 - smooth((leave - 0.75) / 0.25)), () => render(sc, w, h));
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
      for (let i = 0; i < 12; i++) photoIn(libraryPhoto(i), x + 8 + (i % 3) * ((w - 16) / 3), y + 46 + Math.floor(i / 3) * 44,
        (w - 16) / 3 - 3, 41, 3);
      fillRR(x + 8, y + 232, w - 16, 34, 8, '#1d2128');
      text('Notes · Garden plan', x + 16, y + 253, { size: 10.5, color: C.t2 });
    });
    g.restore();
    const [mx, my, ms] = P.pc;
    g.save();
    g.translate(mx + (1 - devIn) * 40, my);
    g.scale(ms, ms);
    monitorFrame(-190, -125, 380, 240, (x, y, w, h) => {
      // the desktop's painted wallpaper (the same one as the tray scene)
      wallTo('sharp', [0, 0, 550 * (w / h), 550], x, y, w, h);
      // folders arrive once the cloud links them
      const names = ['iCloud Photos', 'iCloud Drive', 'Apple Notes'];
      names.forEach((n, i) => {
        const p = spring(mark(lt, tA + 0.5 + i * 0.25, 0.6));
        if (p <= 0) return;
        g.save();
        const fx = x + 40 + i * 108, fy = y + 70;
        g.translate(fx + 26, fy + 26);
        g.scale(p, p);
        g.shadowColor = 'rgba(0,0,0,0.35)';
        g.shadowBlur = 8;
        folderIcon(-26, -26, 52, [C.pink, C.drive, C.amber][i]);
        g.restore();
        withAlpha(p, () => {
          g.save();
          g.shadowColor = 'rgba(0,0,0,0.8)';
          g.shadowBlur = 4;
          text(n, fx + 26, fy + 78, { size: 11, align: 'center', color: '#ffffff', weight: 600 });
          g.restore();
        });
      });
      g.fillStyle = 'rgba(30,33,39,0.88)';
      g.fillRect(x, y + h - 18, w, 18);
      withAlpha(1 - smooth(mark(lt, tA + 0.4, 0.5)), () => text('?', x + w / 2, y + h / 2 + 10,
        { size: 46, weight: 700, color: 'rgba(255,255,255,0.75)', align: 'center', family: DISPLAY }));
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

// --- Tray: the Windows 11 desktop, the tray icon, the panel (376 px wide in the design) ----------------
const DESK_CROP = [30, 0, 726, 550]; // the part of the wallpaper on the 700 x 530 desktop
const PW = 376, PH = 600;
const TRAY_EVENTS = [
  ['up', C.drive, 'Field guide.pdf', 'Uploaded · iCloud Drive/Trips', '14:05'],
  ['art', 3, 'IMG_2041.HEIC', 'Downloaded · 2026/11', '09:12'],
  ['note', C.amber, 'Garden plan', 'Updated · Notes/Home', '07:15'],
];
function tray(sc, w, h) {
  const t = sc.local;
  const tc = ev(sc, 0), tok = ev(sc, 1);
  const area = [0, 0, w, h];
  floatShadow(0, 0, w, h, 14, 40, 18);
  g.save();
  rr(0, 0, w, h, 14);
  g.clip();
  wallTo('sharp', DESK_CROP, 0, 0, w, h);
  // the taskbar: the same glass, a hairline on top, the apps in the middle, the tray on the right
  const tb = h - 40;
  glass(0, tb, w, 40, 0, DESK_CROP, area, 'rgba(30,33,39,0.86)');
  g.fillStyle = C.stroke2;
  g.fillRect(0, tb, w, 1);
  const my = tb + 20;
  // start
  [[0, 0], [1, 0], [0, 1], [1, 1]].forEach(([i, j]) => {
    const sg = g.createLinearGradient(0, my - 9, 0, my + 9);
    sg.addColorStop(0, '#7fb6ff');
    sg.addColorStop(1, '#3b82f6');
    fillRR(262 + i * 9.5, my - 9 + j * 9.5, 8.5, 8.5, 1.5, sg);
  });
  ico('search', 300, my - 9, 18, C.ink);
  folderIcon(332, my - 10, 20, C.amber);
  fillRR(338, tb + 35, 8, 2.5, 1.25, C.t3);
  g.beginPath();
  g.arc(381, my, 9, 0, Math.PI * 2);
  const bg2 = g.createLinearGradient(372, my - 9, 390, my + 9);
  bg2.addColorStop(0, '#5fd3c6');
  bg2.addColorStop(1, '#3b82f6');
  g.fillStyle = bg2;
  g.fill();
  fillRR(406, my - 9, 18, 18, 4, '#2a2f38');
  text('>_', 415, my + 4, { size: 9, weight: 700, family: MONO, align: 'center', color: C.ink });
  // the tray
  ico('chevUp', 526, my - 7, 14, C.t2);
  const ix = 556, iy = my;
  const hover = clamp(1 - Math.abs(t - tc) / 0.4);
  const open = easeOut(mark(t, tc + 0.05, 0.3));
  fillRR(ix - 13, iy - 14, 26, 28, 4, `rgba(255,255,255,${0.09 * Math.max(hover, open)})`);
  logoTile(ix, iy, 16, 3);
  g.strokeStyle = C.ink;
  g.lineWidth = 1.4;
  g.lineCap = 'round';
  for (let k = 0; k < 3; k++) {
    g.beginPath();
    g.arc(584, iy + 6, 3 + k * 3.6, -Math.PI * 0.75, -Math.PI * 0.25);
    g.stroke();
  }
  g.beginPath();
  g.moveTo(602, iy - 3);
  g.lineTo(606, iy - 3);
  g.lineTo(611, iy - 7);
  g.lineTo(611, iy + 7);
  g.lineTo(606, iy + 3);
  g.lineTo(602, iy + 3);
  g.closePath();
  g.stroke();
  text('14:32', 688, tb + 17, { size: 11, color: C.ink, align: 'right' });
  text('25.11.2026', 688, tb + 31, { size: 11, color: C.ink, align: 'right' });
  // the notification card when the sync is done, beside the panel
  const px = w - 12 - PW * KT, py = tb - 12 - PH * KT;
  const tCard = tok + 0.25;
  const ca = easeOut(mark(t, tCard, 0.3));
  if (ca > 0) {
    const cw = 360 * KT, chh = 182 * KT;
    const cx = px - 9 - cw, cy = tb - 12 - chh + (1 - ca) * 14 * KT;
    withAlpha(ca, () => {
      g.save();
      g.translate(cx + cw / 2, cy + chh / 2);
      g.scale(lerp(0.98, 1, ca), lerp(0.98, 1, ca));
      g.translate(-cw / 2, -chh / 2);
      floatShadow(0, 0, cw, chh, 12 * KT, 36, 12, 0.45);
      g.restore();
      const s = lerp(0.98, 1, ca);
      const ox = cx + (cw - cw * s) / 2, oy = cy + (chh - chh * s) / 2;
      glass(ox, oy, cw * s, chh * s, 12 * KT * s, DESK_CROP, area);
      g.save();
      g.translate(ox, oy);
      g.scale(KT * s, KT * s);
      photosCard(t - tCard);
      g.restore();
    });
  }
  // the panel rises 12 px and fades in
  if (open > 0) {
    const oy = py + (1 - open) * 12 * KT;
    withAlpha(open, () => {
      floatShadow(px, oy, PW * KT, PH * KT, 12 * KT, 40, 14, 0.5);
      glass(px, oy, PW * KT, PH * KT, 12 * KT, DESK_CROP, area);
      g.save();
      g.translate(px, oy);
      g.scale(KT, KT);
      panel(t, tc, tok);
      g.restore();
    });
  }
  g.restore();
  strokeRR(0.5, 0.5, w - 1, h - 1, 14, C.stroke2);
  const [x, y] = path([[0, 250, 200], [tc - 0.05, ix + 2, iy - 2], [tc + 0.8, 320, 230]], t);
  cursor(x, y, clickPress(t, tc));
  ripple(ix, iy, (t - tc) / 0.5);
}
// the panel's content, in the design's pixels (376 x 600)
function panel(t, tc, tok) {
  const W = PW, H = PH;
  strokeRR(0.5, 0.5, W - 1, H - 1, 12, C.stroke2);
  const prog = easeInOut(mark(t, tc + 0.2, tok - tc - 0.3));
  const n = Math.round(lerp(612, 1284, prog));
  const pct = Math.floor((n / 1284) * 100);
  // the syncing state goes first, then the finished one comes in (never both at once)
  const busy = 1 - smooth(mark(t, tok, 0.12)), ok = smooth(mark(t, tok + 0.1, 0.2));
  // head: the logo tile, the name, the status line, Settings
  const lg = g.createLinearGradient(0, 14, 0, 50);
  lg.addColorStop(0, '#1e293b');
  lg.addColorStop(1, '#0b1220');
  fillRR(14, 14, 36, 36, 10, lg);
  strokeRR(14.5, 14.5, 35, 35, 9.5, 'rgba(255,255,255,0.08)');
  logoTile(32, 32, 30, 7);
  text('Skyfold', 62, 30, { size: 16, weight: 600, family: DISPLAY });
  withAlpha(busy, () => {
    spinIcon(68, 42, 12, C.accent, t * 5, 2);
    text('Syncing', 79, 46.5, { size: 12, color: C.accent });
  });
  withAlpha(ok, () => {
    g.beginPath();
    g.arc(65.5, 42.2, 6.5, 0, Math.PI * 2);
    g.fillStyle = rgba(C.green, 0.22);
    g.fill();
    g.beginPath();
    g.arc(65.5, 42.2, 3.5, 0, Math.PI * 2);
    g.fillStyle = C.green;
    g.fill();
    text('Everything is in sync', 75, 46.5, { size: 12, color: C.green });
  });
  ico('gear', 336, 23, 18, C.t2);
  // the status card
  fillRR(12, 60, 352, 72, 8, C.layer);
  strokeRR(12.5, 60.5, 351, 71, 8, C.stroke);
  withAlpha(busy, () => {
    g.beginPath();
    g.arc(46, 96, 22, 0, Math.PI * 2);
    g.fillStyle = C.layer2;
    g.fill();
    g.beginPath();
    g.moveTo(46, 96);
    g.arc(46, 96, 22, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (n / 1284));
    g.closePath();
    g.fillStyle = C.accent;
    g.fill();
    g.beginPath();
    g.arc(46, 96, 18, 0, Math.PI * 2);
    g.fillStyle = C.micaSolid;
    g.fill();
    text(`${pct}%`, 46, 100, { size: 11, weight: 600, family: MONO, align: 'center' });
    text('Downloading photos', 80, 92, { size: 14, weight: 600, family: DISPLAY });
    const eta = n < 900 ? 3 : n < 1150 ? 2 : 1;
    text(`${num(n)} of 1,284 · 4.2 MB/s · ~${eta} min`, 80, 110.5, { size: 12.5, family: MONO, color: C.t2 });
  });
  withAlpha(ok, () => {
    okBadge(46, 96, 22, mark(t, tok + 0.05, 0.5));
    text('Up to date', 80, 83, { size: 14, weight: 600, family: DISPLAY });
    wrap('Photos, iCloud Drive and Notes are in step with iCloud.', 272, 12.5, 400, TEXT)
      .forEach((l, i) => text(l, 80, 101 + i * 18, { size: 12.5, color: C.t2 }));
  });
  // the services, one card with hairlines across
  fillRR(12, 140, 352, 170, 8, C.layer);
  strokeRR(12.5, 140.5, 351, 169, 8, C.stroke);
  const rows = [
    ['photo', 'Photos', C.pink, `${num(n)} of 1,284`, '1,284 photos · 36 videos'],
    ['folder', 'iCloud Drive', C.drive, 'Waiting', '4,210 files · 31.0 GB'],
    ['note', 'Notes', C.amber, 'Up to date', '86 notes'],
  ];
  rows.forEach(([ic, name, color, working, idle], i) => {
    const ry = 141 + i * 56;
    if (i) {
      g.fillStyle = C.stroke;
      g.fillRect(13, ry - 0.5, 350, 1);
    }
    fillRR(24, ry + 12, 32, 32, 9, rgba(color, 0.16));
    ico(ic, 31.5, ry + 19.5, 17, color);
    text(name, 68, ry + 25, { size: 14, weight: 600 });
    withAlpha(busy, () => text(working, 68, ry + 43.5, { size: 12.5, color: C.t3 }));
    withAlpha(ok, () => text(idle, 68, ry + 43.5, { size: 12.5, color: C.t3 }));
    ico('chev', 334, ry + 19, 18, C.t3);
  });
  // iCloud storage
  text('iCloud storage', 16, 335.5, { size: 12.5, color: C.t2 });
  text('74 / 200 GB', 360, 335.5, { size: 12.5, family: MONO, color: C.t2, align: 'right' });
  fillRR(16, 348, 344, 6, 3, C.layer2);
  const grow = easeOut(mark(t, tc + 0.2, 0.7));
  let sx = 16;
  [[0.19, C.pink], [0.145, C.drive], [0.035, C.amber]].forEach(([f, color]) => {
    const ww = 344 * f * grow;
    if (ww > 1) fillRR(sx, 348, ww, 6, 3, color);
    sx += ww + 2;
  });
  let lx = 16;
  [['Photos', '38 GB', C.pink], ['Drive', '29 GB', C.drive], ['Other', '7 GB', C.amber]].forEach(([k, v, color]) => {
    g.beginPath();
    g.arc(lx + 3.5, 371, 3.5, 0, Math.PI * 2);
    g.fillStyle = color;
    g.fill();
    lx += 13;
    lx += text(`${k} `, lx, 375, { size: 12, color: C.t3 });
    lx += text(v, lx, 375, { size: 12, weight: 600, color: C.t2 }) + 14;
  });
  // Today: what happened, newest first; the finished download arrives on top
  text('Today', 16, 406, { size: 12, weight: 600, color: C.t3 });
  text('Clear', 360, 406, { size: 12.5, weight: 600, color: C.accent, align: 'right' });
  const ins = easeOut(mark(t, tok + 0.1, 0.3)), arrive = smooth(mark(t, tok + 0.32, 0.25));
  const list = [['art', 7, '672 new photos', 'Downloaded · 2026/11', 'now'], ...TRAY_EVENTS];
  g.save();
  g.beginPath();
  g.rect(0, 413, W, 132);
  g.clip();
  list.forEach((e, i) => {
    const [kind, a, title, sub, when] = e;
    const isNew = i === 0;
    const ry = isNew ? 413 + (1 - arrive) * 8 : 413 + (i - 1 + ins) * 50;
    const fade = clamp((545 - (ry + 18)) / 32) * (isNew ? arrive : 1);
    withAlpha(fade, () => {
      if (kind === 'art') photoIn(a, 16, ry + 6, 32, 32, 7);
      else {
        fillRR(16, ry + 6, 32, 32, 9, rgba(a, 0.16));
        ico(kind, 23.5, ry + 13.5, 17, a);
      }
      text(title, 60, ry + 22, { size: 14, weight: 500 });
      text(sub, 60, ry + 40, { size: 12, color: C.t3 });
      text(when, 360, ry + 30, { size: 12, family: MONO, color: C.t3, align: 'right' });
    });
  });
  g.restore();
  // the footer: when, the main button (Stop while syncing), three square buttons
  g.fillStyle = 'rgba(30,33,39,0.4)';
  g.fillRect(0.5, 545, W - 1, H - 545.5);
  g.fillStyle = C.stroke;
  g.fillRect(0.5, 545, W - 1, 1);
  withAlpha(busy, () => {
    text('Started', 12, 567.5, { size: 11.5, color: C.t3 });
    text('at 14:31', 12, 582, { size: 11.5, color: C.t3 });
    btn(106, 555, 132, 34, 'Stop', 'stop');
  });
  withAlpha(ok, () => {
    text('Last checked', 12, 567.5, { size: 11.5, color: C.t3 });
    text('at 14:32', 12, 582, { size: 11.5, color: C.t3 });
    btn(106, 555, 132, 34, 'Sync now', 'sync', { primary: true });
  });
  ['folder', 'pin', 'more'].forEach((ic, i) => btn(246 + i * 42, 555, 34, 34, '', ic));
}
// the notification card (360 px wide in the design): tile, "Skyfold · Photos · now", a photo strip, buttons, a countdown
function photosCard(since) {
  const W = 360, H = 182;
  strokeRR(0.5, 0.5, W - 1, H - 1, 12, C.stroke2);
  fillRR(14, 14, 32, 32, 9, rgba(C.pink, 0.16));
  ico('photo', 21.5, 21.5, 17, C.pink);
  logoTile(65, 22.5, 14, 4);
  text('Skyfold · Photos · now', 78, 27, { size: 12, color: C.t3 });
  text('672 new photos', 58, 48, { size: 14, weight: 600, family: DISPLAY });
  text('Saved to Pictures › iCloud Photos › 2026 › 11.', 58, 67, { size: 13, color: C.t2 });
  [7, 12, 2].forEach((p, i) => photoIn(p, 58 + i * 48, 80, 44, 44, 6));
  const b1 = measure('View photos', 12.5, 600) + 24, b2 = measure('Show in folder', 12.5, 600) + 24;
  btn(58, 136, b1, 30, 'View photos', null, { size: 12.5 });
  btn(58 + b1 + 8, 136, b2, 30, 'Show in folder', null, { size: 12.5 });
  g.save();
  rr(0, 0, W, H, 12);
  g.clip();
  g.fillStyle = rgba(C.pink, 0.75);
  g.fillRect(0, H - 2, W * (1 - clamp(since / 7)), 2);
  g.restore();
}

// --- Photos: they fly into iCloud Photos\2026\11 in File Explorer --------------------------------------
function explorerBar(W, crumbs, searchLabel) {
  ico('arrowL', 14, 53, 18, C.t2);
  ico('arrowR', 44, 53, 18, C.t3);
  ico('up', 74, 53, 18, C.t2);
  const aw = W - 104 - 236;
  fillRR(104, 46, aw, 32, 6, C.layer2);
  strokeRR(104.5, 46.5, aw - 1, 31, 6, C.stroke2);
  let cx = 116;
  crumbs.forEach((c, i) => {
    cx += text(c, cx, 66.5, { size: 12.5, color: i === crumbs.length - 1 ? C.ink : C.t2 }) + 7;
    if (i < crumbs.length - 1) {
      ico('chev', cx - 2, 56, 12, C.t3);
      cx += 15;
    }
  });
  searchBox(W - 222, 46, 208, 32, '', { placeholder: searchLabel });
}
function photos(sc) {
  const t = sc.local;
  const W = CW / K, H = CH / K;
  g.save();
  g.scale(K, K);
  appWindow(W, H, 'iCloud Photos — File Explorer', (x, y, s) => folderIcon(x, y, s, C.amber));
  explorerBar(W, ['This PC', 'Pictures', 'iCloud Photos', '2026', '11'], 'Search 11');
  // the navigation pane
  const nav = [['Home', 0], ['Pictures', 0], ['iCloud Photos', 1], ['2025', 2], ['2026', 2], ['11', 3], ['Albums', 2],
    ['iCloud Drive', 0], ['Documents', 0]];
  nav.forEach(([n, lvl], i) => {
    const y = 92 + i * 34;
    if (n === '11') {
      fillRR(8, y, 184, 32, 6, C.layer2);
      fillRR(8, y + 9, 3, 14, 1.5, C.accent);
    }
    const x = 20 + lvl * 14;
    folderIcon(x, y + 7, 18, n.startsWith('iCloud') ? C.drive : C.amber);
    text(n, x + 26, y + 21, { size: 13 });
  });
  // the folder, the photos arriving from iCloud
  const px = 200, py = 88, pw = W - 208, ph = H - 88 - 36;
  pane(px, py, pw, ph);
  const cols = 5, tw = 118, gap = 20, gx = px + 22, gy = py + 20;
  const names = [2031, 2032, 2033, 2034, 2035, 2036, 2037, 2038, 2039, 2040, 2041, 2042, 2043, 2044, 2045];
  names.forEach((n, i) => {
    const at = 0.6 + i * 0.16;
    const p = spring(mark(t, at, 0.7));
    if (p <= 0) return;
    const x = gx + (i % cols) * (tw + gap), y = gy + Math.floor(i / cols) * (tw * 0.75 + 40);
    g.save();
    g.translate(x + tw / 2, lerp(-190, y + tw * 0.375, p));
    g.scale(lerp(0.6, 1, p), lerp(0.6, 1, p));
    withAlpha(clamp(p * 2), () => {
      photoIn(n, -tw / 2, -tw * 0.375, tw, tw * 0.75, 4);
      if (n % 4 === 1) { // a Live Photo's video sits next to it, in the same folder
        fillRR(-tw / 2 + 6, -tw * 0.375 + 6, 34, 17, 4, 'rgba(0,0,0,0.5)');
        text('LIVE', -tw / 2 + 23, -tw * 0.375 + 18.5, { size: 10, weight: 700, align: 'center' });
      }
      text(`IMG_${n}.HEIC`, 0, tw * 0.375 + 20, { size: 12, color: C.t2, align: 'center' });
    });
    g.restore();
  });
  // the status bar
  const tok = ev(sc, 4);
  if (t < tok) {
    const p = easeInOut(mark(t, 0.5, tok - 0.5));
    spinIcon(22, H - 18, 14, C.accent, t * 5, 2);
    text(`Downloading · ${num(lerp(0, 1284, p))} of 1,284 photos`, 36, H - 13.5, { size: 12, color: C.accent });
    fillRR(W - 230, H - 21, 216, 4, 2, C.layer2);
    fillRR(W - 230, H - 21, 216 * p, 4, 2, C.accentStrong);
  } else {
    okBadge(22, H - 18, 9, (t - tok) / 0.35);
    text('1,284 photos · 36 videos · all on this PC', 38, H - 13.5, { size: 12, color: C.green });
  }
  g.restore();
}

// --- Gallery: the Photos window (sidebar, the pictures under a title, the floating switch, the viewer) ---
function gallery(sc) {
  const t = sc.local, len = sc.length;
  const tClick = ev(sc, 0), tHeart = ev(sc, 1);
  const W = CW / K, H = CH / K; // 933 x 613
  g.save();
  g.scale(K, K);
  appWindow(W, H, 'Photos', skyfoldIcon);
  // the sidebar
  const fav = t > tHeart + 0.1 ? 6 : 5;
  sideRow(8, 44, 184, 'Library', 'photo', C.pink, 48, true);
  sideRow(8, 80, 184, 'Recently added', 'clock', C.ink, 12);
  sideRow(8, 116, 184, 'Favourites', 'heart', C.ink, fav);
  sideLabel(8, 174, 'Media types');
  sideRow(8, 182, 184, 'Videos', 'dl', C.ink, 3);
  sideRow(8, 218, 184, 'Live Photos', 'photo', C.ink, 2);
  sideLabel(8, 274, 'Albums');
  sideRow(8, 282, 184, 'Glasswater', 'folder', C.ink, 9);
  sideRow(8, 318, 184, 'Orchard walks', 'folder', C.ink, 6);
  // the pictures, gliding up as the wheel turns
  const px = 200, py = 40, pw = W - 208, ph = H - 48;
  pane(px, py, pw, ph);
  const cols = 4, ts = (pw - 2 * (cols - 1)) / cols;
  const glides = [[0.16, 93], [0.39, 147], [0.62, 120]].map(([f, d]) => [f * tClick, d]);
  let scroll = 0;
  glides.forEach(([at, d]) => { scroll += d * easeOut(mark(t, at, 0.5)); });
  const target = 13; // row 3, column 1
  const tileAt = (i) => ({ x: px + (i % cols) * (ts + 2), y: py + 74 + Math.floor(i / cols) * (ts + 2) - scroll, w: ts, h: ts });
  g.save();
  rr(px, py, pw, ph, [8, 8, 12, 8]);
  g.clip();
  for (let i = 0; i < 48; i++) {
    const r = tileAt(i);
    if (r.y > py + ph || r.y + ts < py) continue;
    photoIn(libraryPhoto(i), r.x, r.y, r.w, r.h);
  }
  // the title over the pictures, on a band of the window's colour
  const band = g.createLinearGradient(0, py, 0, py + 74);
  band.addColorStop(0, 'rgba(30,33,39,0.92)');
  band.addColorStop(1, 'rgba(30,33,39,0)');
  g.fillStyle = band;
  g.fillRect(px, py, pw, 74);
  text('Library', px + 18, py + 37, { size: 24, weight: 600, family: DISPLAY });
  text('October – November 2026 · 48 items', px + 18, py + 56, { size: 12.5, color: C.t2 });
  searchBox(px + pw - 238, py + 14, 220, 34, '', { bg: C.mica });
  // Years · Months · All Photos, floating at the bottom (the selection is neutral, not the accent)
  const segs = ['Years', 'Months', 'All Photos'];
  const sw = segs.map((s) => measure(s, 13, 600) + 26);
  const segW = sw.reduce((a, b) => a + b, 0) + 8, segH = 38;
  const sx0 = px + pw / 2 - segW / 2, sy0 = py + ph - 14 - segH;
  g.save();
  g.shadowColor = 'rgba(0,0,0,0.35)';
  g.shadowBlur = 24;
  g.shadowOffsetY = 8;
  fillRR(sx0, sy0, segW, segH, 10, 'rgba(30,33,39,0.9)');
  g.restore();
  strokeRR(sx0 + 0.5, sy0 + 0.5, segW - 1, segH - 1, 10, C.stroke);
  let bx = sx0 + 4;
  segs.forEach((s, i) => {
    if (i === 2) {
      g.save();
      g.shadowColor = 'rgba(0,0,0,0.3)';
      g.shadowBlur = 2;
      g.shadowOffsetY = 1;
      fillRR(bx, sy0 + 4, sw[i], segH - 8, 7, '#2a2e36');
      g.restore();
    }
    text(s, bx + sw[i] / 2, sy0 + segH / 2 + 4.5, { size: 13, weight: 600, align: 'center', color: i === 2 ? C.ink : C.t2 });
    bx += sw[i];
  });
  g.restore();
  // the viewer opens from the photo clicked, on the same material
  const tile = tileAt(target);
  const vo = easeInOut(mark(t, tClick + 0.05, 0.5));
  const heartX = px + pw - 12 - 34 * 3 - 20, heartY = py + 10; // the heart button's corner
  if (vo > 0) {
    g.save();
    rr(px, py, pw, ph, [8, 8, 12, 8]);
    g.clip();
    withAlpha(clamp(vo * 1.6), () => {
      g.fillStyle = '#1d2025';
      g.fillRect(px, py, pw, ph);
    });
    const fw = pw - 120, fh = fw / (400 / 280);
    const fx = px + 60, fy = py + 54 + (ph - 54 - 50 - fh) / 2;
    const r = { x: lerp(tile.x, fx, vo), y: lerp(tile.y, fy, vo), w: lerp(tile.w, fw, vo), h: lerp(tile.h, fh, vo) };
    g.save();
    g.shadowColor = `rgba(0,0,0,${0.35 * vo})`;
    g.shadowBlur = 24;
    g.shadowOffsetY = 8;
    fillRR(r.x, r.y, r.w, r.h, 6 * vo, '#1d2025');
    g.restore();
    photoIn(libraryPhoto(target), r.x, r.y, r.w, r.h, 6 * vo);
    withAlpha(smooth(mark(t, tClick + 0.35, 0.3)), () => {
      const bw = 12 + 14 + 8 + measure('Library', 12.5, 600) + 12;
      btn(px + 12, py + 12, bw, 30, 'Library', 'back', { size: 12.5, iconSize: 14 });
      text('Wednesday, 18 November 2026', px + pw / 2, py + 26, { size: 14, weight: 600, family: DISPLAY, align: 'center' });
      text('IMG_2014.HEIC · 14 of 48', px + pw / 2, py + 43, { size: 12, family: MONO, color: C.t3, align: 'center' });
      // the heart, Info, Delete: 34 px square buttons
      const hp = easeOut(mark(t, tHeart, 0.3));
      const burst = mark(t, tHeart, 0.6);
      const pop = 1 + Math.sin(clamp(burst / 0.6) * Math.PI) * 0.3;
      btn(heartX, heartY, 34, 34, '', null);
      g.save();
      g.translate(heartX + 17, heartY + 17);
      g.scale(pop, pop);
      ico('heart', -9, -9, 18, hp > 0.5 ? C.pink : C.t2, { fill: hp > 0 ? rgba(C.pink, hp) : null });
      g.restore();
      if (burst > 0 && burst < 1) {
        for (let i = 0; i < 10; i++) {
          const a = (i / 10) * Math.PI * 2;
          const d = 14 + burst * 30;
          g.fillStyle = rgba(i % 2 ? C.pink : C.amber, 1 - burst);
          g.beginPath();
          g.arc(heartX + 17 + Math.cos(a) * d, heartY + 17 + Math.sin(a) * d, 3, 0, Math.PI * 2);
          g.fill();
        }
      }
      btn(heartX + 44, heartY, 34, 34, '', 'info');
      btn(heartX + 88, heartY, 34, 34, '', 'trash');
      // the film strip, centred: the current photo wider, the others dimmed
      const k = target, from = k - 7, to = k + 7;
      const widths = [];
      for (let j = from; j <= to; j++) widths.push(j === k ? 40 : 30);
      const total = widths.reduce((a, b) => a + b, 0) + 3 * (widths.length - 1);
      let x = px + pw / 2 - total / 2;
      for (let j = from; j <= to; j++) {
        const ww = j === k ? 40 : 30;
        withAlpha(j === k ? 1 : 0.6, () => photoIn(libraryPhoto(((j % 48) + 48) % 48), x, py + ph - 40, ww, 30, 4));
        x += ww + 3;
      }
    });
    // the toast
    const toast = smooth(mark(t, tHeart + 0.15, 0.3)) * (1 - smooth(mark(t, len - 0.7, 0.3)));
    withAlpha(toast, () => {
      const tw = measure('Added to Favourites', 13, 600) + 52;
      const tx = px + pw / 2 - tw / 2, ty = py + ph - 100 + (1 - toast) * 6;
      g.save();
      g.shadowColor = 'rgba(0,0,0,0.35)';
      g.shadowBlur = 24;
      g.shadowOffsetY = 8;
      fillRR(tx, ty, tw, 38, 10, 'rgba(36,40,47,0.96)');
      g.restore();
      strokeRR(tx + 0.5, ty + 0.5, tw - 1, 37, 10, C.stroke2);
      ico('heart', tx + 14, ty + 10, 18, C.pink, { fill: C.pink });
      text('Added to Favourites', tx + 40, ty + 24, { size: 13, weight: 600 });
    });
    g.restore();
  }
  g.restore();
  // the iPhone that gets the heart too (card units)
  const ph2 = spring(mark(t, tHeart + 0.4, 0.7));
  if (ph2 > 0) {
    g.save();
    g.translate(CW - 30 + (1 - ph2) * 140, CH - 40);
    g.rotate(0.06);
    phoneFrame(-110, -230, 120, 240, (x, y, w) => {
      text('18 November', x + 10, y + 30, { size: 11, weight: 700 });
      photoIn(libraryPhoto(target), x, y + 50, w, w * 0.75);
      heart(x + w / 2, y + 50 + w * 0.75 + 30, 14, 1);
      text('Favourite', x + w / 2, y + 50 + w * 0.75 + 58, { size: 10, color: C.pink, align: 'center', weight: 600 });
    });
    g.restore();
    withAlpha(smooth(mark(t, tHeart + 0.8, 0.4)), () => {
      fillRR(CW - 152, 140, 128, 24, 12, 'rgba(58,30,40,0.96)');
      strokeRR(CW - 151.5, 140.5, 127, 23, 11.5, rgba(C.pink, 0.4));
      text('on your iPhone too', CW - 88, 156.5, { size: 11.5, color: C.pink, align: 'center', weight: 650 });
    });
  }
  // the cursor: the wheel over the pictures, a click on the photo, a click on the heart (design px → card units)
  const tcx = tile.x + tile.w * 0.55, tcy = py + 74 + Math.floor(target / cols) * (ts + 2) - 360 + tile.h * 0.5;
  const keys = [[0, 560, 480], [0.15 * tClick, 575, 400], [0.76 * tClick, 590, 400], [tClick - 0.05, tcx, tcy],
    [tClick + 0.6, 640, 330], [tHeart - 0.05, heartX + 17, heartY + 18], [tHeart + 0.8, 700, 160]];
  const [cx, cy] = path(keys, t).map((v) => v * K);
  if (t < tClick) { // the wheel
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
  ripple(tcx * K, tcy * K, (t - tClick) / 0.45);
  ripple((heartX + 17) * K, (heartY + 17) * K, (t - tHeart) / 0.45);
}

// --- iCloud Drive: Ctrl+S on the PC goes up to the iPhone; a new file comes back down -------------------
function drive(sc) {
  const t = sc.local;
  const tSave = ev(sc, 0), tOk = ev(sc, 1);
  const ww = 440, W = ww / K, H = CH / K; // 587 x 613 px
  g.save();
  g.scale(K, K);
  appWindow(W, H, 'Projects — File Explorer', (x, y, s) => folderIcon(x, y, s, C.drive));
  ico('arrowL', 14, 53, 18, C.t2);
  ico('arrowR', 44, 53, 18, C.t3);
  fillRR(74, 46, W - 88, 32, 6, C.layer2);
  strokeRR(74.5, 46.5, W - 89, 31, 6, C.stroke2);
  let ax = 86 + text('iCloud Drive', 86, 66.5, { size: 12.5, color: C.t2 }) + 7;
  ico('chev', ax - 2, 56, 12, C.t3);
  text('Projects', ax + 15, 66.5, { size: 12.5 });
  pane(8, 88, W - 16, H - 88 - 36);
  text('Name', 54, 113, { size: 12, color: C.t3 });
  text('Date modified', 330, 113, { size: 12, color: C.t3 });
  text('Status', W - 26, 113, { size: 12, color: C.t3, align: 'right' });
  g.fillStyle = C.stroke;
  g.fillRect(9, 124, W - 18, 1);
  const newIn = easeOut(mark(t, tOk + 0.9, 0.5));
  const files = [
    ['Budget 2026.xlsx', '#21a366'], ['Letter.docx', '#2b7cd3'], ['House plans.pdf', '#e5533d'],
    ['Trip photos.zip', '#c9a227'], ['Ideas.txt', '#8a94a6'],
  ];
  const rowsY = 132, step = 48;
  const status = (x, y, p) => { // available on this PC: a green check in a ring
    g.beginPath();
    g.arc(x, y, 8, 0, Math.PI * 2);
    g.strokeStyle = C.green;
    g.lineWidth = 1.5;
    g.stroke();
    ico('check', x - 6, y - 6, 12, C.green, { lw: 2.2, dash: p });
  };
  // a new file arrives from the iPhone
  withAlpha(newIn, () => {
    const y = rowsY;
    fillRR(16, y, W - 32, 42, 6, rgba(C.green, 0.12 * (1 - smooth(mark(t, tOk + 1.6, 0.6)))));
    fileIcon(28, y + 10, 22, '#8a94a6');
    text('Packing list.txt', 54, y + 26, { size: 13.5 });
    text('just now', 330, y + 26, { size: 12.5, color: C.t2 });
    status(W - 42, y + 21, mark(t, tOk + 1.2, 0.3));
  });
  files.forEach(([n, color], i) => {
    const y = rowsY + (i + newIn) * step;
    const isBudget = i === 0;
    const sel = isBudget && t > tSave - 0.1;
    if (sel) fillRR(16, y, W - 32, 42, 6, C.accentSoft);
    fileIcon(28, y + 10, 22, color);
    text(n, 54, y + 26, { size: 13.5 });
    text(isBudget && t > tSave ? 'just now' : `2${i}.11.2026 09:1${i}`, 330, y + 26, { size: 12.5, color: C.t2 });
    if (isBudget && t > tSave && t < tOk) {
      spinIcon(W - 42, y + 21, 18, C.accent, t * 5, 1.8);
      const p = easeInOut(mark(t, tSave + 0.15, tOk - tSave - 0.2));
      fillRR(54, y + 33, 220, 3, 1.5, C.layer2);
      fillRR(54, y + 33, 220 * p, 3, 1.5, C.accentStrong);
    } else status(W - 42, y + 21, isBudget && t > tOk ? (t - tOk) / 0.3 : 1);
  });
  withAlpha(t > tSave && t < tOk + 0.3 ? 1 : 0, () => {
    spinIcon(22, H - 18, 14, C.accent, t * 5, 2);
    text('Uploading · 4.2 MB/s', 36, H - 13.5, { size: 12, color: C.accent });
  });
  g.restore();
  // the iPhone, with the same folder (card units)
  const px = 500, py = 40, pw = 190, ph = 380;
  phoneFrame(px, py, pw, ph, (x, y, w) => {
    text('‹ iCloud Drive', x + 10, y + 34, { size: 10.5, color: C.accent });
    text('Projects', x + 10, y + 60, { size: 17, weight: 700, family: DISPLAY });
    const list = [['Packing list.txt', '#8a94a6', 'Just now'], ...files.map(([n, c]) => [n, c, 'Yesterday'])];
    list.forEach(([n, color, when], i) => {
      if (i === 0 && t < tOk + 0.6) return;
      const yy = y + 78 + (i - (t < tOk + 0.6 ? 1 : 0)) * 40 + (i === 0 ? (1 - easeOut(mark(t, tOk + 0.6, 0.4))) * -10 : 0);
      const flash = i === 1 ? Math.max(0, 1 - Math.abs(t - tOk - 0.15) / 0.5) : 0;
      if (flash > 0) fillRR(x + 4, yy - 4, w - 8, 36, 6, rgba(C.green, 0.25 * flash));
      fileIcon(x + 10, yy + 2, 20, color);
      text(n, x + 36, yy + 13, { size: 10.5, maxW: w - 46 });
      text(i === 1 && t > tOk ? 'Just now' : when, x + 36, yy + 26, { size: 9, color: C.t3 });
    });
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
  const fileAt = [(W - 42) * K, (rowsY + 21) * K];
  if (up) {
    dots(fileAt, [cx - 6, cy + 14], 1);
    dots([cx + 6, cy + 14], [px + 30, py + 120], 1);
  }
  if (down) {
    dots([px + 30, py + 120], [cx + 6, cy + 14], 1);
    dots([cx - 6, cy + 14], fileAt, 1);
  }
  // the cursor saves the budget (Ctrl+S)
  const [kx, ky] = path([[0, 300, 330], [tSave - 0.05, 120, (rowsY + 24) * K], [tSave + 1, 160, 300]], t);
  cursor(kx, ky, clickPress(t, tSave));
  withAlpha(clamp(1 - Math.abs(t - tSave - 0.25) / 0.5), () => {
    fillRR(kx + 18, ky + 22, 62, 24, 6, 'rgba(36,40,47,0.96)');
    strokeRR(kx + 18.5, ky + 22.5, 61, 23, 6, C.stroke2);
    text('Ctrl + S', kx + 49, ky + 38, { size: 11.5, weight: 650, align: 'center' });
  });
}

// --- Notes: search finds "garden"; the note with its checklist and table ----------------------------
const NOTE_LIST = [
  ['Garden plan', 'Jobs for the garden in spring', '07:15'],
  ['Lake weekend', 'Glasswater Inlet, two nights', 'Sat'],
  ['Shopping list', 'Milk, bread, coffee, apples.', '18 Nov'],
  ['Book ideas', 'A story told by a lighthouse…', '12 Nov'],
];
function chk(x, y, label, done, strike = done) {
  // 18 px rounded box; ticked: the accent with the check, the text struck through
  strokeRR(x + 0.75, y + 0.75, 16.5, 16.5, 5, C.t3, 1.5);
  if (done > 0) withAlpha(clamp(done * 2), () => {
    fillRR(x, y, 18, 18, 5, C.accentStrong);
    ico('check', x + 3, y + 3, 12, C.onAccent, { lw: 2.4, dash: done });
  });
  const color = strike > 0.5 ? C.t3 : C.ink;
  text(label, x + 28, y + 14, { size: 14, color, strike });
}
function notes(sc) {
  const t = sc.local;
  const tSearch = ev(sc, 0), tPick = ev(sc, 1), tTick = ev(sc, 2);
  const W = CW / K, H = CH / K;
  g.save();
  g.scale(K, K);
  appWindow(W, H, 'Notes', skyfoldIcon);
  // the sidebar: All notes, then the iCloud folders
  sideRow(8, 44, 184, 'All notes', 'note', C.amber, 4, true);
  sideLabel(8, 102, 'iCloud');
  [['Home', 2], ['Travel', 1], ['Work', 1]].forEach(([n, c], i) => sideRow(8, 110 + i * 36, 184, n, 'folder', C.amber, c));
  const px = 200, py = 40, pw = W - 208, ph = H - 48;
  pane(px, py, pw, ph);
  // the list, with its search box; filtered as you type
  const lw = 256;
  g.fillStyle = C.stroke;
  g.fillRect(px + lw, py + 1, 1, ph - 2);
  const query = 'garden';
  const typed = query.slice(0, Math.floor(clamp((t - tSearch - 0.15) / 0.6) * query.length + 0.0001));
  searchBox(px + 10, py + 10, lw - 20, 34, typed, { placeholder: 'Search notes', focus: t > tSearch ? 1 : 0,
    caret: t > tSearch && Math.floor(t * 2) % 2 === 0 });
  const filt = smooth(mark(t, tSearch + 0.6, 0.35));
  const picked = t > tPick;
  NOTE_LIST.forEach(([n, s, d], i) => {
    withAlpha(i === 0 ? 1 : 1 - filt, () => {
      const y = py + 54 + i * 60;
      const x = px + 10, w = lw - 20;
      const sel = i === 0 ? picked : i === 1 && !picked;
      if (sel) fillRR(x, y, w, 58, 6, C.accentSoft);
      text(n, x + 12, y + 25, { size: 14, weight: 600 });
      text(d, x + w - 12, y + 24, { size: 11.5, family: MONO, color: C.t3, align: 'right' });
      text(s, x + 12, y + 44, { size: 12.5, color: C.t3, maxW: w - 24 });
      if (i === 0 && filt > 0) withAlpha(filt, () => {
        const pre = measure('Jobs for the ', 12.5);
        markBg(x + 12 + pre, y + 44, measure('garden', 12.5), 12.5);
        text('garden', x + 12 + pre, y + 44, { size: 12.5, color: C.t3 });
      });
    });
  });
  // the note
  const dx = px + lw + 32, dw = pw - lw - 64;
  g.save();
  rr(px + lw + 1, py + 1, pw - lw - 2, ph - 2, [0, 8, 12, 0]);
  g.clip();
  const swap = smooth(mark(t, tPick + 0.05, 0.3));
  withAlpha(1 - smooth(mark(t, tPick, 0.15)), () => { // Lake weekend, open before the search
    let y = py + 26;
    text('Travel · 22 November 2026, 09:15', dx, y + 13, { size: 12, color: C.t3 });
    text('Lake weekend', dx, y + 48, { size: 26, weight: 600, family: DISPLAY });
    const lines = wrap('Two nights by the inlet. Early walk to the ridge, then the boat back.', dw, 14, 400, TEXT);
    lines.forEach((l, i) => text(l, dx, y + 81 + i * 20.3, { size: 14, color: C.t2 }));
    y += 81 + lines.length * 20.3 + 4;
    const iw = Math.min(dw, 420);
    photoIn(0, dx, y, iw, iw * 0.7, 10);
    y += iw * 0.7 + 30;
    text('To do', dx, y, { size: 14, weight: 600 });
    [['Book the cabin', 1], ['Rain jackets', 0], ['Camera battery', 0]].forEach(([l, d], i) => chk(dx, y + 12 + i * 28, l, d));
  });
  withAlpha(swap, () => { // Garden plan
    const y = py + 26 + (1 - swap) * 8;
    text('Home · 24 November 2026, 07:15', dx, y + 13, { size: 12, color: C.t3 });
    text('Garden plan', dx, y + 48, { size: 26, weight: 600, family: DISPLAY });
    const pre = 'Jobs for the ';
    const pw0 = measure(pre, 14);
    markBg(dx + pw0, y + 81, measure('garden', 14), 14);
    text(pre, dx, y + 81, { size: 14, color: C.t2 });
    text('garden', dx + pw0, y + 81, { size: 14, color: C.t2 });
    text(' in spring, before the end of April.', dx + pw0 + measure('garden', 14), y + 81, { size: 14, color: C.t2 });
    text('To do', dx, y + 115, { size: 14, weight: 600 });
    ['Prune the apple tree', 'Buy tomato seedlings', 'Fix the fence by the shed', 'Plant beans'].forEach((it, i) => {
      const d = i < 2 ? mark(t, tTick + i * 0.25, 0.35) : 0;
      chk(dx, y + 127 + i * 28.3, it, d, i < 2 ? mark(t, tTick + i * 0.25 + 0.15, 0.3) : 0);
    });
    text('Budget', dx, y + 266, { size: 14, weight: 600 });
    // the table: a rounded card, the header on a layer, hairlines between the rows
    const tx = dx, ty = y + 278, c1 = 128, c2 = 70, rh = 33;
    const rows = [['Item', 'Price'], ['Seedlings', '12 €'], ['Fence boards', '48 €']];
    g.save();
    rr(tx, ty, c1 + c2, rh * 3, 8);
    g.clip();
    g.fillStyle = C.layer2;
    g.fillRect(tx, ty, c1 + c2, rh);
    g.fillStyle = C.stroke;
    g.fillRect(tx, ty + rh, c1 + c2, 1);
    g.fillRect(tx, ty + rh * 2, c1 + c2, 1);
    g.restore();
    strokeRR(tx + 0.5, ty + 0.5, c1 + c2 - 1, rh * 3 - 1, 8, C.stroke2);
    rows.forEach((r, i) => {
      text(r[0], tx + 14, ty + i * rh + 21, { size: 13, weight: i ? 400 : 600 });
      text(r[1], tx + c1 + 14, ty + i * rh + 21, { size: 13, weight: i ? 400 : 600 });
    });
  });
  g.restore();
  g.restore();
  const keys = [[0, 600, 480], [tSearch - 0.05, 330, 67], [tPick - 0.05, 300, 124], [tPick + 1.2, 700, 430]];
  const [cx, cy] = path(keys, t).map((v) => v * K);
  cursor(cx, cy, clickPress(t, tSearch) + clickPress(t, tPick));
  ripple(330 * K, 67 * K, (t - tSearch) / 0.45);
  ripple(300 * K, 124 * K, (t - tPick) / 0.45);
}

// --- Safe: Recently Deleted, the Recycle Bin, a conflict keeps both copies ----------------------------
function safe(sc) {
  const t = sc.local;
  const t1 = ev(sc, 0), t2 = ev(sc, 1), tOk = ev(sc, 2);
  floatShadow(0, 0, CW, CH, 12, 48, 18, 0.5);
  glass(0, 0, CW, CH, 12, WIN_CROP, [0, 0, CW, CH]);
  strokeRR(0.5, 0.5, CW - 1, CH - 1, 12, C.stroke2);
  const big = (name, x, y, color, s = 46) => ico(name, x - s / 2, y - s / 2, s, color, { lw: 1.6 * 20 / s * 1.3 });
  const lane = (y, from, to, label, file, at, fromIcon, toIcon) => {
    fillRR(20, y, CW - 40, 116, 8, C.layer);
    strokeRR(20.5, y + 0.5, CW - 41, 115, 8, C.stroke);
    fromIcon(80, y + 50);
    toIcon(CW - 110, y + 50);
    g.strokeStyle = C.stroke2;
    g.lineWidth = 2;
    g.setLineDash([4, 6]);
    g.beginPath();
    g.moveTo(140, y + 50);
    g.lineTo(CW - 170, y + 50);
    g.stroke();
    g.setLineDash([]);
    text(from, 80, y + 100, { size: 11.5, color: C.t3, align: 'center' });
    text(to, CW - 110, y + 100, { size: 11.5, color: C.ink, align: 'center', weight: 600 });
    const p = easeInOut(mark(t, at - 0.35, 0.7));
    const fx = lerp(150, CW - 240, p), fy = y + 50 - Math.sin(p * Math.PI) * 16;
    withAlpha(p > 0 && p < 1 ? 1 : 0, () => {
      const fw = measure(file, 11, 500) + 40;
      fillRR(fx, fy - 14, fw, 28, 6, '#2b3038');
      strokeRR(fx + 0.5, fy - 13.5, fw - 1, 27, 6, C.stroke2);
      fileIcon(fx + 10, fy - 7, 14, file.endsWith('.docx') ? '#2b7cd3' : C.pink);
      text(file, fx + 28, fy + 4, { size: 11, weight: 500 });
    });
    withAlpha(smooth(mark(t, at + 0.3, 0.4)), () => {
      text(label, CW / 2 - 20, y + 30, { size: 12, color: C.green, align: 'center', weight: 600 });
    });
  };
  const pcIcon = (x, y) => big('monitor', x, y, C.t2, 50);
  const phoneIcon = (x, y) => big('device', x, y, C.t2, 50);
  const binIcon = (x, y) => big('trash', x, y, C.accent, 48);
  const cloudBin = (x, y) => skyMark(x, y - 4, 64, C.accent, { lw: 2.4, aw: 1.8 });
  lane(20, 'Deleted on this PC', 'Recently Deleted in iCloud', '30 days to change your mind', 'IMG_2051.HEIC', t1,
    pcIcon, cloudBin);
  lane(146, 'Deleted on the iPhone', 'Recycle Bin on this PC', 'Restore it any time', 'Letter.docx', t2, phoneIcon, binIcon);
  // a conflict keeps both
  fillRR(20, 272, CW - 40, 168, 8, C.layer);
  strokeRR(20.5, 272.5, CW - 41, 167, 8, C.stroke);
  text('Changed on both sides?', 44, 304, { size: 14, weight: 600, family: DISPLAY });
  const a = spring(mark(t, tOk - 0.3, 0.6));
  [['Budget 2026.xlsx', 'iCloud\'s version'], ['Budget 2026 (PC conflict …).xlsx', 'this PC\'s version']].forEach(([n, who], i) => {
    const p = i ? a : 1;
    withAlpha(clamp(p * 2), () => {
      const y = 326 + i * 48 + (1 - p) * -20;
      fillRR(44, y, 384, 38, 6, C.layer2);
      strokeRR(44.5, y + 0.5, 383, 37, 6, C.stroke);
      fileIcon(56, y + 9, 20, '#21a366');
      text(n, 84, y + 24, { size: 12.5 });
      text(who, 416, y + 24, { size: 11, color: C.t3, align: 'right' });
    });
  });
  withAlpha(smooth(mark(t, tOk, 0.3)), () => {
    okBadge(472, 368, 14, mark(t, tOk, 0.4));
    text('Both copies kept', 494, 373, { size: 14, color: C.green, weight: 600 });
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
      fillRR(-w / 2, -17, w, 34, 8, C.card2);
      strokeRR(-w / 2 + 0.5, -16.5, w - 1, 33, 8, C.line2);
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
  walls(); // the wallpaper and its blur, once
  for (let i = 0; i < 48; i++) painting(i);
  window.promoReady = true;
  window.dispatchEvent(new Event('promo-ready'));
});
