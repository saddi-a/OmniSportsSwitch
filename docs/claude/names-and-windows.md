# Window names

Windows are shown to the user as Main, Window 2, 3, 4, 5. The internal ids stay `MAIN`, `LEFT`, `RIGHT_BOT`, `RIGHT_MID`, `RIGHT_TOP` (in this order of filling) because they are saved in the browser and used by the daemon, arrange and the tests.

| User-facing | Internal id |
|---|---|
| Main | `MAIN` |
| Window 2 | `LEFT` |
| Window 3 | `RIGHT_BOT` |
| Window 4 | `RIGHT_MID` |
| Window 5 | `RIGHT_TOP` |

The labels live in `ROLE_NAMES` in `extension/popup.js`. Use the user-facing names when talking to the user.
