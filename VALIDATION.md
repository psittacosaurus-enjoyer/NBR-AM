# Verification and current blocker — 2026-10-11

Implementation is incomplete. The background runner is independent and the normal results/start/combat/results path was exercised once, but complete unattended farming and crash recovery have not been established.

## Verified live

Direct-rewards transition update: farming stalled with a persisted completion-advance latch while the screen was already recognized as `REWARD_REVEAL`. The expected completion successors omitted that known screen. The catalog now includes it, and the engine also accepts it for old persisted completion latches. Twenty-five offline tests pass, including resolving the old latch and revealing rewards exactly once. No app reset or change to spending/loss restrictions was made.

Resumed-mission latch update: an old `title_start` latch was found while the game displayed Mission Complete. The runner now accepts two agreeing observations of battle or completion as recovery specifically from that title action. It creates an observation-based recovered-run record when needed, preserving loss monitoring. Other pending actions retain their original gating; a battle frame cannot clear a completion-advance latch. Twenty-three offline tests pass. The worker was restarted with this change without resetting the game.

- The rank 72 → 73 results popup saying "Your stamina has been fully restored" was captured and registered with its exact message, OK button, and All Scrolls [A] title. The independent runner closed it once, confirmed transition to results, handled rewards and Retry, and started the next mission. This was acknowledgement of an automatic rank-up reward; no stamina-restoration option was selected. Twenty offline tests now pass, including exact popup recognition, one-click gating, and rejection when its message is missing.

- acc1/Pie64 mapping, ADB serial `127.0.0.1:5555`, and Android identity agree. Every input targets this serial.
- One All Scrolls [A] mission started with the existing team and automatic combat configuration, completed, advanced through Next and reward reveal, and returned to Retry.
- The recorded start-to-observed-completion duration was approximately 220 seconds, including development observation delay.
- Pearl balance remained 359 in the inspected menu/start screens. Normal mission entry consumed 15 natural stamina; no restoration, purchase, revive, inventory clearing, or combat-setting actions occurred.
- Both reward-reference variants classify correctly. Completion detection was checked against multiple glowing animation frames.
- The supervisor survived termination of the launching shell. Its process ancestry leads to Windows WMI, independently of Codex. Duplicate start prevention and clean stop/resume worked.
- An intentionally terminated worker was restarted by the supervisor with state retained.
- A controlled game closure outside battle was detected and the game relaunched. Title, News close, home carousel, Emergency Missions entry, list scrolling, and a naturally occurring communication-error dismissal worked.
- Target-banner location follows the matching title as list position changes. List motion must settle before a tap.
- A physical BlueStacks touch test reported cooked coordinates approximately `(450.989, 982.031)` for the intended `(451, 982)`, with a single active pointer and primary button. The touch was released afterward. No privileges or settings were changed.

## Current blocker

Update after user manual resumption: the game was observed in automatic battle. The obsolete banner-input latch was cleared under supervision, and the independent runner restarted. The manually resumed mission completed; completion, Next, reward reveal, Retry, and Start Mission all transitioned automatically into another All Scrolls [A] battle. The manually resumed run is explicitly marked `manual_resume`, with duration measured from first observation rather than actual mission entry. Forced application resets have been removed at the user's request; the revised fifteen-check safety suite passes, including stalled-menu preservation. The historical launch counter predates this change.

The following runner-started mission also completed automatically and returned through rewards to Retry. Cumulative observed completions reached three (two runner-started missions across development and one manually resumed mission). The ten-mission and sixty-minute targets remain unverified; fresh-menu recovery and actual stamina/defeat dialogs remain incomplete.

The All Scrolls mission banner does not open rank selection through the tested inputs. Synthetic touch, held touch, mouse-source input, and verified physical touch did not produce the expected transition. Correct delivery of physical coordinates was confirmed, but the cause of the game's behavior is not established.

The unresolved action remains latched. Repeated clicking and repeated application relaunches for this banner are disabled in normal operation. The background process continues observing and can resume from a supported safe state.

The user subsequently authorized trying multiple taps on the center, scroll artwork, and text. With the background worker stopped, supervised development issued 18 taps: three individual taps followed by five taps each on the center, artwork, and lower title. Every tap required a fresh recognized banner screenshot; each group was followed by a screenshot. All remained on the same banner. This bounded experiment did not establish a reliable interaction, so repeated taps have not been added to normal operation. The development helper is `development/banner_probe.py` and is never invoked by the runner.

Native acc1 window capture was also unavailable: the computer-use helper reported `window capture timed out: timed out waiting on channel` after refreshing/raising the selected window. ADB screenshots remain available.

## Offline checks

Fifteen small safety checks passed: reference variants, wrong-mission rejection, dim-overlay rejection, completion frames, persistent action latch, transition gating, unknown-state resume, passive long battles, relocated banner coordinates, stalled-menu preservation, passive holding at the stalled target banner, no unknown-screen reset, persisted stamina deadline, stop prevention, wrong-device identity, and deterministic fixed-tap script contents.

These tests do not prove that unobserved dialogs are handled correctly.

## Not yet supported or verified live

Loss-policy update: recognized defeat/revival screens and two agreeing local Windows OCR reads of explicit failure or Pearl revival/continue text now persist STOP, save evidence, mark the active run failed, and launch a local Windows alert. No decline, exit, or revive input is made. Nineteen offline tests pass, including persistent loss stop, one notification per stop, unconfirmed OCR rejection, revival no-input handling, and rejection of success/reward/stamina text. Windows OCR was exercised against a real battle screenshot without classifying it as a loss and successfully recognized the generated text-only fixture `development/ocr_loss_text_fixture.png`. This fixture is not a game defeat screenshot. Actual defeat text recognition and visible Windows alert delivery have not been verified against a natural loss; unreadable unknown dialogs remain safely observed. The independent runner was resumed with the new policy.

- Rank A, Solo Mission, and first-helper selection after a fresh launch: state/action interfaces exist, but real screen patterns have not been captured because banner entry is blocked.
- Insufficient-stamina popup: the persistent 900-second waiting mechanism is implemented and checked offline; actual popup detection and dismissal are not registered yet. It will be observation-only.
- Defeat/revival popup: actual crop patterns are not registered yet. The stop-only OCR guard may recognize explicit failure/revival text; otherwise it remains observation-only. The runner will never select revival or automatically decline/exit after detected loss.
- Additional popups, unverified rank-up variations, and inventory limits remain observation-only unless explicitly registered from inspected evidence. The observed rank-up stamina-restored acknowledgement is supported.
- Ten completed missions and a continuous sixty-minute farming soak were not achieved. Extended development/navigation observation is not counted as a successful farming soak.

## Next implementation step

Resolve the manual mission-banner interaction, capture the resulting rank/Solo/helper screens, and verify recovery back into All Scrolls [A]. Then validate successive automatic cycles and add stamina/defeat patterns when those screens occur naturally. Retain the spending restrictions throughout.
