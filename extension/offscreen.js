// Long-lived hidden page: captures tab audio, streams it to the daemon, applies muting, and keeps the WebSocket.
const DAEMON_URL = "ws://127.0.0.1:8765";
const RECONNECT_MS = 2000;
const ANALYSIS_RATE = 32000;      // what the daemon's audio classifier expects
const CHUNK_SAMPLES = 3200;       // 100 ms of audio per message

const playbackCtx = new AudioContext();                        // full-quality playback of the tab
const analysisCtx = new AudioContext({ sampleRate: ANALYSIS_RATE }); // separate context, only for analysis
const workletReady = analysisCtx.audioWorklet.addModule("pcm-processor.js");

const captures = new Map(); // tabId -> { stream, gain, node }
let state = { roles: {}, weights: {}, labels: {} }; // pushed by the service worker
let socket = null;

function send(obj) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(obj));
}

// Binary audio message: 4-byte little-endian tab id, then 16-bit little-endian mono samples.
function sendPcm(tabId, samples) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const buf = new ArrayBuffer(4 + samples.length * 2);
  const view = new DataView(buf);
  view.setUint32(0, tabId, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(4 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  socket.send(buf);
}

function register(tabId) {
  send({
    type: "register",
    tab_id: tabId,
    weight: state.weights[tabId] ?? 1,
    is_main: state.roles[tabId] === "MAIN",
    sport: state.labels[tabId]?.sport ?? "",
    status: state.labels[tabId]?.status ?? "auto",
    role: state.roles[tabId] ?? "",
    allowed: state.labels[tabId]?.allowed ?? null,
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
    if (msg.type === "arrange" || msg.type === "focus_tile") chrome.runtime.sendMessage(msg); // service worker performs it
  };
  socket.onclose = () => setTimeout(connect, RECONNECT_MS);
  socket.onerror = () => socket.close();
}

async function startCapture({ tabId, streamId }) {
  if (captures.has(tabId)) return;
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
  });
  await playbackCtx.resume();
  await analysisCtx.resume();
  await workletReady;

  // Playback path. Capturing a tab silences it, so route it back out through a gain node (used for muting).
  const gain = playbackCtx.createGain();
  playbackCtx.createMediaStreamSource(stream).connect(gain);
  gain.connect(playbackCtx.destination);

  // Analysis path: raw audio blocks -> 100 ms chunks -> daemon. The silent gain keeps the worklet running.
  const node = new AudioWorkletNode(analysisCtx, "pcm-processor");
  const silent = analysisCtx.createGain();
  silent.gain.value = 0;
  analysisCtx.createMediaStreamSource(stream).connect(node);
  node.connect(silent).connect(analysisCtx.destination);

  let pending = new Float32Array(CHUNK_SAMPLES);
  let filled = 0;
  node.port.onmessage = (e) => {
    const block = e.data;
    for (let i = 0; i < block.length; i++) {
      pending[filled++] = block[i];
      if (filled === CHUNK_SAMPLES) {
        sendPcm(tabId, pending);
        filled = 0;
      }
    }
  };

  captures.set(tabId, { stream, gain, node });
  stream.getAudioTracks()[0].addEventListener("ended", () => stopCapture(tabId)); // e.g. tab reloaded
  register(tabId);
  applyMute();
}

function stopCapture(tabId) {
  const c = captures.get(tabId);
  if (!c) return;
  c.node.port.onmessage = null;
  c.node.disconnect();
  c.stream.getTracks().forEach((t) => t.stop());
  captures.delete(tabId);
  send({ type: "unregister", tab_id: tabId });
  chrome.runtime.sendMessage({ type: "stopped", tabId }).catch(() => {});
}

chrome.runtime.onMessage.addListener((m, sender) => {
  if (m.type === "video-rect" && sender.tab && captures.has(sender.tab.id)) {
    send({ type: "video_rect", tab_id: sender.tab.id, rects: m.rects, visible: m.visible, fullscreen: m.fullscreen });
  }
  if (m.type === "start") startCapture(m).catch((e) => console.error("capture failed", e));
  if (m.type === "stop") stopCapture(m.tabId);
  if (m.type === "resync") for (const tabId of captures.keys()) register(tabId);
  if (m.type === "state") {
    const before = state;
    state = { roles: m.roles, weights: m.weights, labels: m.labels ?? {} };
    for (const tabId of captures.keys()) {
      const changed =
        before.roles[tabId] !== state.roles[tabId] ||
        before.weights[tabId] !== state.weights[tabId] ||
        JSON.stringify(before.labels[tabId]) !== JSON.stringify(state.labels[tabId]);
      if (changed) register(tabId); // re-sending register updates weight and main flag on the daemon
    }
    applyMute();
  }
});

connect();
chrome.runtime.sendMessage({ type: "offscreen-ready" });
