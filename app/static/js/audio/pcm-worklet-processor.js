class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor(options = {}) {
    super();
    const opts = options.processorOptions || {};
    this._targetRate = typeof opts.targetSampleRate === 'number' && opts.targetSampleRate > 0
      ? opts.targetSampleRate
      : 16000;
    const minSamples = typeof opts.minFrameSamples === 'number' && opts.minFrameSamples > 0
      ? Math.floor(opts.minFrameSamples)
      : 320;
    const maxSamples = typeof opts.maxFrameSamples === 'number' && opts.maxFrameSamples >= minSamples
      ? Math.floor(opts.maxFrameSamples)
      : Math.max(minSamples, 640);
    this._minFrameSamples = minSamples;
    this._maxFrameSamples = maxSamples;
    this._sourceRate = sampleRate || this._targetRate;
    this._needsResample = Math.abs(this._sourceRate - this._targetRate) > 0.5;
    this._inputBuffer = [];
    this._resampleIndex = 0;
    this._pendingOutput = [];
    this._int16Max = 0x7fff;
    this._int16Min = -0x8000;
  }

  static get parameterDescriptors() {
    return [];
  }

  process(inputs) {
    const channels = inputs && inputs[0] ? inputs[0] : [];
    const frameCount = channels[0] ? channels[0].length : 0;
    if (!frameCount) {
      return true;
    }

    for (let i = 0; i < frameCount; i += 1) {
      let mono = 0;
      if (channels.length === 0) {
        mono = 0;
      } else if (channels.length === 1) {
        mono = channels[0][i] || 0;
      } else {
        let sum = 0;
        for (let ch = 0; ch < channels.length; ch += 1) {
          sum += channels[ch][i] || 0;
        }
        mono = sum / channels.length;
      }

      if (this._needsResample) {
        this._inputBuffer.push(mono);
      } else {
        this._pendingOutput.push(this._floatToInt16(mono));
        this._flushPending(false);
      }
    }

    if (this._needsResample && this._inputBuffer.length) {
      this._resamplePending();
    }

    return true;
  }

  _resamplePending() {
    const step = this._sourceRate / this._targetRate;
    if (!isFinite(step) || step <= 0) {
      return;
    }
    let index = this._resampleIndex;
    const buffer = this._inputBuffer;

    while (buffer.length - 1 > index) {
      const baseIndex = Math.floor(index);
      const nextIndex = baseIndex + 1;
      if (nextIndex >= buffer.length) {
        break;
      }
      const frac = index - baseIndex;
      const sampleA = buffer[baseIndex];
      const sampleB = buffer[nextIndex];
      const interpolated = sampleA + (sampleB - sampleA) * frac;
      this._pendingOutput.push(this._floatToInt16(interpolated));
      this._flushPending(false);
      index += step;
    }

    const consumed = Math.floor(index);
    if (consumed > 0) {
      this._inputBuffer = buffer.slice(consumed);
      index -= consumed;
    }
    this._resampleIndex = index;
  }

  _floatToInt16(sample) {
    const clamped = Math.max(-1, Math.min(1, sample || 0));
    const scaled = Math.round(clamped * this._int16Max);
    if (scaled < this._int16Min) {
      return this._int16Min;
    }
    if (scaled > this._int16Max) {
      return this._int16Max;
    }
    return scaled;
  }

  _flushPending(force) {
    const pending = this._pendingOutput;
    if (!pending.length) {
      return;
    }
    if (!force && pending.length < this._minFrameSamples) {
      return;
    }

    while (pending.length >= this._minFrameSamples || force) {
      const take = force
        ? pending.length
        : Math.min(Math.max(this._minFrameSamples, pending.length), this._maxFrameSamples);
      if (!take) {
        break;
      }
      const frame = new Int16Array(take);
      for (let i = 0; i < take; i += 1) {
        frame[i] = pending[i] || 0;
      }
      pending.splice(0, take);
      const buffer = frame.buffer;
      this.port.postMessage({
        type: 'pcm16',
        buffer,
        samples: take,
        sampleRate: this._targetRate,
      }, [buffer]);
      if (pending.length < this._minFrameSamples) {
        break;
      }
      if (force) {
        force = false;
      }
    }
  }

  flush() {
    this._flushPending(true);
  }
}

registerProcessor('pcm-worklet-processor', PCMWorkletProcessor);
