// Results are kept in globalThis.levels / globalThis.errors so they can be read over the debug port.
globalThis.levels = {};
globalThis.errors = [];

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Measure tab audio level",
  });
  await new Promise((r) => setTimeout(r, 400));
}

chrome.action.onClicked.addListener(async (tab) => {
  try {
    await ensureOffscreen();
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    chrome.runtime.sendMessage({ type: "start", tabId: tab.id, streamId, title: tab.title });
    chrome.action.setBadgeText({ tabId: tab.id, text: "REC" });
  } catch (e) {
    globalThis.errors.push({ tabId: tab.id, stage: "start", error: String(e) });
  }
});

// Called from the debug port to mute/unmute a captured tab with a Web Audio gain node.
globalThis.setGain = (tabId, value) => chrome.runtime.sendMessage({ type: "setGain", tabId, value });

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "level") globalThis.levels[m.tabId] = { ...m, at: Date.now() };
  if (m.type === "error") globalThis.errors.push(m);
});
