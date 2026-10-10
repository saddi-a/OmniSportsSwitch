const ROLES = ["MAIN", "LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"];
const $ = (id) => document.getElementById(id);

async function load() {
  const { tracked = {}, weights = {}, windowRoles = {} } = await chrome.storage.local.get();
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });

  // Role of the window this popup was opened from.
  const select = $("windowRole");
  select.replaceChildren(new Option("(none)", ""));
  for (const r of ROLES) select.append(new Option(r, r));
  select.value = windowRoles[active.windowId] ?? "";
  select.onchange = async () => {
    const current = (await chrome.storage.local.get({ windowRoles: {} })).windowRoles;
    // A role belongs to one window at a time, so take it away from whichever window had it.
    if (select.value) for (const [id, r] of Object.entries(current)) if (r === select.value) delete current[id];
    if (select.value) current[active.windowId] = select.value; else delete current[active.windowId];
    await chrome.storage.local.set({ windowRoles: current });
    load();
  };

  $("track").hidden = String(active.id) in tracked;
  $("track").onclick = () => {
    chrome.runtime.sendMessage({ type: "track", tabId: active.id });
    window.close();
  };

  const list = $("list");
  list.replaceChildren();
  for (const [tabId, info] of Object.entries(tracked)) {
    let role = "";
    try {
      role = windowRoles[(await chrome.tabs.get(Number(tabId))).windowId] ?? "";
    } catch {
      continue; // tab no longer exists
    }

    const row = document.createElement("div");
    row.className = "row";

    const title = document.createElement("span");
    title.className = "title";
    title.textContent = info.title;
    title.title = info.title;

    const roleLabel = document.createElement("span");
    roleLabel.textContent = role || "-";

    const weight = document.createElement("input");
    weight.type = "number";
    weight.min = "0";
    weight.step = "0.1";
    weight.value = weights[tabId] ?? 1;
    weight.title = "Priority weight";
    weight.onchange = async () => {
      const current = (await chrome.storage.local.get({ weights: {} })).weights;
      current[tabId] = Number(weight.value) || 0;
      await chrome.storage.local.set({ weights: current });
    };

    const untrack = document.createElement("button");
    untrack.textContent = "Untrack";
    untrack.onclick = () => {
      chrome.runtime.sendMessage({ type: "untrack", tabId: Number(tabId) });
      row.remove();
    };

    row.append(title, roleLabel, weight, untrack);
    list.append(row);
  }

  $("hint").textContent = "The role belongs to the window (screen slot). Weight scales a stream's excitement score.";
}

$("reload").onclick = () => chrome.runtime.reload();

load();
