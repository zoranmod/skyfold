// The music of the promo video, made entirely in code (no samples, nothing to license).
// 120 BPM in D major, I–V–vi–IV. Calm pads in the opening; the beat comes in at the first cut;
// a click, a pop or a chime lands exactly on each EVENT of the timeline. Seeded, so every run
// gives the same file.

import { writeFileSync } from 'node:fs';
import { BAR, BEAT, FPS, eventFrames, rng } from './timeline.mjs';

export const RATE = 44100;
const SPB = (BEAT / FPS) * RATE; // samples per beat (22050)
const TAU = Math.PI * 2;
const midi = (n) => 440 * 2 ** ((n - 69) / 12);
// D, A, Bm, G (root, then the chord as MIDI notes)
const CHORDS = [[50, [62, 66, 69]], [45, [61, 64, 69]], [47, [62, 66, 71]], [43, [62, 67, 71]]];

class SVF { // a state-variable low-pass filter (TPT form): stable while its frequency moves
  constructor() { this.a = 0; this.b = 0; }
  low(x, fc, q = 0.7) {
    const gg = Math.tan((Math.PI * Math.min(fc, RATE * 0.45)) / RATE), k = 1 / q;
    const a1 = 1 / (1 + gg * (gg + k)), a2 = gg * a1, a3 = gg * a2;
    const v3 = x - this.b, v1 = a1 * this.a + a2 * v3, v2 = this.b + a2 * this.a + a3 * v3;
    this.a = 2 * v1 - this.a;
    this.b = 2 * v2 - this.b;
    return v2;
  }
  band(x, fc, q = 1) {
    const gg = Math.tan((Math.PI * Math.min(fc, RATE * 0.45)) / RATE), k = 1 / q;
    const a1 = 1 / (1 + gg * (gg + k)), a2 = gg * a1, a3 = gg * a2;
    const v3 = x - this.b, v1 = a1 * this.a + a2 * v3, v2 = this.b + a2 * this.a + a3 * v3;
    this.a = 2 * v1 - this.a;
    this.b = 2 * v2 - this.b;
    return v1;
  }
}
function blep(t, dt) {
  if (t < dt) { t /= dt; return t + t - t * t - 1; }
  if (t > 1 - dt) { t = (t - 1) / dt; return t * t + t + t + 1; }
  return 0;
}

class Reverb { // Freeverb-style: eight combs and four all-passes per side
  constructor(seedOffset) {
    const combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617], aps = [556, 441, 341, 225];
    this.c = combs.map((n) => ({ buf: new Float32Array(n + seedOffset), i: 0, f: 0 }));
    this.p = aps.map((n) => ({ buf: new Float32Array(n + seedOffset), i: 0 }));
  }
  run(x, room = 0.84, damp = 0.25) {
    let out = 0;
    for (const c of this.c) {
      const y = c.buf[c.i];
      c.f = y * (1 - damp) + c.f * damp;
      c.buf[c.i] = x + c.f * room;
      c.i = (c.i + 1) % c.buf.length;
      out += y;
    }
    for (const p of this.p) {
      const b = p.buf[p.i];
      p.buf[p.i] = out + b * 0.5;
      p.i = (p.i + 1) % p.buf.length;
      out = b - out;
    }
    return out * 0.05;
  }
}

