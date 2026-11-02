"""Stage 15 policy layered over Ninja Road's existing input and boss guards."""
from __future__ import annotations

import time

from automation import controller as farm
from automation.ninja_boss_swap import BossSwap
from automation.ninja_strategy_vision import Stage15Vision


class Stage15Strategy:
    TEAMS = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 7: 2, 8: 1, 9: 1,
             11: 3, 12: 3, 13: 1}
    SEQUENCES = {7, 9, 12, 13}
    COMBAT = {"NR_COMBAT", "NR_COMBAT_AUTO_OFF", "NR_COMBAT_NINJUTSU_OFF"}
    END = {"NR_TEAM_DEFEATED", "NR_ALL_DEFEATED", "MISSION_COMPLETE", "NR_RESULTS",
           "NR_RETURN", "NR_HOME", "NR_ENTRY", "NR_START"}
    WAIT_LIMIT = 120

    def __init__(self, engine):
        self.engine = engine
        self.vision = Stage15Vision(engine)
        if not hasattr(engine, "boss_swap_controller"):
            engine.boss_swap_controller = BossSwap(engine)
        self.boss = engine.boss_swap_controller

    @property
    def data(self):
        return self.engine.data

    def block(self, reason):
        self.data["blocked"] = "Stage 15: " + reason
        self.engine.save()
        return 2

    def dispatch(self, state, action, purpose, **metadata):
        state["pending"] = {"purpose": purpose, "time": time.time(), **metadata}
        self.engine.save()
        try:
            self.engine.device.perform(action)
        except farm.ActionNotDispatched:
            state["pending"] = None
            self.engine.save()
            raise
        state["pending"]["returned"] = True
        self.engine.save()
        return .15

    def auto(self, state, target, **metadata):
        return self.dispatch(state, {"id": "nr_auto", "kind": "tap", "point": [623, 56]},
                             "auto", target=target, **metadata)

    def settings(self, state, frame, stage, ready=False):
        desired = {"ninjutsu": stage >= 5}
        if stage > 5:  # Preserve the existing opening/Stage 5 secret-technique policy.
            desired["secret"] = stage in {10, 15}
        for setting, target in desired.items():
            observed = self.vision.setting(frame, setting)
            if observed is not None:
                self.data[setting + "_enabled"] = observed
                if observed != target:
                    return self.dispatch(state, {
                        "id": "nr_ninjutsu" if setting == "ninjutsu" else "nr_secret",
                        "kind": "tap", "point": [159 if setting == "ninjutsu" else 460, 1415]},
                        "setting", setting=setting, target=target)
                state.setdefault("settings", {})[setting] = target
            if state.get("settings", {}).get(setting) != target:
                if ready:
                    # Ready hides these controls even when Auto is ON. Enter
                    # manual combat before exposing an unknown/wrong setting.
                    if self.data.get(setting + "_enabled") is target:
                        state.setdefault("settings", {})[setting] = target
                        continue
                    if self.boss.auto(frame) is True:
                        return self.auto(state, False)
                    continue
                if self.boss.auto(frame) is False:
                    delay = self.auto(state, True)
                    # Expose and converge the actual overlay in this worker
                    # turn, minimizing the window before Auto takes an action.
                    for _ in range(5):
                        if farm.STOP.exists():
                            return delay
                        time.sleep(.15)
                        fresh = self.engine.device.capture()
                        if self.boss.auto(fresh) is not True:
                            continue
                        current = self.vision.setting(fresh, setting)
                        if current is None:
                            continue
                        state["pending"] = None
                        if current != target:
                            return self.dispatch(state, {
                                "id": "nr_ninjutsu" if setting == "ninjutsu" else "nr_secret",
                                "kind": "tap", "point": [159 if setting == "ninjutsu" else 460, 1415]},
                                "setting", setting=setting, target=target)
                        state.setdefault("settings", {})[setting] = target
                        self.data[setting + "_enabled"] = target
                        self.engine.save()
                        return .1
                    return delay
                return self.block("waiting for visible " + setting + " control")
        return None

    def confirm(self, state, frame, name, stage):
        pending = state.get("pending")
        if not pending:
            return None
        purpose = pending["purpose"]
        confirmed = False
        if purpose == "setting":
            confirmed = self.vision.setting(frame, pending["setting"]) is pending["target"]
            if confirmed:
                state.setdefault("settings", {})[pending["setting"]] = pending["target"]
                self.data[pending["setting"] + "_enabled"] = pending["target"]
        elif purpose == "auto":
            confirmed = self.boss.auto(frame) is pending["target"]
            if confirmed and pending.get("pulse"):
                return self.auto(state, False)
        elif purpose == "team":
            if name == "NR_TEAM_SELECT" and stage == pending["map"]:
                panel = self.vision.team_panel(frame)
                if not self.boss.matches(panel, self.boss.unpack(pending["before"])):
                    # Require a stable changed panel; an arrow animation alone
                    # cannot confirm which formation was selected.
                    previous = pending.get("after")
                    if previous and self.boss.matches(panel, self.boss.unpack(previous)):
                        self.data["current_team"] = pending["target"]
                        state["team"] = pending["target"]
                        state["settings"] = {}
                        self.resume_formation()
                        confirmed = True
                        farm.event("ninja_strategy_team_selected", team=pending["target"], map=stage)
                    else:
                        pending["after"] = self.boss.pack(panel)
                        self.engine.save()
        if confirmed:
            state["pending"] = None
            self.engine.save()
            return .1
        if time.time() - pending["time"] > 20:
            return self.block(purpose + " transition unverified; input remains latched")
        return .25

    def select_team(self, state, frame, name, stage):
        current = self.data.get("current_team")
        defeated = self.data.get("defeated_teams", [])
        if current in defeated:
            return None  # Established death recovery owns the next selection.
        target = self.TEAMS.get(stage, current)
        if target in defeated or target not in self.data.get("available_teams", []):
            target = current
        if current == target or target is None:
            return None
        if name != "NR_TEAM_SELECT":
            return self.block("team strategy requires a verified Ready screen")
        if current is None:
            return None  # Initial Team 1 selection belongs to the existing controller.
        teams = sorted(self.data.get("available_teams", []))
        if current not in teams:
            return self.block("current team has no verified identity")
        index = teams.index(current)
        forward = teams[(index + 1) % len(teams)]
        backward = teams[(index - 1) % len(teams)]
        step = 1 if forward == target else -1
        next_team = forward if step == 1 else backward
        if next_team in defeated:
            return self.block("team navigation would select a defeated team")
        # Arrows visit every team, including disabled teams; no combat is
        # started until the desired surviving team is visually confirmed.
        return self.dispatch(state, {"id": "nr_strategy_team", "kind": "tap",
                                     "point": [878 if step > 0 else 28, 1286]},
                             "team", target=next_team, map=stage,
                             before=self.boss.pack(self.vision.team_panel(frame)))

    def resume_formation(self):
        formations = self.data.get("stage15_formations", {})
        stored = formations.pop(str(self.data.get("current_team")), None)
        if stored:
            self.data["boss_swap"] = stored
            self.engine.save()

    def defer_formation(self, state, name, stage):
        swap = self.data.get("boss_swap")
        current = self.data.get("current_team")
        target = self.TEAMS.get(stage, current)
        if (name != "NR_TEAM_SELECT" or not swap or swap.get("pending")
                or target == current or target in self.data.get("defeated_teams", [])
                or target not in self.data.get("available_teams", [])
                or (swap.get("stage15_sequence") == stage and swap["phase"] != "restore")):
            return
        if swap["phase"] != "restore":
            swap.update(phase="restore", done=[], targets=list(swap["swapped"]), advance=False)
        self.data.setdefault("stage15_formations", {})[str(current)] = swap
        self.data.pop("boss_swap", None)
        self.engine.save()

    def start_swap(self, state, frame):
        self.data["boss_swap"] = {
            "phase": "enter", "team_key": [(self.data.get("run") or {}).get("id"), self.data.get("current_team")],
            "boss_map": self.boss.pack(self.boss.crop(frame, self.boss.config["map_region"])),
            "swapped": [], "done": [], "original": {}, "pending": None,
            "stage15_sequence": state["map"]}
        state["phase"] = "back_enter"
        state["back_usage_cache"] = {"peaks": {}, "spent": [], "identities": {}}
        self.engine.save()

    def record_back_role(self, frame, column):
        """Observe the newly verified role before the shared driver pulses it.

        Earlier swapped characters can cast while later positions are still
        being exchanged. Preserve that expenditure rather than waiting for a
        second cast after the last position has been switched.
        """
        state = self.data.get("stage15_strategy") or {}
        if state.get("phase") != "back_enter":
            return
        value = self.vision.chakra(frame, column)
        if value is None:
            return
        cache = state.setdefault("back_usage_cache", {"peaks": {}, "spent": [], "identities": {}})
        key = str(column)
        portrait = self.boss.crop(frame, self.boss.config["portraits"][column])
        if key not in cache["identities"]:
            cache["identities"][key] = self.boss.pack(portrait)
        if not self.boss.same_portrait(portrait, self.boss.unpack(cache["identities"][key])):
            return
        peak = cache["peaks"].get(key, value)
        if peak >= .45 and peak - value >= .30 and column not in cache["spent"]:
            cache["spent"].append(column)
        cache["peaks"][key] = max(peak, value)
        self.engine.save()

    def restore(self, state):
        swap = self.data.get("boss_swap")
        if swap:
            swap.update(phase="restore", done=[], targets=list(swap["swapped"]), advance=False)
            for key in ("rejected_column", "skipped_from", "advanced"):
                swap.pop(key, None)
            self.boss._turn_candidate = None
        state["phase"] = "restore"
        self.engine.save()

    def usage(self, state, frame, allow_turn=True):
        if time.time() - state["usage_since"] > self.WAIT_LIMIT or state.get("pulses", 0) >= 60:
            # An unavailable skill must never produce endless polling/swaps.
            # Let combat progress, restoring any roles we actually exchanged.
            farm.event("ninja_strategy_usage_timeout", map=state["map"], phase=state["phase"],
                       spent=state.get("spent", []))
            if self.data.get("boss_swap"):
                self.restore(state)
            else:
                state["phase"] = "done"
            self.engine.save()
            return .1
        auto = self.boss.auto(frame)
        if auto is True:
            return self.auto(state, False)
        if auto is not False:
            return .25
        peaks = state.setdefault("peaks", {})
        spent = state.setdefault("spent", [])
        identities = state.setdefault("identities", {})
        active = self.boss.settled_active(frame)
        for column in range(3):
            value = self.vision.chakra(frame, column)
            if value is None:
                continue
            key = str(column)
            portrait = self.boss.crop(frame, self.boss.config["portraits"][column])
            if key not in identities:
                identities[key] = self.boss.pack(portrait)
            if not self.boss.same_portrait(portrait, self.boss.unpack(identities[key])):
                # Death/replacement must not look like a spent skill. A
                # settled new actor aborts this sequence and restores only
                # the surviving roles whose identities remain known.
                mismatch = state.setdefault("identity_mismatches", {}).setdefault(key, time.time())
                if active is not None and time.time() - mismatch >= 2.5:
                    swap = self.data.get("boss_swap")
                    if swap:
                        swap["swapped"] = [item for item in swap["swapped"] if item != column]
                        self.restore(state)
                    else:
                        state["phase"] = "done"
                    self.engine.save()
                    return .1
                continue
            state.get("identity_mismatches", {}).pop(key, None)
            peak = peaks.get(key, value)
            # Read a decrease in this role's own gauge. An initially empty
            # gauge or merely entering an active turn does not prove usage.
            if peak >= .45 and peak - value >= .30 and column not in spent:
                spent.append(column)
            peaks[key] = max(peak, value)
        if len(spent) == 3:
            if state["phase"] == "front_usage":
                self.start_swap(state, frame)
            else:
                self.restore(state)
            self.engine.save()
            return .1
        if not allow_turn:
            self.engine.save()
            return None
        column = active
        if column is None:
            return .25
        # The existing boss driver uses the same ON/OFF pulse to let one actor
        # finish a turn while retaining access to the unobscured gauges.
        previous = state.get("actor")
        if previous == column and time.time() - state.get("pulse_at", 0) < 12:
            return .25
        state.update(actor=column, pulse_at=time.time(), pulses=state.get("pulses", 0) + 1)
        self.engine.save()
        delay = self.auto(state, True, pulse=True)
        for _ in range(5):
            if farm.STOP.exists():
                break
            time.sleep(.15)
            if self.boss.auto(self.engine.device.capture()) is True:
                return self.auto(state, False)
        return delay

    def tick(self, frame, name, spec):
        if name in self.END:
            self.data.pop("stage15_strategy", None)
            if name == "NR_TEAM_DEFEATED":
                self.data.get("stage15_formations", {}).pop(str(self.data.get("current_team")), None)
            else:
                self.data.pop("stage15_formations", None)
            return None
        ready = name in self.engine.READY_SCREENS
        if not ready and name not in self.COMBAT:
            return None
        if self.data.get("pending") or self.data.get("connection_pending"):
            return None
        observed_map = (spec or {}).get("ready_map_index" if ready else "combat_map_index")
        if type(observed_map) is int:
            self.data["stage15_map"] = observed_map
        stage = self.data.get("stage15_map")
        if stage is None or (ready and observed_map is None):
            return self.block("waiting for an unambiguous map counter")
        if stage == 16:
            # Never turn Auto ON or enter combat on the retreat map. If its
            # overlay is already visible, disable boss secrets before leaving.
            state = self.data.get("stage15_strategy")
            if self.boss.auto(frame) is True and self.vision.setting(frame, "secret") is True:
                if not state:
                    state = {"map": 16, "pending": None}
                    self.data["stage15_strategy"] = state
                if state.get("pending"):
                    return self.confirm(state, frame, name, stage)
                return self.dispatch(state, {"id": "nr_secret", "kind": "tap", "point": [460, 1415]},
                                     "setting", setting="secret", target=False)
            if not ready:
                if self.boss.auto(frame) is True:
                    if not state:
                        state = {"map": 16, "pending": None}
                        self.data["stage15_strategy"] = state
                    if state.get("pending"):
                        return self.confirm(state, frame, name, stage)
                    return self.auto(state, False)
                return .25  # Await Ready/retreat; never fall through to Auto ON.
            self.data.pop("stage15_strategy", None)
            self.engine.save()
            return None
        run_id = (self.data.get("run") or {}).get("id")
        state = self.data.get("stage15_strategy")
        if not state or state.get("run_id") != run_id:
            state = {"run_id": run_id, "map": stage, "team": self.data.get("current_team"),
                     "phase": "setup", "pending": None, "settings": {}}
            self.data["stage15_strategy"] = state
            self.resume_formation()
        if stage != state["map"]:
            # Map progression aborts unfinished usage, but the shared boss
            # driver retains any role restoration that is still necessary.
            if state.get("pending") and state["pending"]["purpose"] not in {"auto", "setting"}:
                return self.block("map changed during an unverified team selection")
            if state.get("pending") and state["pending"].get("returned"):
                # A verified new Ready screen supersedes completed toggle
                # commands. Reobserve their actual state under the new policy.
                state["pending"] = None
            swap = self.data.get("boss_swap")
            if swap and swap.get("stage15_sequence") and swap["phase"] != "restore" and not swap.get("pending"):
                self.restore(state)
            state.update(map=stage, phase="setup", settings={})
            for key in ("spent", "peaks", "actor", "usage_since", "pulses", "identities", "identity_mismatches", "back_usage_cache"):
                state.pop(key, None)
        if self.data.get("current_team") in self.data.get("defeated_teams", []):
            self.data.pop("stage15_strategy", None)
            return None
        if state.get("team") != self.data.get("current_team"):
            state.update(team=self.data.get("current_team"), phase="setup", settings={}, pending=None)
            for key in ("spent", "peaks", "actor", "usage_since", "pulses", "identities", "identity_mismatches", "back_usage_cache"):
                state.pop(key, None)
            self.resume_formation()
        delay = self.confirm(state, frame, name, stage)
        if delay is not None:
            return delay
        if state["phase"] == "back_enter":
            for column in (self.data.get("boss_swap") or {}).get("swapped", []):
                self.record_back_role(frame, column)
        if (self.data.get("boss_swap") or {}).get("pending"):
            return None  # Never race the shared driver's latched Auto/swipe input.
        self.defer_formation(state, name, stage)
        if stage > 5 and stage not in {10, 15} and self.vision.setting(frame, "secret") is True:
            return self.dispatch(state, {"id": "nr_secret", "kind": "tap", "point": [460, 1415]},
                                 "setting", setting="secret", target=False)
        # Finish an inherited boss/role restoration before any new strategy.
        swap = self.data.get("boss_swap")
        if swap and (swap.get("stage15_sequence") != stage or swap["phase"] == "restore"):
            delay = self.settings(state, frame, stage, ready=ready)
            return delay  # None hands control back to BossSwap.
        delay = self.select_team(state, frame, name, stage)
        if delay is not None:
            return delay
        delay = self.settings(state, frame, stage, ready=ready)
        if delay is not None:
            return delay
        if stage >= 16:
            self.engine.save()
            return None  # Existing guarded retreat runs before any Ready input.
        if stage not in self.SEQUENCES:
            self.engine.save()
            return None
        if state["phase"] == "setup":
            state.update(phase="front_usage" if stage == 7 else "back_enter",
                         usage_since=time.time(), spent=[], peaks={}, pulses=0)
            if stage != 7:
                self.start_swap(state, frame)
        if state["phase"] == "back_enter":
            swap = self.data.get("boss_swap")
            if swap and swap["phase"] == "boss" and not swap.get("pending"):
                state.update(phase="back_usage", usage_since=time.time(), spent=[], peaks={}, pulses=0)
                state.pop("actor", None)
                state.pop("identities", None)
                state.pop("identity_mismatches", None)
                state.update(state.pop("back_usage_cache", {}))
            else:
                return None  # Reuse verified per-turn swaps and auto pulses.
        if state["phase"] in {"front_usage", "back_usage"}:
            self.engine.save()
            if ready:
                return self.usage(state, frame, allow_turn=False)
            return self.usage(state, frame)
        if state["phase"] == "restore" and not self.data.get("boss_swap"):
            state["phase"] = "done"
        self.engine.save()
        return None
