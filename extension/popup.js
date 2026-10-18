// The ids are stored in the browser and used by the daemon; the names shown to the user do not depend on the
// physical screens. Windows fill up in this order: Main, then Window 2, 3, 4, 5.
const ROLES = ["MAIN", "LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"];
const ROLE_NAMES = { MAIN: "Main", LEFT: "Window 2", RIGHT_BOT: "Window 3", RIGHT_MID: "Window 4", RIGHT_TOP: "Window 5" };
const $ = (id) => document.getElementById(id);
const SLOTS = ROLES.map((role) => [role, ROLE_NAMES[role]]);

// Read one storage key, change it, write it back, then redraw.
async function update(key, fallback, change) {
  const value = (await chrome.storage.local.get({ [key]: fallback }))[key];
  change(value);
  await chrome.storage.local.set({ [key]: value });
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function showFreeze() {
  const { frozen = false } = await chrome.storage.local.get("frozen");
  $("freeze").classList.toggle("on", frozen);
  $("freeze").textContent = frozen ? "FROZEN - click to resume (Ctrl+Shift+P)" : "Freeze Main (Ctrl+Shift+P)";
}

async function showFreeze2() {
  const { frozen2 = false } = await chrome.storage.local.get("frozen2");
  $("freeze2").classList.toggle("on", frozen2);
  $("freeze2").textContent = frozen2 ? "WINDOW 2 FROZEN - click to resume (Ctrl+Shift+L)" : "Freeze Window 2 (Ctrl+Shift+L)";
}

async function load() {
  showFreeze();
  showFreeze2();
  const { tracked = {}, weights = {}, windowRoles = {}, priorities = DEFAULT_PRIORITIES } =
    await chrome.storage.local.get();
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });

  // Role of the window this popup was opened from.
  const select = $("windowRole");
  select.replaceChildren(new Option("(none)", ""));
  for (const r of ROLES) select.append(new Option(ROLE_NAMES[r], r));
  select.value = windowRoles[active.windowId] ?? "";
  select.onchange = async () => {
    await update("windowRoles", {}, (current) => {
      // A role belongs to one window at a time, so take it away from whichever window had it.
      if (select.value) for (const [id, r] of Object.entries(current)) if (r === select.value) delete current[id];
      if (select.value) current[active.windowId] = select.value; else delete current[active.windowId];
    });
    load();
  };

  // Always available: after a page reload or a view change the tab needs tracking again (also on Ctrl+Shift+Y).
  $("track").hidden = false;
  $("track").textContent = String(active.id) in tracked ? "Re-track this tab (Ctrl+Shift+Y)" : "Track this tab (Ctrl+Shift+Y)";
  $("track").onclick = () => {
    chrome.runtime.sendMessage({ type: "track", tabId: active.id });
    window.close();
  };

  // One block per tracked tab.
  const list = $("list");
  list.replaceChildren();
  for (const [tabId, info] of Object.entries(tracked)) {
    let role = "";
    try {
      role = windowRoles[(await chrome.tabs.get(Number(tabId))).windowId] ?? "";
    } catch {
      continue; // tab no longer exists
    }

    const sport = el("select", { title: "Sport" });
    sport.append(new Option("(sport)", ""));
    for (const name of Object.keys(priorities.sports)) sport.append(new Option(name, name));
    sport.value = info.sport ?? "";
    sport.onchange = () => update("tracked", {}, (t) => (t[tabId].sport = sport.value));

    const teams = el("input", { type: "text", placeholder: "teams, e.g. Paris SG vs Lyon", value: info.teams ?? "" });
    teams.classList.add("grow");
    teams.onchange = () => update("tracked", {}, (t) => (t[tabId].teams = teams.value));

    const weight = el("input", { type: "number", min: "0", step: "0.1", value: weights[tabId] ?? 1, title: "Tab weight" });
    weight.onchange = () => update("weights", {}, (w) => (w[tabId] = Number(weight.value) || 0));

    const status = el("select", { title: "Game status" });
    for (const [value, label] of [["auto", "Auto"], ["live", "Live"], ["break", "Break"], ["over", "Over"]]) status.append(new Option(label, value));
    status.value = info.status ?? "auto";
    status.onchange = () => update("tracked", {}, (t) => (t[tabId].status = status.value));

    const allowedNow = info.allowed ?? SLOTS.map(([role]) => role);
    const slotBoxes = SLOTS.map(([role, label]) => {
      const box = el("input", { type: "checkbox", checked: allowedNow.includes(role) });
      box.onchange = () => update("tracked", {}, (t) => {
        const chosen = SLOTS.filter(([r], i) => slotBoxes[i].checked).map(([r]) => r);
        t[tabId].allowed = chosen.length === SLOTS.length ? null : chosen; // null = no restriction
      });
      return box;
    });
    const slotRow = el("div", { className: "row slots" }, el("span", { textContent: "Allowed in:" }),
      ...SLOTS.map(([role, label], i) => el("label", {}, slotBoxes[i], label)));

    trackedTitles[tabId] = info.title;
    const makeMain = el("button", { textContent: role === "MAIN" ? "Hold Main" : "To Main",
      title: "Make Main: put this stream on Main and keep it there for a while (the hold time is in Settings)" });
    makeMain.onclick = () => {
      connectSettings();
      const send = () => settingsSend({ type: "force_main", tab_id: Number(tabId) });
      if (settingsSocket.readyState === WebSocket.OPEN) send(); else settingsSocket.addEventListener("open", send, { once: true });
    };
    const makeWindow2 = el("button", { textContent: role === "LEFT" ? "Hold Window 2" : "To Window 2",
      title: "Make Window 2: put this stream in Window 2 and keep it there for a while: no swap moves Window 2 meanwhile (the hold time is in Settings)" });
    makeWindow2.onclick = () => {
      connectSettings();
      const send = () => settingsSend({ type: "force_window2", tab_id: Number(tabId) });
      if (settingsSocket.readyState === WebSocket.OPEN) send(); else settingsSocket.addEventListener("open", send, { once: true });
    };
    const untrack = el("button", { textContent: "Untrack" });
    untrack.onclick = () => {
      chrome.runtime.sendMessage({ type: "untrack", tabId: Number(tabId) });
      block.remove();
    };

    const title = el("span", { className: "title grow", textContent: info.title, title: info.title });
    const block = el("div", { className: "tab" },
      el("div", { className: "row" }, title, el("span", { className: "role", textContent: ROLE_NAMES[role] || "-" }), status, makeMain, makeWindow2, untrack),
      el("div", { className: "row" }, sport, teams, weight),
      slotRow);
    list.append(block);
  }

  // Priorities editor.
  const sports = $("sports");
  sports.replaceChildren();
  for (const [name, w] of Object.entries(priorities.sports)) {
    const input = el("input", { type: "number", min: "0", step: "0.1", value: w });
    input.onchange = () => update("priorities", DEFAULT_PRIORITIES, (p) => (p.sports[name] = Number(input.value) || 0));
    const remove = el("button", { textContent: "x" });
    remove.onclick = async () => { await update("priorities", DEFAULT_PRIORITIES, (p) => delete p.sports[name]); load(); };
    sports.append(el("div", { className: "row" }, el("span", { className: "grow", textContent: name }), input, remove));
  }

  const teamsBox = $("teams");
  teamsBox.replaceChildren();
  priorities.teams.forEach((team, i) => {
    const input = el("input", { type: "number", min: "0", step: "0.1", value: team.mult });
    input.onchange = () => update("priorities", DEFAULT_PRIORITIES, (p) => (p.teams[i].mult = Number(input.value) || 0));
    const remove = el("button", { textContent: "x" });
    remove.onclick = async () => { await update("priorities", DEFAULT_PRIORITIES, (p) => p.teams.splice(i, 1)); load(); };
    const slotsNow = team.slots ?? SLOTS.map(([role]) => role);
    const boxes = SLOTS.map(([role]) => el("input", { type: "checkbox", checked: slotsNow.includes(role) }));
    boxes.forEach((box) => (box.onchange = () => update("priorities", DEFAULT_PRIORITIES, (p) => {
      const chosen = SLOTS.filter((_, k) => boxes[k].checked).map(([role]) => role);
      if (chosen.length === SLOTS.length || !chosen.length) delete p.teams[i].slots; else p.teams[i].slots = chosen; // none ticked = no restriction
    })));
    teamsBox.append(el("div", { className: "row" }, el("span", { className: "grow", textContent: team.name }), input, remove),
      el("div", { className: "row slots" }, ...SLOTS.map(([, label], k) => el("label", {}, boxes[k], label))));
  });

  $("hint").textContent =
    "Weight = tab weight x sport weight. Favourite teams add a steady bonus. Status Auto is detected from the picture; Live / Break / Over overrides it (Break counts 0.25, Over 0).";
}