// One cut of the video -> stereo Float32 arrays of exactly its length.
export function composeScore(cut) {
  const n = Math.round((cut.frames / FPS) * RATE);
  const L = new Float32Array(n), R = new Float32Array(n);
  const sendL = new Float32Array(n), sendR = new Float32Array(n);
  const duck = new Float32Array(n).fill(1);
  const rand = rng(2026);
  const sceneAt = (s) => {
    const f = (s / RATE) * FPS;
    let i = 0;
    cut.starts.forEach((st, k) => { if (st <= f) i = k; });
    return i;
  };
  const beatSample = (b) => Math.round(b * SPB);
  const totalBeats = Math.floor(n / SPB);
  const drumsFrom = cut.starts[1] / BEAT; // the first cut
  const hatsFrom = cut.starts[2] / BEAT;
  const endFrom = cut.starts[cut.starts.length - 1] / BEAT;
  const add = (buf, i, v) => { if (i >= 0 && i < n) buf[i] += v; };

  // kick (and its duck on the pads)
  for (let b = drumsFrom; b < endFrom; b++) {
    const s0 = beatSample(b);
    let ph = 0;
    for (let i = 0; i < 0.32 * RATE; i++) {
      const tt = i / RATE;
      const f = 45 + 110 * Math.exp(-tt * 28);
      ph += (TAU * f) / RATE;
      const v = Math.sin(ph) * Math.exp(-tt * 9) * 0.55 + (i < 40 ? (rand() - 0.5) * 0.15 : 0);
      add(L, s0 + i, v);
      add(R, s0 + i, v);
      if (s0 + i < n) duck[s0 + i] = Math.min(duck[s0 + i], 1 - 0.55 * Math.exp(-tt * 7));
    }
  }
  // snare on 2 and 4, hats on the off-beats
  const snF = new SVF(), hatF = new SVF();
  for (let b = hatsFrom; b < endFrom; b++) {
    if (b % 2 === 1) {
      const s0 = beatSample(b);
      for (let i = 0; i < 0.22 * RATE; i++) {
        const tt = i / RATE;
        const v = snF.band(rand() * 2 - 1, 1900, 0.8) * Math.exp(-tt * 18) * 0.5 + Math.sin(TAU * 185 * tt) * Math.exp(-tt * 30) * 0.18;
        add(L, s0 + i, v * 0.95);
        add(R, s0 + i, v);
        add(sendL, s0 + i, v * 0.25);
        add(sendR, s0 + i, v * 0.25);
      }
    }
    for (const off of [0.5]) {
      const s0 = beatSample(b + off);
      for (let i = 0; i < 0.06 * RATE; i++) {
        const x = rand() * 2 - 1;
        const v = (x - hatF.low(x, 7000)) * Math.exp(-(i / RATE) * 60) * 0.16;
        add(L, s0 + i, v * 0.8);
        add(R, s0 + i, v);
      }
    }
  }

  // pads: detuned saws per chord tone, through a slowly opening filter
  const padF = [new SVF(), new SVF()];
  const phases = Array.from({ length: 12 }, () => rand());
  for (let i = 0; i < n; i++) {
    const beat = i / SPB;
    const bar = Math.floor(beat / 4);
    const [, chord] = CHORDS[bar % 4];
    const final = beat >= endFrom;
    const notes = final ? [62, 66, 69] : chord;
    const inBar = (beat % 4) / 4;
    const env = Math.min(1, inBar * 12) * (final ? Math.exp(-(beat - endFrom) * 0.12) : 1);
    let l = 0, r = 0;
    notes.forEach((m, k) => {
      for (let d = 0; d < 3; d++) {
        const idx = k * 3 + d;
        const f = midi(m) * (1 + (d - 1) * 0.0035);
        const dt = f / RATE;
        phases[idx] = (phases[idx] + dt) % 1;
        const saw = 2 * phases[idx] - 1 - blep(phases[idx], dt);
        if (d === 0) l += saw; else if (d === 2) r += saw; else { l += saw * 0.5; r += saw * 0.5; }
      }
    });
    const intro = beat < drumsFrom ? 0.35 + 0.65 * (beat / drumsFrom) : 1;
    const fc = 500 + 1600 * intro + 500 * Math.sin((beat / 16) * TAU);
    const vol = 0.055 * env * duck[i];
    l = padF[0].low(l, fc) * vol;
    r = padF[1].low(r, fc) * vol;
    L[i] += l;
    R[i] += r;
    sendL[i] += l * 0.9;
    sendR[i] += r * 0.9;
  }

  // bass: the root, in eighths, once the beat is in
  let bph = 0;
  const bassF = new SVF();
  for (let i = beatSample(drumsFrom); i < n; i++) {
    const beat = i / SPB;
    const [root] = CHORDS[Math.floor(beat / 4) % 4];
    const final = beat >= endFrom;
    const f = midi(final ? 50 : root);
    bph = (bph + f / RATE) % 1;
    const eighth = (beat * 2) % 1;
    const env = final ? Math.exp(-(beat - endFrom) * 1.2) : Math.exp(-eighth * 4) * 0.7 + 0.3;
    const raw = Math.sin(TAU * bph) * 0.8 + (2 * bph - 1) * 0.2;
    const v = bassF.low(raw, 420) * env * 0.32 * (0.6 + 0.4 * duck[i]);
    L[i] += v;
    R[i] += v;
  }

  // arpeggio: a soft FM pluck over the chord, from the third scene, with a ping-pong echo
  const arpFrom = hatsFrom;
  const pattern = [0, 1, 2, 3, 2, 1, 0, 2];
  for (let s = arpFrom * 2; s < endFrom * 2; s++) { // eighths
    const beat = s / 2;
    const [, chord] = CHORDS[Math.floor(beat / 4) % 4];
    const tones = [...chord, chord[0] + 12];
    const m = tones[pattern[s % 8]] + 12;
    const f = midi(m);
    const s0 = beatSample(beat);
    const len = 0.5 * RATE;
    const pan = s % 2 ? 0.7 : 0.3;
    for (let i = 0; i < len; i++) {
      const tt = i / RATE;
      const mod = Math.sin(TAU * f * 2 * tt) * 1.2 * Math.exp(-tt * 10);
      const v = Math.sin(TAU * f * tt + mod) * Math.exp(-tt * 7) * 0.075;
      add(L, s0 + i, v * (1 - pan));
      add(R, s0 + i, v * pan);
      add(sendL, s0 + i, v * 0.6);
      add(sendR, s0 + i, v * 0.6);
      // the echo, three eighths later, on the other side
      const e = s0 + i + Math.round(1.5 * SPB);
      add(L, e, v * pan * 0.35);
      add(R, e, v * (1 - pan) * 0.35);
    }
  }

  // sound effects on the timeline's events
  const fx = (kind, s0) => {
    const tone = (f, at, dur, vol, decay) => {
      const a = s0 + Math.round(at * RATE);
      for (let i = 0; i < dur * RATE; i++) {
        const tt = i / RATE;
        const v = Math.sin(TAU * f * tt) * Math.exp(-tt * decay) * vol * Math.min(1, i / 60);
        add(L, a + i, v);
        add(R, a + i, v);
        add(sendL, a + i, v * 0.8);
        add(sendR, a + i, v * 0.8);
      }
    };
    if (kind === 'click') {
      for (let i = 0; i < 0.03 * RATE; i++) {
        const v = (rand() * 2 - 1) * Math.exp(-(i / RATE) * 300) * 0.3 + Math.sin(TAU * 2400 * (i / RATE)) * Math.exp(-(i / RATE) * 160) * 0.2;
        add(L, s0 + i, v);
        add(R, s0 + i, v);
      }
    } else if (kind === 'pop') {
      let ph = 0;
      for (let i = 0; i < 0.12 * RATE; i++) {
        const tt = i / RATE;
        ph += (TAU * (700 + 900 * Math.min(1, tt * 25))) / RATE;
        const v = Math.sin(ph) * Math.exp(-tt * 30) * 0.13;
        add(L, s0 + i, v);
        add(R, s0 + i, v);
        add(sendL, s0 + i, v);
        add(sendR, s0 + i, v);
      }
    } else if (kind === 'ok') {
      tone(midi(81), 0, 0.9, 0.09, 6);
      tone(midi(88), 0.09, 1.1, 0.08, 5);
    } else if (kind === 'heart') {
      tone(midi(78), 0, 0.6, 0.07, 7);
      tone(midi(81), 0.07, 0.6, 0.07, 7);
      tone(midi(86), 0.14, 1.0, 0.08, 5);
    } else if (kind === 'done') {
      [74, 78, 81, 86].forEach((m, k) => tone(midi(m), k * 0.03, 2.4, 0.05, 1.8));
    } else if (kind === 'whoosh') {
      const f = new SVF();
      const len = 0.9 * RATE, start = s0 - Math.round(0.6 * RATE);
      for (let i = 0; i < len; i++) {
        const q = i / len;
        const v = f.band(rand() * 2 - 1, 300 + 3500 * q * q, 1.2) * Math.sin(Math.PI * q) * 0.16;
        add(L, start + i, v * (1 - q * 0.6));
        add(R, start + i, v * (0.4 + q * 0.6));
        add(sendL, start + i, v * 0.5);
        add(sendR, start + i, v * 0.5);
      }
    }
  };
  for (const e of eventFrames(cut)) fx(e.kind, Math.round((e.frame / FPS) * RATE));

  // reverb, then a gentle fade in and out
  const revL = new Reverb(0), revR = new Reverb(23);
  for (let i = 0; i < n; i++) {
    L[i] += revL.run(sendL[i]);
    R[i] += revR.run(sendR[i]);
    const tt = i / RATE, end = n / RATE;
    const fade = Math.min(1, tt / 0.4) * Math.min(1, (end - tt) / 1.2);
    L[i] = Math.tanh(L[i] * 1.1 * fade);
    R[i] = Math.tanh(R[i] * 1.1 * fade);
  }
  void totalBeats;
  void BAR;
  return [L, R];
}

export function writeScore(file, cut) {
  const [L, R] = composeScore(cut);
  const n = L.length;
  const buf = Buffer.alloc(44 + n * 4);
  buf.write('RIFF', 0);
  buf.writeUInt32LE(36 + n * 4, 4);
  buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(2, 22);
  buf.writeUInt32LE(RATE, 24);
  buf.writeUInt32LE(RATE * 4, 28);
  buf.writeUInt16LE(4, 32);
  buf.writeUInt16LE(16, 34);
  buf.write('data', 36);
  buf.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) {
    buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, L[i])) * 32767), 44 + i * 4);
    buf.writeInt16LE(Math.round(Math.max(-1, Math.min(1, R[i])) * 32767), 46 + i * 4);
  }
  writeFileSync(file, buf);
  return n / RATE;
}
