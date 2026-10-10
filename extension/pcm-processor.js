// Runs on the audio thread: hands raw audio blocks (128 samples each) back to offscreen.js.
class PcmProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor("pcm-processor", PcmProcessor);
