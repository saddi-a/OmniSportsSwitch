// Shared by the service worker (importScripts) and the popup (script tag).
const DEFAULT_PRIORITIES = {
  sports: { "Football": 1, "American football": 1, "Basketball": 1, "Tennis": 1, "Hockey": 1, "Rugby": 1, "Motorsport": 1, "Other": 1 },
  teams: [], // [{ name: "Paris SG", mult: 2 }]
};

// Quick per-tab status: a game on a long break counts less, a finished game cannot win a swap.
const STATUS_MULT = { live: 1, break: 0.25, over: 0 };
