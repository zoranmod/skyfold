// node --test marketing/promo-video/timeline.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import { BAR, BEAT, BPM, EVENTS, FPS, atFrame, cuts, eventFrames, scenes } from './timeline.mjs';
import { RATE, composeScore } from './music.mjs';

test('a beat is a whole number of frames', () => {
  assert.equal((60 / BPM) * FPS, BEAT);
  assert.equal(BAR, BEAT * 4);
});

test('every cut lands on a beat, and both formats have every scene', () => {
  for (const cut of Object.values(cuts)) {
    assert.equal(cut.starts.length, scenes.length);
    for (const s of cut.starts) assert.equal(s % BEAT, 0, `cut at frame ${s}`);
    assert.equal(cut.frames % BEAT, 0);
  }
});

test('the wide cut is under a minute, the tall one about half a minute', () => {
  assert.ok(cuts.wide.frames / FPS <= 60);
  assert.ok(cuts.tall.frames / FPS <= 35);
});

test('every event is inside its scene, and the scenes are found back from a frame', () => {
  for (const [id, list] of Object.entries(EVENTS)) {
    assert.ok(scenes.some((s) => s.id === id), id);
    for (const e of list) assert.ok(e.at > 0 && e.at < 1, `${id} ${e.at}`);
  }
  for (const cut of Object.values(cuts)) {
    for (const e of eventFrames(cut)) assert.equal(atFrame(cut, e.frame).id, e.scene);
    assert.equal(atFrame(cut, 0).id, 'hook');
    assert.equal(atFrame(cut, cut.frames - 1).id, 'end');
  }
});

test('the music is exactly as long as the video, never clips and is the same every time', () => {
  const cut = cuts.tall;
  const [l, r] = composeScore(cut);
  assert.equal(l.length, Math.round((cut.frames / FPS) * RATE));
  let peak = 0;
  for (let i = 0; i < l.length; i++) peak = Math.max(peak, Math.abs(l[i]), Math.abs(r[i]));
  assert.ok(peak < 1 && peak > 0.2, `peak ${peak}`);
  const [l2] = composeScore(cut);
  assert.equal(l2[123456], l[123456]);
});
