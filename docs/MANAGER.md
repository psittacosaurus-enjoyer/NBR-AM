# Manager 1.23

Choose your BlueStacks instance on **Instance**, open a mission's **Start Mission** screen there, then press **Start** on Dashboard. Universal handles verified Start, completion/results, rewards, Retry and natural stamina waiting. The game manages combat.

The sidebar contains exactly Dashboard, Instance and Settings. Instance discovery reads installed configuration and displays friendly names separately from internal identifiers and ADB addresses. Refresh is passive. Applying a selection requires a verified Android identity and persists it locally; a missing saved target is shown as unavailable rather than replaced by another instance. Manual Setup resolves an exact name or identifier, with an advanced explicit ADB endpoint fallback.

Selection is locked during active farming and unknown process state. Each process pins its target across reconnects and worker recovery. Changing a stopped selection keeps that instance's saved progress separate from other instances. The legacy `acc1`/`Pie64` mapping retains its existing progress, pending input and stamina state. Start, Stop and Restart remain available through the existing command-line interface.

Closing the manager leaves the independent supervisor and loot collector running. Reopening restores process status, elapsed time and persisted loot without issuing a farming command. See [installation and controls](../README.md).

## Appearance and optional details

Fresh settings use **Original**. **Settings → Appearance** also offers **Retro 98**. Both appearances share the same controls and loot display.

**Settings → Activity log** shows controller events and manager errors. **Settings → Recognition details** shows observed/completed/failed recognition counts, quantity evidence, historical sessions, run history, warnings and retained screenshots. Both settings start off and persist independently of appearance. Changing appearance or visibility does not command the controller.

Loot is always visible without expanding a menu. Each tile uses artwork from the matched Game Asset Library identity and overlays its verified acquired total. `?` means the quantity is unknown; `7+` means seven verified acquired with additional quantities still unknown. Hover for evidence and the full item name. Missing or unusable artwork has an explicit placeholder.

Artwork resolves by exact record ID and explicit aliases, honoring preferred eligible assets. Item names and similar images never establish identity. Library lookup runs with the background loot read and does not modify the library.

## Sessions and evidence

Loot sessions belong to a supervisor PID plus creation time, or a direct worker identity. They survive worker reconnects and GUI reopening; a new farming process gets fresh totals. Recognition details provides historical sessions and pages of 100 runs. Double-click a retained screenshot row to open it. A stale collector heartbeat reports Tracking Unavailable.

Observed Icons counts verified acquired-grid identities. Confirmed Acquired counts only explicit acquired quantities. Rarity stars, Lucky Drop labels, repeated icons, Ryo and Completion Reward Conditions never imply an acquired amount. Unknown quantities stay Unknown; partial confirmed totals are labeled **known only** in the details table.

Only newly verified MISSION_COMPLETE runs emit durable completion events. READY_TO_RETRY saves the results frame already captured by the worker. A background collector uses read-only recognition/library APIs; unique completion IDs prevent retries or reopening from increasing totals. Records live in `runtime/loot/loot.sqlite3`, with pending events/evidence in `runtime/loot/pending/` and diagnostic screenshots in `runtime/loot/diagnostics/`.

Recognition has eight pending jobs and bounded memory. Overload records a failure with evidence. Persistence failures retain events/evidence and retry up to three times; `runtime/loot/persistence_error.json` reports unresolved errors. Farming continues when collection fails.

Collectors reload changed library/calibration revisions before new recognition jobs. Existing history is not reprocessed. Deploying changed Python code requires a worker restart; reopening the manager only rereads persisted records. See [recognition details](RECOGNITION.md) for calibration and coverage limits.

## Stop, stamina and restart

**Stop** persists an account-wide stop and allows a bounded game operation to finish. Duplicate starts are guarded. Device mapping and Android identity are verified before inputs and after reconnection. Two fresh agreeing observations gate inputs; unknown screens and unverified dialogs receive no guessed input. Detected loss stops farming and retains evidence for manual resolution.

Saved pending inputs and natural stamina deadlines remain protected across upgrades. If an unresolved saved input prevents starting, resolve the game state manually. Restore a supported Start Mission screen after reaching an unsupported menu.

Stamina shows WAITING, READY or UNKNOWN and a countdown from the worker's persisted deadline. READY means the delay has elapsed; fresh game checks still gate farming. Choose 5, 10, 15, 20 or 25 minutes. The initial duration is ten minutes; unsupported saved values fall back to five. Changes affect subsequent waits.

**End wait** interrupts a verified live wait with no pending input and requests fresh game/stamina checks. Insufficient stamina starts another persisted wait. Requests for another worker or a replaced deadline are rejected.

**Restart** safely stops and confirms all recorded controller processes have exited before reconnecting, verifying identity and launching Universal. It preserves pending input latches, counters, natural stamina deadlines, loot history and logs. A concurrent Stop cancels relaunch; failure to confirm shutdown launches nothing. BlueStacks remains open. Each step is recorded in the optional activity log.

CLI equivalents are `start`, `stop`, `restart`, `end-stamina-wait`, and `stamina-duration --minutes 5|10|15|20|25`.
