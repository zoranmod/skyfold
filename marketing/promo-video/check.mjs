// Checks the rendered videos: they decode without an error, have the right size and length,
// start playing before they are fully downloaded (faststart) and stay under 15 MB.
//   node check.mjs

import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FPS, cuts } from './timeline.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = process.env.PROMO_OUTPUT || join(HERE, 'out');
const LOCAL = process.env.LOCALAPPDATA || '';
function tool(name) {
  const env = process.env[`PROMO_${name.toUpperCase()}`];
  if (env) return env;
  const base = join(LOCAL, 'Microsoft', 'WinGet', 'Packages');
  const dir = readdirSync(base).find((d) => d.startsWith('Gyan.FFmpeg'));
  const inner = readdirSync(join(base, dir)).find((d) => d.startsWith('ffmpeg-'));
  return join(base, dir, inner, 'bin', `${name}.exe`);
}

let bad = 0;
for (const [format, name] of [['wide', 'skyfold-16x9'], ['tall', 'skyfold-9x16']]) {
  for (const file of [`${name}.mp4`, `${name}-silent.mp4`]) {
    const path = join(OUT, file);
    if (!existsSync(path)) continue;
    const problems = [];
    const size = statSync(path).size;
    if (size >= 15 * 1024 * 1024) problems.push(`${(size / 1048576).toFixed(1)} MB`);
    const head = readFileSync(path).subarray(0, 64 * 1024).toString('latin1');
    if (head.indexOf('moov') < 0 || head.indexOf('moov') > head.indexOf('mdat')) problems.push('no faststart');
    const probe = spawnSync(tool('ffprobe'), ['-v', 'error', '-show_entries', 'stream=codec_type,width,height,duration',
      '-of', 'json', path], { encoding: 'utf8' });
    const streams = JSON.parse(probe.stdout || '{"streams":[]}').streams;
    const v = streams.find((s) => s.codec_type === 'video');
    const cut = cuts[format];
    if (!v || v.width !== cut.width || v.height !== cut.height) problems.push('wrong size');
    if (v && Math.abs(Number(v.duration) - cut.frames / FPS) > 0.1) problems.push(`length ${v.duration}`);
    if (!file.includes('silent') && !streams.some((s) => s.codec_type === 'audio')) problems.push('no sound');
    const dec = spawnSync(tool('ffmpeg'), ['-v', 'error', '-xerror', '-i', path, '-f', 'null', '-'], { encoding: 'utf8' });
    if (dec.status !== 0 || dec.stderr.trim()) problems.push(`decode: ${dec.stderr.trim().slice(0, 200)}`);
    console.log(`${problems.length ? 'BAD ' : 'ok  '} ${file}  ${(size / 1048576).toFixed(1)} MB  ${v?.width}x${v?.height}  ${Number(v?.duration).toFixed(1)} s${problems.length ? '  ' + problems.join(', ') : ''}`);
    bad += problems.length ? 1 : 0;
  }
}
process.exit(bad ? 1 : 0);
