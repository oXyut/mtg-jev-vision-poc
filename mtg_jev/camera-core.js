// Pure geometry and scheduling. No camera, storage, network or credentials here.
export function fitRect(width, height, sourceWidth, sourceHeight) {
  if (Math.min(width, height, sourceWidth, sourceHeight) <= 0) return null;
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const w = sourceWidth * scale, h = sourceHeight * scale;
  return {x: (width - w) / 2, y: (height - h) / 2, width: w, height: h};
}

export function projectBox(box, rect, mirror = false) {
  return {x: rect.x + (mirror ? 1 - box.right : box.left) * rect.width,
    y: rect.y + box.top * rect.height,
    width: (box.right - box.left) * rect.width, height: (box.bottom - box.top) * rect.height};
}

export function freshOverlay(capturedAt, now, ttl, busy = false) {
  const age = now - capturedAt;
  return !busy && age >= 0 && age <= ttl;
}

export class FrameLoop {
  constructor(task, {interval = () => 3000, onResult = () => {}, onError = () => {},
    onBusy = () => {}, schedule = (fn, ms) => setTimeout(fn, ms), unschedule = id => clearTimeout(id)} = {}) {
    Object.assign(this, {task, interval, onResult, onError, onBusy, schedule, unschedule});
    this.running = false; this.busy = false; this.epoch = 0; this.timer = null; this.controller = null;
  }
  start() {
    if (this.running || this.busy) return false;
    this.running = true; const epoch = ++this.epoch;
    this.tick(epoch); return true;
  }
  stop() {
    this.running = false; ++this.epoch;
    if (this.timer !== null) this.unschedule(this.timer);
    this.timer = null;
    if (this.controller) this.controller.abort();
  }
  async tick(epoch) {
    if (!this.running || this.busy || epoch !== this.epoch) return;
    const current = () => this.running && epoch === this.epoch;
    const controller = new AbortController(); this.controller = controller;
    this.busy = true; this.onBusy(true);
    try {
      const value = await this.task(controller.signal, current);
      if (current()) this.onResult(value);
    } catch (error) {
      if (current() && error.name !== 'AbortError') {
        this.running = false; this.onError(error);
      }
    } finally {
      this.busy = false;
      if (this.controller === controller) this.controller = null;
      this.onBusy(false);
      if (current()) this.timer = this.schedule(() => {this.timer = null; this.tick(epoch);}, this.interval());
    }
  }
}
