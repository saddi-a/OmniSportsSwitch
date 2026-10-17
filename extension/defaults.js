// Shared by the service worker (importScripts) and the popup (script tag).
const DEFAULT_PRIORITIES = {
  sports: { "Football": 1, "American football": 1, "Other": 1 },
  teams: [], // [{ name: "Paris SG", mult: 2 }]
};

