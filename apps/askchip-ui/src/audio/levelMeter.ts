function computeRms(samples: ArrayLike<number>): number {
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    sum += samples[index] * samples[index];
  }
  return Math.sqrt(sum / samples.length);
}

export class LiveLevelMeter {
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private samples: Uint8Array | null = null;
  private frameId: number | null = null;

  async start(stream: MediaStream, onLevel: (value: number) => void): Promise<void> {
    this.stop();
    const AudioContextCtor = window.AudioContext ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) {
      return;
    }

    this.context = new AudioContextCtor();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 1024;
    this.samples = new Uint8Array(this.analyser.fftSize);
    this.source = this.context.createMediaStreamSource(stream);
    this.source.connect(this.analyser);

    const tick = () => {
      if (!this.analyser || !this.samples) {
        return;
      }
      this.analyser.getByteTimeDomainData(this.samples as unknown as Uint8Array<ArrayBuffer>);
      const centeredSamples = Array.from(this.samples, (value) => (value - 128) / 128);
      const rms = computeRms(centeredSamples);
      onLevel(Math.min(1, Math.max(0, rms * 3.5)));
      this.frameId = window.requestAnimationFrame(tick);
    };

    tick();
  }

  stop(): void {
    if (this.frameId !== null) {
      window.cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    this.source?.disconnect();
    this.analyser?.disconnect();
    void this.context?.close();
    this.source = null;
    this.analyser = null;
    this.context = null;
    this.samples = null;
  }
}

export function normalizeLiveLevel(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}
