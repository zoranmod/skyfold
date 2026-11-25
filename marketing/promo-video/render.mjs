// Renders the promo video: a tiny local web server, a headless Chromium driven over the DevTools
// protocol (no npm packages), each frame drawn by draw(t) and piped into FFmpeg, then the music.
//
//   node render.mjs --preview   a local URL with play, seek and 16:9 / 9:16
//   node render.mjs --frames    only the control pictures (quick check)
//   node render.mjs             both formats, silent and with music
//   options: --wide or --tall (one format), --no-music, --frames --at=120,450 (just those frames)
//   env: PROMO_CHROMIUM, PROMO_FFMPEG, PROMO_OUTPUT

import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FPS, cuts, scenes, EVENTS } from './timeline.mjs';
import { writeScore } from './music.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');
const OUT = process.env.PROMO_OUTPUT || join(HERE, 'out');
const args = new Set(process.argv.slice(2));
const MAX_BYTES = 15 * 1024 * 1024;

// --- the files the page may load, and nothing else --------------------------------------------------
const FILES = {
  '/index.html': [join(HERE, 'index.html'), 'text/html; charset=utf-8'],
  '/draw.mjs': [join(HERE, 'draw.mjs'), 'text/javascript; charset=utf-8'],
  '/timeline.mjs': [join(HERE, 'timeline.mjs'), 'text/javascript; charset=utf-8'],
  '/icon.png': [join(ROOT, 'site', 'assets', 'icon-512.png'), 'image/png'],
};
function serve() {
  return new Promise((resolve) => {
    const server = createServer((req, res) => {
      const path = new URL(req.url, 'http://x').pathname;
      const f = FILES[path === '/' ? '/index.html' : path];
      if (!f) {
        res.writeHead(404).end();
        return;
      }
      res.writeHead(200, { 'content-type': f[1], 'cache-control': 'no-store' });
      res.end(readFileSync(f[0]));
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function findTool(env, guesses) {
  if (process.env[env]) return process.env[env];
  for (const g of guesses) {
    try {
      const hit = g();
      if (hit && existsSync(hit)) return hit;
    } catch { /* next */ }
  }
  throw new Error(`Couldn't find it: set ${env}`);
}
const LOCAL = process.env.LOCALAPPDATA || '';
const chromium = () => findTool('PROMO_CHROMIUM', [
  () => {
    const base = join(LOCAL, 'ms-playwright');
    const dir = readdirSync(base).filter((d) => d.startsWith('chromium_headless_shell-')).sort().pop();
    return join(base, dir, 'chrome-headless-shell-win64', 'chrome-headless-shell.exe');
  },
]);
const ffmpeg = () => findTool('PROMO_FFMPEG', [
  () => {
    const base = join(LOCAL, 'Microsoft', 'WinGet', 'Packages');
    const dir = readdirSync(base).find((d) => d.startsWith('Gyan.FFmpeg'));
    const inner = readdirSync(join(base, dir)).find((d) => d.startsWith('ffmpeg-'));
    return join(base, dir, inner, 'bin', 'ffmpeg.exe');
  },
]);

// --- a minimal DevTools protocol client --------------------------------------------------------------
async function launch(width, height) {
  const profile = mkdtempSync(join(tmpdir(), 'skyfold-promo-'));
  const proc = spawn(chromium(), [
    '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
    '--force-device-scale-factor=1', '--hide-scrollbars', '--mute-audio', '--no-first-run',
    '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-extensions',
    '--disable-features=Translate,OptimizationHints,MediaRouter', 'about:blank',
  ], { stdio: 'ignore' });
  let port;
  for (let i = 0; i < 100 && !port; i++) {
    await new Promise((r) => setTimeout(r, 100));
    const f = join(profile, 'DevToolsActivePort');
    if (existsSync(f)) port = readFileSync(f, 'utf8').split('\n')[0].trim();
  }
  if (!port) throw new Error('Chromium did not start');
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = list.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0;
  const waiting = new Map();
  const listeners = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && waiting.has(msg.id)) {
      const [res, rej] = waiting.get(msg.id);
      waiting.delete(msg.id);
      if (msg.error) rej(new Error(msg.error.message)); else res(msg.result);
    } else if (msg.method) listeners.forEach((fn) => fn(msg));
  };
  const send = (method, params = {}) => new Promise((res, rej) => {
    waiting.set(++id, [res, rej]);
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const close = () => {
    try { ws.close(); } catch { /* gone */ }
    proc.kill();
    setTimeout(() => { try { rmSync(profile, { recursive: true, force: true }); } catch { /* in use */ } }, 800);
  };
  return { send, evaluate, close, on: (fn) => listeners.push(fn) };
}

async function openPage(format, origin) {
  const cut = cuts[format];
  const b = await launch(cut.width, cut.height);
  const outside = [];
  b.on((m) => {
    if (m.method === 'Network.requestWillBeSent') {
      const u = m.params.request.url;
      if (!u.startsWith(origin) && !u.startsWith('data:') && !u.startsWith('about:')) outside.push(u);
    }
  });
  await b.send('Network.enable');
  await b.send('Emulation.setDeviceMetricsOverride', { width: cut.width, height: cut.height, deviceScaleFactor: 1, mobile: false });
  await b.send('Page.enable');
  await b.send('Page.navigate', { url: `${origin}/index.html?export&format=${format}` });
  for (let i = 0; i < 100; i++) {
    if (await b.evaluate('window.promoReady === true').catch(() => false)) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!(await b.evaluate('window.promoReady === true'))) throw new Error('the page never got ready');
  const fonts = await b.evaluate('window.fontsOk()');
  if (!fonts.display || !fonts.text) throw new Error(`Segoe UI Variable is missing: ${JSON.stringify(fonts)}`);
  if (!fonts.mono) console.warn('  (Cascadia Mono is missing: Consolas is used for the address)');
  return { b, cut, outside };
}

async function controlFrames(format, origin) {
  const { b, cut, outside } = await openPage(format, origin);
  const dir = join(OUT, `frames-${format}`);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const picks = [];
  const only = [...args].find((a) => a.startsWith('--at='));
  if (only) only.slice(5).split(',').forEach((f) => picks.push(['at', Number(f)]));
  else scenes.forEach((s, i) => {
    const start = cut.starts[i], end = cut.starts[i + 1] ?? cut.frames;
    const at = [0.5, 0.92, ...(EVENTS[s.id] ?? []).map((e) => Math.min(0.97, e.at + 0.06))];
    [...new Set(at.map((a) => Math.round(start + a * (end - start))))].forEach((f) => picks.push([s.id, f]));
  });
  for (const [id, f] of picks) {
    const png = await b.evaluate(`(draw(${f} / ${FPS}), document.querySelector('canvas').toDataURL('image/png').split(',')[1])`);
    writeFileSync(join(dir, `${String(f).padStart(4, '0')}-${id}.png`), Buffer.from(png, 'base64'));
  }
  // the same frame after seeking elsewhere must be the same picture
  const mid = picks[Math.floor(picks.length / 2)][1];
  const grab = `document.querySelector('canvas').toDataURL('image/png')`;
  const a = await b.evaluate(`(draw(${mid} / ${FPS}), ${grab})`);
  await b.evaluate(`draw(${mid + 47} / ${FPS})`);
  const c = await b.evaluate(`(draw(${mid} / ${FPS}), ${grab})`);
  b.close();
  if (a !== c) throw new Error('seeking back gave another picture: draw(t) keeps state');
  if (outside.length) throw new Error(`the page asked for something outside: ${outside[0]}`);
  console.log(`  ${picks.length} control frames in ${dir}`);
}

async function renderVideo(format, origin, music) {
  const { b, cut, outside } = await openPage(format, origin);
  mkdirSync(OUT, { recursive: true });
  const name = format === 'wide' ? 'skyfold-16x9' : 'skyfold-9x16';
  const silent = join(OUT, `${name}-silent.mp4`);
  const ff = spawn(ffmpeg(), [
    '-y', '-loglevel', 'error', '-framerate', String(FPS), '-f', 'image2pipe', '-c:v', 'mjpeg', '-i', 'pipe:0', '-an',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-maxrate', '2400k', '-bufsize', '3600k',
    '-vf', 'scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p',
    '-pix_fmt', 'yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    '-profile:v', 'high', '-movflags', '+faststart', silent,
  ], { stdio: ['pipe', 'inherit', 'inherit'] });
  let dead = null;
  ff.on('exit', (code) => { dead = code; });
  const write = (buf) => new Promise((res, rej) => {
    if (dead !== null) return rej(new Error(`FFmpeg stopped (${dead})`));
    if (ff.stdin.write(buf)) res(); else ff.stdin.once('drain', res);
  });
  const t0 = Date.now();
  for (let f = 0; f < cut.frames; f += 6) {
    const last = Math.min(cut.frames, f + 6);
    const batch = await b.evaluate(`(() => { const c = document.querySelector('canvas'), out = [];
      for (let n = ${f}; n < ${last}; n++) { draw(n / ${FPS}); out.push(c.toDataURL('image/jpeg', 0.94).split(',')[1]); }
      return out; })()`);
    for (const jpg of batch) await write(Buffer.from(jpg, 'base64'));
    if (f % 150 === 0) process.stdout.write(`\r  ${format}: frame ${f} of ${cut.frames}`);
  }
  ff.stdin.end();
  await new Promise((r) => (dead !== null ? r() : ff.on('exit', r)));
  b.close();
  if (outside.length) throw new Error(`the page asked for something outside: ${outside[0]}`);
  console.log(`\r  ${format}: ${cut.frames} frames in ${((Date.now() - t0) / 1000).toFixed(0)} s -> ${silent}`);
  const out = [silent];
  if (music) {
    const wav = join(OUT, `${name}.wav`);
    writeScore(wav, cut);
    const withMusic = join(OUT, `${name}.mp4`);
    await new Promise((res, rej) => {
      const p = spawn(ffmpeg(), ['-y', '-loglevel', 'error', '-i', silent, '-i', wav, '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'copy', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', '44100', '-c:a', 'aac', '-b:a', '160k',
        '-shortest', '-movflags', '+faststart', withMusic], { stdio: 'inherit' });
      p.on('exit', (c) => (c === 0 ? res() : rej(new Error(`the music mix failed (${c})`))));
    });
    out.push(withMusic);
  }
  for (const f of out) {
    const size = statSync(f).size;
    if (size >= MAX_BYTES) throw new Error(`${f} is ${(size / 1048576).toFixed(1)} MB (limit 15)`);
    console.log(`  ${f}: ${(size / 1048576).toFixed(1)} MB`);
  }
}

const server = await serve();
const origin = `http://127.0.0.1:${server.address().port}`;
try {
  if (args.has('--preview')) {
    console.log(`Preview: ${origin}/index.html   (Ctrl+C to stop)`);
    await new Promise(() => {});
  }
  const formats = args.has('--wide') ? ['wide'] : args.has('--tall') ? ['tall'] : ['wide', 'tall'];
  for (const f of formats) {
    if (args.has('--frames')) await controlFrames(f, origin);
    else {
      await controlFrames(f, origin);
      await renderVideo(f, origin, !args.has('--no-music'));
    }
  }
} finally {
  server.close();
}