$("addSport").onclick = async () => {
  const name = $("newSport").value.trim();
  if (!name) return;
  await update("priorities", DEFAULT_PRIORITIES, (p) => (p.sports[name] ??= 1));
  $("newSport").value = "";
  load();
};

$("addTeam").onclick = async () => {
  const name = $("newTeam").value.trim();
  if (!name) return;
  await update("priorities", DEFAULT_PRIORITIES, (p) => p.teams.push({ name, mult: Number($("newTeamMult").value) || 1 }));
  $("newTeam").value = "";
  load();
};

$("freeze").onclick = async () => {
  chrome.runtime.sendMessage({ type: "toggle_freeze" });
  setTimeout(showFreeze, 300);
};

$("freeze2").onclick = async () => {
  chrome.runtime.sendMessage({ type: "toggle_freeze2" });
  setTimeout(showFreeze2, 300);
};

$("reload").onclick = () => chrome.runtime.reload();

// ---- Settings: the daemon describes every setting; this page only draws the form and sends edits. ----
let settingsSocket = null;   // the popup's own connection to the daemon (settings and Make Main)
const trackedTitles = {};    // tab id -> title, to name the held stream
const settingsOpenGroups = new Set(); // group titles the user has opened, kept across redraws

function settingsSend(message) {
  if (settingsSocket && settingsSocket.readyState === WebSocket.OPEN) settingsSocket.send(JSON.stringify(message));
}

