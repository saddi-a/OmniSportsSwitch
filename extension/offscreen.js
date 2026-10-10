// Long-lived hidden page: captures tab audio, measures its level, applies muting, and keeps the WebSocket to the daemon.
const DAEMON_URL = "ws://127.0.0.1:8765";
const SEND_EVERY_MS = 250;
const RECONNECT_MS = 2000;

const audioCtx = new AudioContext();
const captures = new Map(); // tabId -> { stream, timer, gain }
let state = { roles: {}, weights: {} }; // pushed by the service worker
let socket = null;

function send(obj) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(obj));
}

function register(tabId) {
  send({
    type: "register",
    tab_id: tabId,
    weight: state.weights[tabId] ?? 1,
    is_main: state.roles[tabId] === "MAIN",
  });
}

// Only the MAIN tab is audible. If no tab is MAIN yet, leave everything audible.
function applyMute() {
  const hasMain = Object.values(state.roles).includes("MAIN");
  for (const [tabId, c] of captures) {
    c.gain.gain.value = !hasMain || state.roles[tabId] === "MAIN" ? 1 : 0;
  }
}

function connect() {
  socket = new WebSocket(DAEMON_URL);
  socket.onopen = () => {
    // After a reconnect the daemon has forgotten us, so register every active tab again.
    for (const tabId of captures.keys()) register(tabId);
  };
  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "swap") chrome.runtime.sendMessage(msg); // service worker performs it
  };
  socket.onclose = () => setTimeout(connect, RECONNECT_MS);
  socket.onerror = () => socket.close();
}

async function startCapture({ tabId, streamId }) {
  if (captures.has(tabId)) return;
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
  });
  await audioCtx.resume();

  const source = audioCtx.createMediaStreamSource(stream);
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  const gain = audioCtx.createGain();
  // Capturing a tab silences it, so route it back out. The level is measured before the gain node,
  // so muting a tab does not affect its measurement.
  source.connect(analyser);
  source.connect(gain);
  gain.connect(audioCtx.destination);

  const buf = new Float32Array(analyser.fftSize);
  const timer = setInterval(() => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const x of buf) sum += x * x;
    send({ type: "audio", tab_id: tabId, rms: Math.sqrt(sum / buf.length) });
  }, SEND_EVERY_MS);

  captures.set(tabId, { stream, timer, gain });
  stream.getAudioTracks()[0].addEventListener("ended", () => stopCapture(tabId)); // e.g. tab reloaded
  register(tabId);
  applyMute();
}

function stopCapture(tabId) {
  const c = captures.get(tabId);
  if (!c) return;
  clearInterval(c.timer);
  c.stream.getTracks().forEach((t) => t.stop());
  captures.delete(tabId);
  send({ type: "unregister", tab_id: tabId });
  chrome.runtime.sendMessage({ type: "stopped", tabId }).catch(() => {});
}

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "start") startCapture(m).catch((e) => console.error("capture failed", e));
  if (m.type === "stop") stopCapture(m.tabId);
  if (m.type === "state") {
    const before = state;
    state = { roles: m.roles, weights: m.weights };
    for (const tabId of captures.keys()) {
      const changed =
        before.roles[tabId] !== state.roles[tabId] || before.weights[tabId] !== state.weights[tabId];
      if (changed) register(tabId); // re-sending register updates weight and main flag on the daemon
    }
    applyMute();
  }
});

connect();
chrome.runtime.sendMessage({ type: "offscreen-ready" });
