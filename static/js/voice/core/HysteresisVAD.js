const clampThreshold = (value, fallback) => {
  const num = Number.isFinite(value) ? Math.floor(value) : fallback;
  return Math.max(1, num);
};

export class HysteresisVAD {
  constructor({ enter = 3, exit = 4 } = {}) {
    this.enter = clampThreshold(enter, 3);
    this.exit = clampThreshold(exit, 4);
    this._state = 'silence';
    this._speechStreak = 0;
    this._silenceStreak = 0;
  }

  reset() {
    this._state = 'silence';
    this._speechStreak = 0;
    this._silenceStreak = 0;
  }

  push(isSpeech) {
    const speech = !!isSpeech;
    if (speech) {
      this._speechStreak += 1;
      this._silenceStreak = 0;
      if (this._state === 'speech') {
        return 'speech';
      }
      if (this._speechStreak >= this.enter) {
        this._state = 'speech';
        this._speechStreak = this.enter;
        return 'speech';
      }
      this._state = 'hold';
      return 'hold';
    }

    this._silenceStreak += 1;
    this._speechStreak = 0;
    if (this._state === 'silence') {
      return 'silence';
    }
    if (this._silenceStreak >= this.exit) {
      this._state = 'silence';
      this._silenceStreak = this.exit;
      return 'silence';
    }
    this._state = 'hold';
    return 'hold';
  }
}
