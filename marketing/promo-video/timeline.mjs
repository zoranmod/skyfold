// The timeline of the Skyfold promo video: scenes, cuts in musical bars, click moments, easing.
// Everything here is data or a pure function, so the drawing, the music and the tests all read
// the same numbers.

export const FPS = 30;
export const BPM = 120; // 60 / 120 s = 0.5 s = exactly 15 frames at 30 fps
export const BEAT = 15; // frames per beat
export const BAR = BEAT * 4; // 60 frames (2 s) per bar

export const scenes = [
  { id: 'hook' },
  { id: 'tray', phase: 'Tray', title: 'A small app in your tray.',
    sub: 'Sign in with your Apple ID. Skyfold keeps everything in sync in the background.' },
  { id: 'photos', phase: 'Photos', title: 'Every photo, in a real folder.',
    sub: 'By year and month, albums as folders. Delete on one side and it goes on the other.' },
  { id: 'gallery', phase: 'Gallery', title: 'Browse them like on an iPad.', isNew: true,
    sub: 'Years, months and albums. A heart here is a heart on your iPhone too.' },
  { id: 'drive', phase: 'iCloud Drive', title: 'iCloud Drive, both ways.',
    sub: 'Changes on the PC are noticed within seconds. Renames and moves follow.' },
  { id: 'notes', phase: 'Notes', title: 'Your Apple Notes, ready to read and search.',
    sub: 'Checklists, tables, pictures and scans, on your PC.' },
  { id: 'safe', phase: 'Safe', title: 'Built to be safe.',
    sub: 'Deleted files go to the Recycle Bin or to Recently Deleted. Conflicts keep both copies.' },
  { id: 'end' },
];

export const PHASES = scenes.filter((s) => s.phase);

function bars(...lengths) {
  const starts = [];
  let at = 0;
  for (const length of lengths) {
    starts.push(Math.round(at * BAR));
    at += length;
  }
  return { starts, frames: Math.round(at * BAR) };
}

//                                       hook tray photos gallery drive notes safe end
export const cuts = {
  wide: { width: 1920, height: 1080, ...bars(3, 2.5, 3, 4, 3, 3, 2.5, 3) }, // 24 bars, 48 s
  tall: { width: 1080, height: 1920, ...bars(2, 1.5, 2, 3, 2, 2, 1.5, 2) }, // 16 bars, 32 s
};

// When the cursor clicks, a check mark appears or something lands, as a fraction of the scene.
// The drawing and the sound effects both read from here, so they always match.
export const EVENTS = {
  hook: [{ at: 0.52, kind: 'whoosh' }, { at: 0.78, kind: 'done' }],
  tray: [{ at: 0.26, kind: 'click' }, { at: 0.62, kind: 'ok' }],
  photos: [{ at: 0.2, kind: 'pop' }, { at: 0.3, kind: 'pop' }, { at: 0.4, kind: 'pop' }, { at: 0.5, kind: 'pop' },
    { at: 0.7, kind: 'ok' }],
  gallery: [{ at: 0.38, kind: 'click' }, { at: 0.64, kind: 'click' }, { at: 0.65, kind: 'heart' }],
  drive: [{ at: 0.2, kind: 'click' }, { at: 0.62, kind: 'ok' }],
  notes: [{ at: 0.14, kind: 'click' }, { at: 0.5, kind: 'click' }, { at: 0.64, kind: 'ok' }],
  safe: [{ at: 0.3, kind: 'pop' }, { at: 0.55, kind: 'pop' }, { at: 0.75, kind: 'ok' }],
  end: [{ at: 0.08, kind: 'done' }],
};

export const OUTRO = 0.45; // seconds a card takes to fly into its dot on the rail

export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, x) => a + (b - a) * x;
export const smooth = (x) => clamp(x) ** 2 * (3 - 2 * clamp(x));
export const easeOut = (x) => 1 - (1 - clamp(x)) ** 3;
export const easeInOut = (x) => (clamp(x) < 0.5 ? 4 * clamp(x) ** 3 : 1 - (-2 * clamp(x) + 2) ** 3 / 2);
export const spring = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : 1 - Math.exp(-7 * x) * Math.cos(10 * x));
// 0 before start, 1 after start + length, eased in between
export const mark = (x, start, length) => clamp((x - start) / length);

export function atFrame(cut, frame) {
  let index = 0;
  for (let i = 0; i < cut.starts.length; i++) if (cut.starts[i] <= frame) index = i;
  const start = cut.starts[index];
  const end = cut.starts[index + 1] ?? cut.frames;
  return {
    index, ...scenes[index], start, end, frame,
    local: (frame - start) / FPS,
    length: (end - start) / FPS,
    progress: (frame - start) / (end - start),
    remaining: (end - frame) / FPS,
  };
}

// Frame (in the whole video) of every event, for the sound effects.
export function eventFrames(cut) {
  const out = [];
  scenes.forEach((s, i) => {
    const start = cut.starts[i];
    const end = cut.starts[i + 1] ?? cut.frames;
    for (const e of EVENTS[s.id] ?? []) out.push({ frame: Math.round(start + e.at * (end - start)), kind: e.kind, scene: s.id });
  });
  return out.sort((a, b) => a.frame - b.frame);
}

// Small seeded random numbers: the same picture on every run.
export function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
}
