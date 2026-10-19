import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../mtg_jev/camera-core.js', import.meta.url), 'utf8');
const {FrameLoop, fitRect, projectBox, freshOverlay} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const settle = () => new Promise(resolve => setImmediate(resolve));

test('letterbox and mirrored normalized coordinates stay aligned', () => {
  const rect = fitRect(1000, 600, 640, 480);
  assert.deepEqual(rect, {x:100, y:0, width:800, height:600});
  const box = {left:.1, top:.2, right:.3, bottom:.8};
  assert.equal(projectBox(box, rect).x, 180);
  assert.equal(projectBox(box, rect, true).x, 660);
});
test('stale, negative-age and in-flight overlays never display', () => {
  assert.equal(freshOverlay(100, 1000, 1500), true);
  assert.equal(freshOverlay(100, 1700, 1500), false);
  assert.equal(freshOverlay(100, 99, 1500), false);
  assert.equal(freshOverlay(100, 1000, 1500, true), false);
});
test('one pending task, no queue, late stopped result ignored', async () => {
  let resolve, calls=0, results=0, timers=0;
  const loop = new FrameLoop(() => {calls++; return new Promise(r=>resolve=r);}, {
    onResult:()=>results++, schedule:()=>++timers});
  assert.equal(loop.start(), true); assert.equal(loop.start(), false);
  assert.equal(calls, 1); loop.stop();
  assert.equal(loop.start(), false); resolve('old frame'); await settle();
  assert.equal(results, 0); assert.equal(timers, 0); assert.equal(loop.busy, false);
});
test('timer begins after completion and stop releases it', async () => {
  let resolve, timer=null, removed=null;
  const loop = new FrameLoop(()=>new Promise(r=>resolve=r), {
    interval:()=>3000, schedule:(fn,delay)=>{timer={fn,delay}; return 7;}, unschedule:id=>removed=id});
  loop.start(); assert.equal(timer, null); resolve('frame'); await settle();
  assert.equal(timer.delay,3000); loop.stop(); assert.equal(removed,7); assert.equal(loop.timer,null);
});
test('provider failure pauses the loop without automatic retries', async () => {
  let calls=0, errors=0, timers=0;
  const loop = new FrameLoop(async()=>{calls++;throw Error('stub provider failure');}, {
    onError:()=>errors++, schedule:()=>++timers});
  loop.start(); await settle();
  assert.equal(calls,1); assert.equal(errors,1); assert.equal(timers,0); assert.equal(loop.running,false);
});
