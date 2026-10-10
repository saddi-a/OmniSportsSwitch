// Service worker: browser-only actions (tracking a tab, moving tabs between windows).
// The browser can shut it down at any time, so all state lives in chrome.storage.local:
//   tracked:     { tabId: { title, sport, teams, status } }   labels you set in the popup (status: auto|live|break|over)
//   weights:     { tabId: number }                     the tab's own weight
//   priorities:  { sports: { name: weight }, teams: [{ name, mult }] }
//   windowRoles: { windowId: "MAIN" | "LEFT" | ... }   <- roles belong to windows (screen slots)
//   layout:      [{ role, cx, cy }]                   <- where each role's window sat on screen (centre), so roles
//                                                        can be given back to windows after a restart
// A tab's role is simply the role of the window it is currently in.
// Long-lived work (audio capture, daemon connection) lives in offscreen.js.

importScripts("defaults.js");

const FILL_ORDER = ["LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"]; // used once MAIN is taken

const getState = () =>
  chrome.storage.local.get({ tracked: {}, weights: {}, windowRoles: {}, priorities: DEFAULT_PRIORITIES });

// Weight sent to the daemon = tab weight x sport weight. Favourite teams are matched by the daemon.
function effectiveWeights({ tracked, weights, priorities }) {
  const out = {};
  for (const [id, info] of Object.entries(tracked)) {
    const sportWeight = priorities.sports[info.sport] ?? 1;
    out[id] = (weights[id] ?? 1) * sportWeight; // game status and favourite teams are handled by the daemon
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
  const { tracked, priorities } = await getState();
  const labels = Object.fromEntries(Object.entries(tracked).map(([id, t]) => [id, { sport: t.sport, status: t.status, allowed: t.allowed ?? null, teams: t.teams ?? "" }]));
  chrome.runtime
    .sendMessage({ type: "state", roles: await computeTabRoles(), weights, labels, favourites: priorities.teams })
    .catch(() => {});
}

// Runs inside the stream page: once a second, reports where the playing video sits on the screen
// (physical pixels), so the daemon knows which part of the screen to read. Reading position is not blocked by DRM.
function installVideoReporter() {
  if (window.__omniReporter) return;
  window.__omniReporter = setInterval(() => {
    // Page order is kept (not sorted by size) so a tile keeps the same index while it stays on the page.
    const candidates = [...document.querySelectorAll("video")]
      .map((video, index) => ({ video, index, r: video.getBoundingClientRect() }))
      .filter(({ video, r }) => r.width > 200 && r.height > 100 && video.videoWidth > 0 && !video.paused);
    const dpr = window.devicePixelRatio;
    const border = (window.outerWidth - window.innerWidth) / 2; // window frame on each side
    const toolbar = window.outerHeight - window.innerHeight - border; // tab strip + address bar
    // Every playing video (DAZN multiview has several); `audible` marks the one with sound.
    const rects = candidates.map(({ video, index, r }) => ({
      index,
      audible: !video.muted && video.volume > 0,
      left: Math.round((window.screenX + border + r.left) * dpr),
      top: Math.round((window.screenY + toolbar + r.top) * dpr),
      width: Math.round(r.width * dpr),
      height: Math.round(r.height * dpr),
    }));
    chrome.runtime
      .sendMessage({ type: "video-rect", rects, visible: document.visibilityState === "visible", fullscreen: !!document.fullscreenElement })
      .catch(() => {});
  }, 1000);
}

// Gives a multiview tile the sound, the same as clicking it. `index` is the page-order index reported above.
function clickVideoTile(index) {
  const video = document.querySelectorAll("video")[index];
  if (video) video.click();
}

// DAZN only expands a multiview tile (makes it the main one) for a genuine mouse click; a script click just
// moves the sound. So send a real click through Edge's debugging API: attach, hover, click, detach.
// Edge shows its "is debugging this browser" bar while attached, which is only a moment.
async function expandTile(tabId, index) {
  const [{ result: center }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (i) => {
      const video = document.querySelectorAll("video")[i];
      if (!video) return null;
      const r = video.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2];
    },
    args: [index],
  });
  if (!center) return;
  const [x, y] = center;
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    const mouse = (type, extra = {}) => chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", { type, x, y, ...extra });
    await mouse("mouseMoved");
    await new Promise((resolve) => setTimeout(resolve, 700)); // lets the tile show its expand icon
    await mouse("mousePressed", { button: "left", clickCount: 1 });
    await mouse("mouseReleased", { button: "left", clickCount: 1 });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

