# extension/ (guide for Claude)

Edge Manifest V3 extension in plain JavaScript, **no build step, no npm**. It does the things only a browser can do and leaves every decision to the daemon.

## Files

| File | Job |
|---|---|
| `manifest.json` | Permissions (tabCapture, offscreen, activeTab, tabs, storage, scripting, debugger), keyboard commands (`Ctrl+Shift+P` freeze, `Ctrl+Shift+Y` track). |
| `background.js` | Service worker. **Stateless**: Edge can stop it at any time, so all state is in `chrome.storage.local` (tracked tabs, weights, priorities, window roles, layout). Tracks tabs, remembers window roles by window position, moves tabs between windows (`chrome.tabs.move`, keeping a spare `about:blank` tab so windows never close), injects the video reporter, performs `arrange` and `focus_tile` commands from the daemon. |
| `offscreen.html` / `offscreen.js` | Long-lived page: per-tab audio capture (tabCapture), 100 ms PCM chunks to the daemon over WebSocket, mutes everything except `MAIN` (gain nodes), registers tabs with the daemon (role, weight, sport, status, allowed slots, teams, team rules). |
| `pcm-processor.js` | AudioWorklet that turns audio blocks into 32 kHz mono samples. |
| `popup.html` / `popup.js` | The control panel: window role, freeze, track, per-tab settings, sport and team priorities (with allowed slots). Only edits storage; the service worker pushes the result to the daemon. |
| Settings section of `popup.js` | Opens its own WebSocket to the daemon, asks for `get_settings`, draws the form from the reply, and sends `set_settings` / `reset_settings`. It knows no setting by name; the daemon describes them (`daemon/settings.py` `LAYOUT`). |
| `defaults.js` | `DEFAULT_PRIORITIES`, shared by the service worker and the popup. |

## Things to know

- **Page information.** Every 5 s the video reporter (`installVideoReporter` in `background.js`) also sends `info` with the video-area message: `document.title`, the media-session metadata, and up to 12 short visible texts near the top of the playing video (the player header). `offscreen.js` forwards it, `Server._page_info` stores it (`logs`: "page info tab ..."); it feeds the feed mapper when the popup has no teams, and `sport_from_title` reads its league words. Only strings of at most 120 characters leave the page, and only to the local daemon. Needs a **Reload extension** to take effect.
- **Reloading the extension drops all tracking** (Edge requires a user click to start tab capture). The user must press "Track this tab" on every stream tab again. Tell them before asking for a reload, and never reload just to fix one tab (use `Ctrl+Shift+Y`).
- `chrome.tabCapture.getMediaStreamId` needs a user gesture (a click or a keyboard command counts).
- The page canvas cannot be captured (DRM). The daemon reads the screen instead, so the extension reports where the video sits on screen (`video-rect`) and nothing may cover it.
- DAZN multiview: scripted clicks only *select* a tile. A genuine expand needs real mouse events through `chrome.debugger` (`Input.dispatchMouseEvent`): move, wait, a second small move, then press and release. The daemon checks where the sound went to confirm the expand.
- Only the popup, the offscreen document and the service worker may talk to each other with `chrome.runtime.sendMessage`; the service worker broadcasts `state` (roles, weights, labels, team rules) to the offscreen document, which re-registers tabs with the daemon whenever something relevant changed.
- No external scripts or CDNs. Keep it dependency-free.
