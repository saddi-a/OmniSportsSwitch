const ROLES = ["MAIN", "LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"];
const $ = (id) => document.getElementById(id);

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

async function load() {
  const { tracked = {}, weights = {}, windowRoles = {}, priorities = DEFAULT_PRIORITIES } =
    await chrome.storage.local.get();
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });

  // Role of the window this popup was opened from.
  const select = $("windowRole");
  select.replaceChildren(new Option("(none)", ""));
  for (const r of ROLES) select.append(new Option(r, r));
  select.value = windowRoles[active.windowId] ?? "";
  select.onchange = async () => {
    await update("windowRoles", {}, (current) => {
      // A role belongs to one window at a time, so take it away from whichever window had it.
      if (select.value) for (const [id, r] of Object.entries(current)) if (r === select.value) delete current[id];
      if (select.value) current[active.windowId] = select.value; else delete current[active.windowId];
    });
    load();
  };

  $("track").hidden = String(active.id) in tracked;
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

    const SLOTS = [["MAIN", "Main"], ["LEFT", "Left"], ["RIGHT_TOP", "R-top"], ["RIGHT_MID", "R-mid"], ["RIGHT_BOT", "R-bot"]];
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

    const untrack = el("button", { textContent: "Untrack" });
    untrack.onclick = () => {
      chrome.runtime.sendMessage({ type: "untrack", tabId: Number(tabId) });
      block.remove();
    };

    const title = el("span", { className: "title grow", textContent: info.title, title: info.title });
    const block = el("div", { className: "tab" },
      el("div", { className: "row" }, title, el("span", { className: "role", textContent: role || "-" }), status, untrack),
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
    teamsBox.append(el("div", { className: "row" }, el("span", { className: "grow", textContent: team.name }), input, remove));
  });

  $("hint").textContent =
    "Final weight = tab weight x sport weight x favourite-team bonus. Status Auto is detected from the picture; Live / Break / Over overrides it (Break counts 0.25, Over 0).";
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

$("reload").onclick = () => chrome.runtime.reload();

load();
