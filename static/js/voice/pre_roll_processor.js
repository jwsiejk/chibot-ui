class PreRollProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs?.[0];
    if (!input || input.length === 0) {
      return true;
    }
    const channelData = input[0];
    if (channelData && channelData.length) {
      try {
        this.port.postMessage(channelData.slice());
      } catch (err) {
        // Swallow errors to avoid breaking the processor; logging is not available here.
      }
    }
    return true;
  }
}

registerProcessor('pre-roll-processor', PreRollProcessor);
