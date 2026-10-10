// Service worker: browser-only actions (tracking a tab, moving tabs between windows).
// The browser can shut it down at any time, so all state lives in chrome.storage.local:
//   tracked:     { tabId: { title, sport, teams, status } }   labels you set in the popup (status: live|break|over)
//   weights:     { tabId: number }                     the tab's own weight
//   priorities:  { sports: { name: weight }, teams: [{ name, mult }] }
//   windowRoles: { windowId: "MAIN" | "LEFT" | ... }   <- roles belong to windows (screen slots)
// A tab's role is simply the role of the window it is currently in.
// Long-lived work (audio capture, daemon connection) lives in offscreen.js.

importScripts("defaults.js");

const FILL_ORDER = ["LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"]; // used once MAIN is taken

const getState = () =>
  chrome.storage.local.get({ tracked: {}, weights: {}, windowRoles: {}, priorities: DEFAULT_PRIORITIES });

// Final weight sent to the daemon = tab weight x sport weight x best matching favourite-team bonus.
function effectiveWeights({ tracked, weights, priorities }) {
  const out = {};
  for (const [id, info] of Object.entries(tracked)) {
    const sportWeight = priorities.sports[info.sport] ?? 1;
    const labels = (info.teams ?? "").toLowerCase();
    let teamMult = 1;
    for (const team of priorities.teams) {
      if (team.name && labels.includes(team.name.toLowerCase())) teamMult = Math.max(teamMult, team.mult);
    }
    out[id] = (weights[id] ?? 1) * sportWeight * teamMult * (STATUS_MULT[info.status] ?? 1);
  }
  return out;
}

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

// Role of every tracked tab, derived from the window it is currently in.
async function computeTabRoles() {
  const { tracked, windowRoles } = await getState();
  const roles = {};
  for (const id of Object.keys(tracked)) {
    try {
      const tab = await chrome.tabs.get(Number(id));
      if (windowRoles[tab.windowId]) roles[id] = windowRoles[tab.windowId];
    } catch {
      // tab is gone; its "stopped" message will clean it up
    }
  }
  return roles;
}

async function pushState() {
  if (!(await chrome.offscreen.hasDocument())) return;
  const weights = effectiveWeights(await getState());
  chrome.runtime.sendMessage({ type: "state", roles: await computeTabRoles(), weights }).catch(() => {});
}

async function trackTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  await ensureOffscreen();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });

  const { tracked, weights, windowRoles } = await getState();
  tracked[tabId] = { sport: "", teams: "", status: "live", ...tracked[tabId], title: tab.title };
  weights[tabId] ??= 1;
  if (!windowRoles[tab.windowId]) {
    // First tracked window becomes MAIN; later ones fill the other slots in order.
    const taken = new Set(Object.values(windowRoles));
    windowRoles[tab.windowId] = !taken.has("MAIN") ? "MAIN" : (FILL_ORDER.find((r) => !taken.has(r)) ?? null);
    if (!windowRoles[tab.windowId]) delete windowRoles[tab.windowId];
  }
  await chrome.storage.local.set({ tracked, weights, windowRoles });

  chrome.runtime.sendMessage({ type: "start", tabId, streamId });
  chrome.action.setBadgeText({ tabId, text: "ON" });
  await pushState();
}

async function forgetTab(tabId) {
  const { tracked, weights } = await getState();
  delete tracked[tabId];
  delete weights[tabId];
  await chrome.storage.local.set({ tracked, weights });
}

// A window with a single tab closes when that tab leaves it, so make sure each slot window keeps a spare tab.
async function ensureSpareTab(windowId) {
  const tabs = await chrome.tabs.query({ windowId });
  if (tabs.length < 2) await chrome.tabs.create({ windowId, active: false, url: "about:blank" });
}

// Window roles stay put; the two tabs trade windows, so their roles trade automatically.
async function performSwap(promoteId, demoteId) {
  const [promote, demote] = await Promise.all([chrome.tabs.get(promoteId), chrome.tabs.get(demoteId)]);
  await ensureSpareTab(promote.windowId);
  await ensureSpareTab(demote.windowId);

  await chrome.tabs.move(promoteId, { windowId: demote.windowId, index: -1 });
  await chrome.tabs.update(promoteId, { active: true });
  await chrome.tabs.move(demoteId, { windowId: promote.windowId, index: -1 });
  await chrome.tabs.update(demoteId, { active: true });
  await pushState();
}

// Audio captures do not survive an extension reload or browser restart, so forget tracked tabs and
// drop roles of windows that no longer exist.
async function reconcile() {
  const open = new Set((await chrome.windows.getAll()).map((w) => String(w.id)));
  const { windowRoles } = await getState();
  for (const id of Object.keys(windowRoles)) if (!open.has(id)) delete windowRoles[id];
  await chrome.storage.local.set({ tracked: {}, weights: {}, windowRoles });
}
chrome.runtime.onInstalled.addListener(reconcile);
chrome.runtime.onStartup.addListener(reconcile);

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "track") trackTab(m.tabId).catch((e) => console.error("track failed", e));
  if (m.type === "swap") performSwap(m.promote, m.demote).catch((e) => console.error("swap failed", e));
  if (m.type === "untrack") {
    chrome.runtime.sendMessage({ type: "stop", tabId: m.tabId }).catch(() => {}); // offscreen then reports "stopped"
    chrome.action.setBadgeText({ tabId: m.tabId, text: "" }).catch(() => {});
  }
  if (m.type === "stopped") forgetTab(m.tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.runtime.sendMessage({ type: "stop", tabId }).catch(() => {});
});

chrome.tabs.onAttached.addListener(() => pushState());

chrome.windows.onRemoved.addListener(async (windowId) => {
  const { windowRoles } = await getState();
  if (windowRoles[windowId]) {
    delete windowRoles[windowId];
    await chrome.storage.local.set({ windowRoles });
  }
});

// The popup edits window roles and weights by writing storage; forward the change to the offscreen page.
chrome.storage.onChanged.addListener((_, area) => {
  if (area === "local") pushState();
});