function removeVideoReporter() {
  clearInterval(window.__omniReporter);
  window.__omniReporter = null;
}

// Freeze: while on, the daemon makes no swaps and no tile changes. State lives in storage, the daemon is told via
// the offscreen page, and every tracked tab's badge shows LOCK.
async function setFrozen(frozen) {
  await chrome.storage.local.set({ frozen });
  if (await chrome.offscreen.hasDocument()) chrome.runtime.sendMessage({ type: "freeze", value: frozen }).catch(() => {});
  const { tracked } = await getState();
  await chrome.action.setBadgeBackgroundColor({ color: frozen ? "#c62828" : "#444444" });
  for (const id of Object.keys(tracked)) chrome.action.setBadgeText({ tabId: Number(id), text: frozen ? "LOCK" : "ON" }).catch(() => {});
}

async function toggleFreeze() {
  const { frozen = false } = await chrome.storage.local.get("frozen");
  await setFrozen(!frozen);
}

chrome.commands.onCommand.addListener(async (command) => {
  if (command === "toggle-freeze") toggleFreeze();
  if (command === "track-tab") {
    // The shortcut counts as the click Edge needs: (re)track the visible tab of the window you are in.
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab) trackTab(tab.id).catch((e) => console.error("track failed", e));
  }
});

async function trackTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  await ensureOffscreen();
  // Re-tracking a tab whose capture is still running makes Edge refuse a second capture: that is fine, the tab is
  // simply refreshed (the position reporter is installed again below).
  let streamId = null;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  } catch (e) {
    console.warn("capture not restarted (already running or not allowed):", e.message);
  }

  const { tracked, weights, windowRoles } = await getState();
  tracked[tabId] = { sport: "", teams: "", status: "auto", ...tracked[tabId], title: tab.title };
  weights[tabId] ??= 1;
  if (!windowRoles[tab.windowId]) {
    // First tracked window becomes MAIN; later ones fill the other slots in order.
    const taken = new Set(Object.values(windowRoles));
    windowRoles[tab.windowId] = !taken.has("MAIN") ? "MAIN" : (FILL_ORDER.find((r) => !taken.has(r)) ?? null);
    if (!windowRoles[tab.windowId]) delete windowRoles[tab.windowId];
  }
  await chrome.storage.local.set({ tracked, weights, windowRoles });

  if (streamId) chrome.runtime.sendMessage({ type: "start", tabId, streamId });
  chrome.scripting.executeScript({ target: { tabId }, func: installVideoReporter }).catch((e) => console.error("reporter failed", e));
  const { frozen = false } = await chrome.storage.local.get("frozen");
  chrome.action.setBadgeText({ tabId, text: frozen ? "LOCK" : "ON" });
  await pushState();
}

