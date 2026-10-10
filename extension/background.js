// Service worker: handles browser-only actions (icon clicks, tab events). It can be shut down by the
// browser at any time, so it keeps no state. The long-lived work lives in offscreen.js.

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  // Listen for the ready message before creating the page, or it could be missed.
  const ready = new Promise((resolve) => {
    const onReady = (m) => {
      if (m.type === "offscreen-ready") {
        chrome.runtime.onMessage.removeListener(onReady);
        resolve();
      }
    };
    chrome.runtime.onMessage.addListener(onReady);
  });
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Measure tab audio and talk to the local daemon",
  });
  await ready;
}

chrome.action.onClicked.addListener(async (tab) => {
  await ensureOffscreen();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  chrome.runtime.sendMessage({ type: "start", tabId: tab.id, streamId, title: tab.title });
  chrome.action.setBadgeText({ tabId: tab.id, text: "ON" });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.runtime.sendMessage({ type: "stop", tabId }).catch(() => {});
});

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "swap") console.log("swap requested", m); // executed in a later step
  if (m.type === "stopped") chrome.action.setBadgeText({ tabId: m.tabId, text: "" }).catch(() => {});
});
