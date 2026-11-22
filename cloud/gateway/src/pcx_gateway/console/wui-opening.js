import { UI } from "./render.js";

const HOLD_MS = 260;
const DOCK_MS = 720;
const START_TIMEOUT_MS = 2800;
const WORDMARK_ASPECT = 632 / 314;

export class WuiOpening {
  constructor() {
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.preload = "auto";
    this.video.src = new URL("./brand/wui-apertura.mp4", import.meta.url);
    this.mark = new Image();
    this.mark.src = new URL("./brand/wui.svg", import.meta.url);
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)");
    this.startedAt = null;
    this.dockAt = null;
    this.finished = false;
    this.sceneVisible = false;
    this.failed = false;
  }

  draw(ctx, width, height, scale, flipped, now) {
    if (this.reduced.matches || (this.mark.complete && !this.mark.naturalWidth)) {
      this.video.pause();
      this.finished = true;
    }
    if (this.startedAt === null && !this.finished) {
      this.startedAt = now;
      this.video.play().catch(() => { this.failed = true; });
    }

    const elapsed = now - (this.startedAt ?? now);
    if (!this.finished && this.dockAt === null
        && (this.video.ended || this.video.error || this.failed || elapsed > START_TIMEOUT_MS)) {
      this.video.pause();
      this.dockAt = now;
    }
    const moving = this.dockAt === null ? 0 : Math.max(0, (now - this.dockAt - HOLD_MS) / DOCK_MS);
    if (moving >= 1) this.finished = true;
    this.sceneVisible = this.finished || moving > 0;

    const margin = 10 * scale;
    const small = Math.min(78 * scale, width * 0.18, height * 0.14 * WORDMARK_ASPECT);
    const large = Math.min(width * 0.45, height * 0.53 * WORDMARK_ASPECT);
    const dockX = Math.min(UI.bar / 2 * scale, width - small / 2 - margin);
    const dockY = height - small / (2 * WORDMARK_ASPECT) - margin;
    const progress = this.finished ? 1 : moving * moving * (3 - 2 * moving);
    const x = width / 2 + (dockX - width / 2) * progress;
    const y = height / 2 + (dockY - height / 2) * progress;
    const w = large + (small - large) * progress;
    const h = w / WORDMARK_ASPECT;
    const playing = !this.finished && this.dockAt === null;
    ctx.save();
    if (!this.finished) {
      ctx.fillStyle = `rgba(0,0,0,${1 - progress})`;
      ctx.fillRect(0, 0, width, height);
    }
    if (flipped) {
      ctx.translate(width, height);
      ctx.rotate(Math.PI);
    }
    if (playing && this.video.readyState >= 2) ctx.drawImage(this.video, x - w / 2, y - h / 2, w, h);
    else if (!playing && this.mark.naturalWidth) ctx.drawImage(this.mark, x - w / 2, y - h / 2, w, h);
    ctx.restore();
  }
}