async function forgetTab(tabId) {
  chrome.scripting.executeScript({ target: { tabId }, func: removeVideoReporter }).catch(() => {});
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

// The daemon decides who goes where (it knows the allowed slots); this just moves each tab into the window
// that carries the requested slot. Window roles stay put, so a tab's role follows the window it ends up in.
async function performArrange(moves) {
  const { windowRoles } = await getState();
  const windowOfRole = Object.fromEntries(Object.entries(windowRoles).map(([id, role]) => [role, Number(id)]));
  const plan = moves.filter((m) => windowOfRole[m.role] !== undefined);
  if (plan.length !== moves.length) return;
  for (const m of plan) await ensureSpareTab((await chrome.tabs.get(m.tab_id)).windowId); // a window must never end up empty
  for (const m of plan) {
    await chrome.tabs.move(m.tab_id, { windowId: windowOfRole[m.role], index: -1 });
    await chrome.tabs.update(m.tab_id, { active: true });
  }
  await pushState();
}

// Remember where each role's window sits on screen. Entries for roles whose window is currently closed are kept.
async function saveLayout() {
  const { windowRoles } = await getState();
  const { layout = [] } = await chrome.storage.local.get("layout");
  const byRole = Object.fromEntries(layout.map((e) => [e.role, e]));
  for (const [id, role] of Object.entries(windowRoles)) {
    try {
      const w = await chrome.windows.get(Number(id));
      byRole[role] = { role, cx: w.left + w.width / 2, cy: w.top + w.height / 2 };
    } catch {
      // window is gone
    }
  }
  await chrome.storage.local.set({ layout: Object.values(byRole) });
}

// Give each window without a role the saved role whose position it matches (nearest centre, and close enough).
async function restoreRoles() {
  const { windowRoles } = await getState();
  const { layout = [] } = await chrome.storage.local.get("layout");
  const taken = new Set(Object.values(windowRoles));
  let changed = false;
  for (const w of await chrome.windows.getAll()) {
    if (windowRoles[w.id] || w.type !== "normal") continue;
    const cx = w.left + w.width / 2;
    const cy = w.top + w.height / 2;
    const best = layout
      .filter((e) => !taken.has(e.role))
      .map((e) => ({ e, distance: Math.hypot(e.cx - cx, e.cy - cy) }))
      .sort((a, b) => a.distance - b.distance)[0];
    if (best && best.distance < Math.max(w.width, w.height) * 0.4) {
      windowRoles[w.id] = best.e.role;
      taken.add(best.e.role);
      changed = true;
    }
  }
  if (changed) await chrome.storage.local.set({ windowRoles });
}

// Windows opened later (Edge restoring a session) get their role once they have settled in place.
chrome.windows.onCreated.addListener(() => setTimeout(restoreRoles, 1500));
// Moving a window updates where its role is remembered.
let layoutTimer = null;
chrome.windows.onBoundsChanged.addListener(() => {
  clearTimeout(layoutTimer);
  layoutTimer = setTimeout(saveLayout, 2000);
});

// Audio captures do not survive an extension reload or browser restart, so forget tracked tabs and
// drop roles of windows that no longer exist.
async function reconcile() {
  const open = new Set((await chrome.windows.getAll()).map((w) => String(w.id)));
  const { windowRoles } = await getState();
  for (const id of Object.keys(windowRoles)) if (!open.has(id)) delete windowRoles[id];
  await chrome.storage.local.set({ tracked: {}, weights: {}, windowRoles, frozen: false });
  await restoreRoles();
  await saveLayout(); // remember where the windows that already have a role sit
}
chrome.runtime.onInstalled.addListener(reconcile);
chrome.runtime.onStartup.addListener(reconcile);

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "toggle_freeze") toggleFreeze();
  if (m.type === "track") trackTab(m.tabId).catch((e) => console.error("track failed", e));
  if (m.type === "focus_tile") {
    expandTile(m.tab_id, m.index).catch((e) => {
      console.error("expand failed, falling back to a script click (moves the sound only)", e);
      chrome.scripting.executeScript({ target: { tabId: m.tab_id }, func: clickVideoTile, args: [m.index] }).catch(() => {});
    });
  }
  if (m.type === "arrange") performArrange(m.moves).catch((e) => console.error("arrange failed", e));
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
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (changes.windowRoles) saveLayout();
  pushState();
});