function connectSettings() {
  if (settingsSocket && settingsSocket.readyState <= WebSocket.OPEN) return;
  $("settingsStatus").textContent = "Connecting to the daemon...";
  settingsSocket = new WebSocket("ws://127.0.0.1:8765");
  settingsSocket.onopen = () => {
    settingsSend({ type: "get_hold" });
    settingsSend({ type: "get_hold2" });
    if ($("settingsBox").open) settingsSend({ type: "get_settings" });
  };
  settingsSocket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "settings") drawSettings(msg);
    if (msg.type === "hold") drawHold(msg);
    if (msg.type === "hold2") drawHold(msg, "hold2", "Window 2");
  };
  settingsSocket.onclose = () => {
    $("settingsStatus").textContent = "The daemon is not running (start it with scripts\\start_all.ps1), or it just stopped.";
    $("settingsGroups").replaceChildren();
    $("settingsReset").hidden = true;
  };
}

function drawSettings(msg) {
  if (msg.error) {
    $("settingsStatus").textContent = msg.error;
    return;
  }
  const changedCount = msg.groups.reduce((n, g) => n + g.items.filter((i) => i.changed).length, 0);
  $("settingsStatus").textContent = changedCount
    ? `${changedCount} setting(s) changed from the defaults (shown in bold). Edits apply at once.`
    : "All settings are at their defaults. Edits apply at once and are kept across restarts.";
  const box = $("settingsGroups");
  box.replaceChildren();
  for (const group of msg.groups) {
    const details = el("details", { open: settingsOpenGroups.has(group.title) }, el("summary", { textContent: group.title }));
    details.ontoggle = () => (details.open ? settingsOpenGroups.add(group.title) : settingsOpenGroups.delete(group.title));
    for (const item of group.items) details.append(settingRow(item, msg.errors?.[item.id]));
    box.append(details);
  }
  $("settingsReset").hidden = false;
}

