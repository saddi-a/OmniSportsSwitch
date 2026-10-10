const ctx = new AudioContext();
const gains = {};

chrome.runtime.onMessage.addListener(async (m) => {
  if (m.type === "setGain" && gains[m.tabId]) gains[m.tabId].gain.value = m.value;
  if (m.type !== "start") return;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: m.streamId } },
    });
    await ctx.resume();

    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    const gain = ctx.createGain();
    gains[m.tabId] = gain;

    // Analyser sits before the gain node, so it keeps measuring even when the gain mutes the output.
    src.connect(analyser);
    src.connect(gain);
    gain.connect(ctx.destination);

    const buf = new Float32Array(analyser.fftSize);
    setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const x of buf) sum += x * x;
      chrome.runtime.sendMessage({
        type: "level",
        tabId: m.tabId,
        title: m.title,
        rms: Math.sqrt(sum / buf.length),
        gain: gain.gain.value,
        ctxState: ctx.state,
        track: stream.getAudioTracks()[0]?.readyState,
      });
    }, 500);
  } catch (e) {
    chrome.runtime.sendMessage({ type: "error", tabId: m.tabId, stage: "getUserMedia", error: String(e) });
  }
});
