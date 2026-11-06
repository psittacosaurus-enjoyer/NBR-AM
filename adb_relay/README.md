# NBR ADB Relay

A small, standalone, **read-only** bridge to one BlueStacks instance. It lets a
process that can only see files (such as an AI workspace) take screenshots and
read device state without controlling the PC.

It is independent of the NBR Automation Manager: standard-library Python only,
it imports nothing from NBR, and it reads and writes only inside this folder.

## Use
1. `relay.local.json` holds the ADB path, device serial and the expected
   Android ID (copy `relay.example.json` to create it).
2. Double-click **Start ADB Relay.cmd** (opens a minimized window).
3. Stop with **Stop ADB Relay.cmd** or by closing that window.

`status.json` shows whether it is running (heartbeat every 2 s).

## Protocol
Write `requests/<id>.json` (write to a temporary name, then rename) containing
`{"command": "<name>", ...options}`. The relay answers in
`responses/<id>.json`; screenshots are saved as `responses/<id>.png`.
Responses are deleted after 15 minutes.

`python client.py <command> [key=value ...]` does this for you.

| Command | Result |
| --- | --- |
| `ping` | relay alive (no ADB call) |
| `devices` | `adb devices -l` |
| `identity` | re-verifies the Android ID |
| `foreground` | resumed package/activity |
| `screencap` | PNG of the screen + capture time |
| `window_size` | `wm size` |
| `getprop` | `key=<property name>` |
| `timing` | `count=1..30` repeated raw/PNG screencap, foreground and identity latency |

## Safety
* No input of any kind: there is no tap, swipe, key, text, install or generic
  shell command, and every operation builds its own fixed argument list.
* Every device command first confirms the configured Android ID (re-checked at
  least every 20 s); a different device is refused.
* One relay per folder (`relay.lock`).
