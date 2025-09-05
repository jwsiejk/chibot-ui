
// VisemeAnimator — drives 2D mouth sprites or CSS based on a viseme schedule (schedule viseme t_ms)
export class VisemeAnimator {
  constructor(mouthEl) {
    this.mouthEl = mouthEl; // e.g., <img id="chipMouth">
    this.timerIds = [];
    this.map = {
      "A": "viseme-a",
      "B": "viseme-b",
      "C": "viseme-c",
      "D": "viseme-d",
      "E": "viseme-e"
    };
  }
  play(schedule, startAtMs=0) {
    this.stop();
    const t0 = performance.now();
    (schedule || []).forEach(entry => {
      const at = startAtMs + (entry.t_ms || 0);
      const v = entry.v || "A";
      const id = setTimeout(() => this._setViseme(v), at);
      this.timerIds.push(id);
    });
    // Return approx duration
    if (schedule && schedule.length) {
      return schedule[schedule.length-1].t_ms + 120;
    }
    return 0;
  }
  _setViseme(v) {
    const cls = this.map[v] || "viseme-a";
    const el = this.mouthEl;
    if (!el) return;
    el.setAttribute("data-viseme", v);
    el.classList.remove(...Object.values(this.map));
    el.classList.add(cls);
  }
  stop() {
    this.timerIds.forEach(id => clearTimeout(id));
    this.timerIds = [];
  }
}
