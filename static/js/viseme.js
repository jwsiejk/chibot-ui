class VisemeStage {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.active = false;
    this.schedule = [];
    this.audio = null;
    this._raf = null;
    this._lastViseme = "";
  }
  animate(schedule, audioEl) {
    this.schedule = Array.isArray(schedule) ? schedule : [];
    this.audio = audioEl;
    this.active = true;
    this._lastViseme = "";
    this._tick();
  }
  stop() {
    this.active = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this._clear();
  }
  _tick() {
    if (!this.active || !this.audio) return;
    const ratio = (this.audio.duration && this.audio.currentTime) ? Math.min(1, Math.max(0, this.audio.currentTime / this.audio.duration)) : 0;
    let current = this.schedule[0] || {t:0, id:"REST"};
    for (let i = 0; i < this.schedule.length; i++) {
      if (this.schedule[i].t <= ratio) current = this.schedule[i];
      else break;
    }
    if (current.id !== this._lastViseme) {
      this._drawViseme(current.id);
      this._lastViseme = current.id;
    }
    if (this.audio.ended || ratio >= 1) {
      this.active = false;
      this._clear();
      return;
    }
    this._raf = requestAnimationFrame(this._tick.bind(this));
  }
  _clear(){
    const {width, height} = this.canvas;
    this.ctx.clearRect(0,0,width,height);
  }
  _drawViseme(id){
    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    this._clear();
    const cx = w/2, cy = h/2, mw = Math.min(w*0.6, 320), mh = Math.min(h*0.45, 160);
    ctx.lineWidth = 6;
    ctx.strokeStyle = "#ff6a00";
    ctx.fillStyle = "#111";
    switch(id){
      case "M":
        this._roundedRect(cx-mw/4, cy-mh/12, mw/2, mh/6, mh/10); ctx.fill(); ctx.stroke(); break;
      case "F":
        this._roundedRect(cx-mw/2.8, cy-mh/10, mw*0.7, mh*0.22, mh/10); ctx.fill(); ctx.stroke(); this._teeth(cx, cy, mw*0.6, mh*0.15); break;
      case "L":
        this._roundedRect(cx-mw/2.8, cy-mh/10, mw*0.7, mh*0.28, mh/10); ctx.fill(); ctx.stroke(); this._tongue(cx, cy+mh*0.02, mw*0.5, mh*0.10); break;
      case "O":
        this._oval(cx, cy, mw*0.35, mh*0.35); ctx.fill(); ctx.stroke(); break;
      case "E":
        this._roundedRect(cx-mw/1.9, cy-mh/8, mw*0.95, mh*0.25, mh/8); ctx.fill(); ctx.stroke(); break;
      case "AI":
        this._roundedRect(cx-mw/2.2, cy-mh/6, mw*0.9, mh*0.5, mh/6); ctx.fill(); ctx.stroke(); this._teeth(cx, cy-mh*0.05, mw*0.8, mh*0.15); break;
      case "S":
        this._roundedRect(cx-mw/3.0, cy-mh/12, mw*0.6, mh*0.22, mh/10); ctx.fill(); ctx.stroke(); break;
      case "R":
        this._oval(cx, cy, mw*0.45, mh*0.28); ctx.fill(); ctx.stroke(); break;
      case "N":
        this._roundedRect(cx-mw/3.2, cy-mh/14, mw*0.62, mh*0.20, mh/12); ctx.fill(); ctx.stroke(); break;
      default:
        this._roundedRect(cx-mw/2.5, cy-mh/14, mw*0.8, mh*0.18, mh/12); ctx.fill(); ctx.stroke();
    }
  }
  _roundedRect(x,y,w,h,r){
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(x+r, y);
    ctx.lineTo(x+w-r, y);
    ctx.quadraticCurveTo(x+w, y, x+w, y+r);
    ctx.lineTo(x+w, y+h-r);
    ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
    ctx.lineTo(x+r, y+h);
    ctx.quadraticCurveTo(x, y+h, x, y+h-r);
    ctx.lineTo(x, y+r);
    ctx.quadraticCurveTo(x, y, x+r, y);
    ctx.closePath();
  }
  _oval(cx, cy, rx, ry){
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI*2);
    ctx.closePath();
  }
  _teeth(cx, cy, w, h){
    const ctx = this.ctx;
    ctx.save(); ctx.fillStyle = "#ddd";
    this._roundedRect(cx-w/2, cy-h/2, w, h, Math.min(12, h/3)); ctx.fill();
    ctx.restore();
  }
  _tongue(cx, cy, w, h){
    const ctx = this.ctx;
    ctx.save(); ctx.fillStyle = "#b75f5f";
    this._oval(cx, cy, w/2, h/2); ctx.fill();
    ctx.restore();
  }
}