function settingRow(item, error) {
  const send = (value) => settingsSend({ type: "set_settings", changes: { [item.id]: value } });
  let input;
  if (item.kind === "list") {
    input = el("textarea", { value: item.value.join("\n"), title: "One phrase per line" });
    input.onchange = () => send(input.value.split("\n").map((p) => p.trim()).filter(Boolean));
  } else {
    input = el("input", { type: "number", step: "any", value: item.value });
    input.onchange = () => (input.value === "" ? send(item.default) : send(Number(input.value)));
  }
  const back = el("button", { textContent: "\u21ba", title: `Back to the default (${item.kind === "list" ? "phrase list" : item.default})` });
  back.hidden = !item.changed;
  back.onclick = () => send(item.default);
  const top = el("div", { className: "row" }, el("span", { className: "name", textContent: item.label }),
    ...(item.kind === "list" ? [] : [input]), back);
  const row = el("div", { className: "setting" + (item.changed ? " changed" : "") }, top,
    el("div", { className: "help", textContent: item.help }));
  if (item.kind === "list") row.append(input);
  if (error) row.append(el("div", { className: "err", textContent: `Not applied: ${error}` }));
  return row;
}

$("settingsBox").ontoggle = () => {
  if (!$("settingsBox").open) return;
  if (settingsSocket && settingsSocket.readyState === WebSocket.OPEN) settingsSend({ type: "get_settings" });
  else connectSettings();
};

// ---- Make Main / Make Window 2: put a stream there and hold it for a while (one status bar each) ----
const holdTimers = {};

function drawHold(msg, prefix = "hold", where = "Main") {
  const box = $(prefix + "Status"), text = $(prefix + "Text"), release = $(prefix + "Release");
  clearInterval(holdTimers[prefix]);
  box.classList.toggle("error", !!msg.error);
  if (msg.error) {
    text.textContent = msg.error;
    release.hidden = true;
    box.hidden = false;
    setTimeout(() => { if (box.classList.contains("error")) box.hidden = true; }, 5000);
    return;
  }
  release.hidden = false;
  if (msg.tab_id == null || msg.left_s <= 0) {
    box.hidden = true;
    return;
  }
  const ends = Date.now() + msg.left_s * 1000;
  const name = trackedTitles[msg.tab_id] ?? "a stream";
  const show = () => {
    const left = Math.max(0, Math.round((ends - Date.now()) / 1000));
    if (left === 0) { box.hidden = true; clearInterval(holdTimers[prefix]); return; }
    text.textContent = `${where} is held on "${name}" for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} more.`;
  };
  show();
  box.hidden = false;
  holdTimers[prefix] = setInterval(show, 1000);
}

$("holdRelease").onclick = () => settingsSend({ type: "release_hold" });
$("hold2Release").onclick = () => settingsSend({ type: "release_hold2" });
$("settingsReset").onclick = () => {
  if ($("settingsReset").dataset.sure === "1") {
    settingsSend({ type: "reset_settings" });
    $("settingsReset").dataset.sure = "0";
    $("settingsReset").textContent = "Reset all settings to the defaults";
  } else {
    $("settingsReset").dataset.sure = "1";
    $("settingsReset").textContent = "Click again to confirm the reset";
  }
};

load();
connectSettings();   // also tells the hold status (Make Main) as soon as the popup opens
