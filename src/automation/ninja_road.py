"""Ninja Road's visually guarded, independent attempt and team loop."""
from __future__ import annotations

import copy
import json
import time
import uuid

from automation import controller as farm


class NinjaRoadEngine(farm.Engine):
    """Reuse device ownership and persistence without universal mission behavior."""

    TRANSITION_TIMEOUT = 90
    UNKNOWN_TIMEOUT = 180
    CONNECTION_LIMIT = 3
    CONNECTION_MENUS = {"NR_START", "NR_ENTRY", "NR_HOME", "NR_AVAILABLE", "TITLE_START", "NR_TITLE_START"}
    CONNECTION_COMBAT = {"NR_COMBAT", "NR_COMBAT_AUTO_OFF", "NR_COMBAT_NINJUTSU_OFF"}
    READY_SCREENS = {"NR_TEAM_SELECT", "NR_EARLY_EXIT", "NR_EARLY_EXIT_PREPARE"}

    def __init__(self, stage="ninja_road", instance=None):
        if stage != "ninja_road":
            raise ValueError("Ninja Road engine requires the Ninja Road stage")
        super().__init__(stage, instance)
        self.data.setdefault("current_team", None)
        self.data.setdefault("defeated_teams", [])
        self.data.setdefault("available_teams", [])
        self.data["counts"].setdefault("resets", 0)

    def read_ready_map(self, frame):
        config_path = farm.ROOT / "assets/patterns/ninja_road/map_progress.json"
        cached = getattr(self, "_ready_map_cache", None)
        if cached and cached[0] == farm.Detector.file_signature(cached[1]):
            config, templates = cached[2:]
        else:
            # Keep one coherent snapshot, and invalidate it when either the
            # catalog or a reference changes. Ready is observed twice per tick.
            config_stamp = farm.Detector.file_signature([config_path])
            config = farm.read_json(config_path, {})
            if not isinstance(config, dict):
                return None
            references = config.get("references", {})
            if not isinstance(references, dict):
                return None
            entries = []
            for number, paths in references.items():
                if not str(number).isdigit() or not 1 <= int(number) <= 20:
                    continue
                paths = [paths] if isinstance(paths, str) else paths
                if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
                    return None
                entries.extend((int(number), reference) for reference in paths)
            dependencies = [config_path, *[farm.ROOT / reference for _, reference in entries]]
            signature = farm.Detector.file_signature(dependencies)
            if config_stamp != farm.Detector.file_signature([config_path]):
                raise RuntimeError("Ninja Road map references changed during recognition")
            templates = []
            for number, reference in entries:
                template = farm.cv2.imread(str(farm.ROOT / reference), farm.cv2.IMREAD_GRAYSCALE)
                if template is None:
                    raise RuntimeError("Missing Ninja Road map reference: " + reference)
                templates.append((number, template))
            if signature != farm.Detector.file_signature(dependencies):
                raise RuntimeError("Ninja Road map references changed during recognition")
            self._ready_map_cache = (signature, dependencies, config, templates)
        region, references = config.get("region"), config.get("references", {})
        if not isinstance(region, list) or len(region) != 4 or not isinstance(references, dict):
            return None
        x1, y1, x2, y2 = region
        if (not all(type(value) is int for value in region)
                or not (0 <= x1 < x2 <= frame.shape[1] and 0 <= y1 < y2 <= frame.shape[0])):
            return None
        counter = farm.cv2.cvtColor(frame[y1:y2, x1:x2], farm.cv2.COLOR_BGR2GRAY)
        counter_values = counter.astype(farm.np.int16)
        matches = set()
        for number, template in templates:
            if number in matches or counter.shape != template.shape:
                continue
            correlation = float(farm.cv2.matchTemplate(counter, template, farm.cv2.TM_CCOEFF_NORMED)[0, 0])
            difference = float(farm.np.abs(counter_values - template).mean())
            if farm.np.isfinite(correlation) and correlation >= .96 and difference <= 12:
                matches.add(number)
        return next(iter(matches)) if len(matches) == 1 else None

    def observe(self):
        frame, name, spec, scores = super().observe()
        # A local, read-only live viewer requests copies of frames already
        # captured by this worker; it never adds ADB traffic or owns input.
        request = getattr(self, "progress", farm.RUNTIME / "ninja_road.json").parent / "ninja_live_request"
        try:
            if request.exists() and time.time() - request.stat().st_mtime < 15:
                ok, encoded = farm.cv2.imencode(".jpg", frame)
                if ok:
                    latest = request.with_name("ninja_live_latest.jpg")
                    temporary = latest.with_suffix(".tmp")
                    temporary.write_bytes(encoded.tobytes())
                    temporary.replace(latest)
        except OSError:
            pass  # A viewer cannot interrupt farming or its saved input.
        if name == "NR_TEAM_SELECT" and farm.early_exit_enabled():
            from automation.ninja_boss_swap import BossSwap
            if not hasattr(self, "boss_swap_controller"):
                self.boss_swap_controller = BossSwap(self)
            if self.boss_swap_controller.reference(frame, "early_exit_map"):
                auto = self.boss_swap_controller.auto(frame)
                if auto is False:
                    name = "NR_EARLY_EXIT"
                    spec = {"name": name, "phase": "early_exit", "verified_safe": True,
                            "on_confirm": {"early_exit_requested": True},
                            "action": {"id": "nr_retreat", "kind": "tap", "point": [230, 1208],
                                       "expected": ["NR_RETREAT_CONFIRM"]}}
                else:
                    name = "NR_EARLY_EXIT_PREPARE"
                    spec = {"name": name, "phase": "early_exit", "verified_safe": True}
                    if auto is True:
                        spec["action"] = {"id": "nr_auto", "kind": "tap", "point": [623, 56],
                                          "expected": ["NR_EARLY_EXIT"]}
        if name in self.READY_SCREENS and spec:
            spec["ready_map_index"] = self.read_ready_map(frame)
        if not spec or "team_slots" not in spec:
            return frame, name, spec, scores
        observed = []
        identities = set()
        cache = self.__dict__.setdefault("_team_slot_cache", {})
        slot_scores = {}
        for slot in spec["team_slots"]:
            index = slot.get("index")
            anchors = slot.get("anchors")
            if type(index) is not int or index < 1 or index in identities or not anchors:
                raise RuntimeError("Invalid Ninja Road visual team slot")
            identities.add(index)
            references = {anchor["template"] for anchor in anchors}
            paths = [farm.ROOT / reference for reference in references]
            signature = farm.Detector.file_signature(paths)
            key = json.dumps(slot, sort_keys=True)
            cached = cache.get(key)
            if not cached or cached[0] != signature:
                detector = object.__new__(farm.Detector)
                detector.states = [{"name": "NR_TEAM_SLOT_" + str(index), "anchors": anchors}]
                detector.templates = {}
                for reference in references:
                    template = farm.cv2.imread(str(farm.ROOT / reference), farm.cv2.IMREAD_GRAYSCALE)
                    if template is None:
                        raise RuntimeError("Missing Ninja Road team template: " + reference)
                    detector.templates[reference] = template
                if signature != farm.Detector.file_signature(paths):
                    raise RuntimeError("Ninja Road team references changed during recognition")
                cache[key] = (signature, detector)
            else:
                detector = cached[1]
            slot_name, _, metrics = detector.classify(frame)
            slot_scores[str(index)] = metrics
            if slot_name != "UNKNOWN":
                observed.append(index)
        spec["available_teams"] = sorted(observed)
        scores["team_slots"] = slot_scores
        return frame, name, spec, scores

    @staticmethod
    def _phase(name, spec):
        if spec and spec.get("phase"):
            return spec["phase"]
        for state, phase in (
            ("ALL_DEFEATED", "all_defeated"),
            ("TEAM_DEFEATED", "team_defeated"),
            ("TEAM_SELECT", "team_select"),
            ("COMBAT", "combat"),
            ("COMPLETE", "complete"),
            ("RESULTS", "results"),
            ("START", "start"),
        ):
            if state in name:
                return phase
        return None

    @staticmethod
    def _stamina_prompt(name, spec):
        action = (spec or {}).get("action", {})
        return (name == "STAMINA_PROMPT" and (spec or {}).get("phase") == "stamina"
                and (spec or {}).get("verified_safe") is True
                and action.get("id") == "dismiss_stamina" and action.get("kind") == "tap"
                and action.get("expected") == ["NR_START"])

    @staticmethod
    def _connection_error(name, spec):
        action = (spec or {}).get("action", {})
        return (name == "COMMUNICATION_ERROR" and (spec or {}).get("verified_safe") is True
                and (spec or {}).get("recovery") is True
                and action.get("id") == "retry_connection" and action.get("kind") == "tap")

    def _resolve_connection(self, name):
        original = self.data.get("connection_pending")
        if not original or name not in self.data.get("connection_targets", []):
            return False
        accepted = name == "NR_TEAM_SELECT" or name in self.CONNECTION_COMBAT
        if not accepted and name not in self.CONNECTION_MENUS:
            return False
        if accepted:
            self._begin_attempt(original)
            if name in self.CONNECTION_COMBAT:
                self.data["current_team"] = 1
                farm.event("ninja_team_selected", team=1)
        farm.event("ninja_connection_resolved", target=name, start_accepted=accepted,
                   run_id=original["run_id"])
        self.data.pop("connection_pending", None)
        self.data.pop("connection_targets", None)
        self.data["pending"] = None
        self.data.pop("transition_timed_out", None)
        return True

    def _begin_attempt(self, pending):
        previous = self.data.get("run")
        if previous and previous.get("outcome"):
            self.data["counts"]["resets"] = self.data["counts"].get("resets", 0) + 1
        self.data["current_team"] = None
        self.data["defeated_teams"] = []
        self.data["available_teams"] = list(pending.get("available_teams", []))
        self.data.pop("settings_verified_for", None)
        self.data.pop("auto_enabled", None)
        self.data.pop("ninjutsu_enabled", None)
        self.data.pop("boss_swap", None)
        self.data.pop("early_exit_requested", None)
        self.data["connection_recoveries"] = 0
        self.data.pop("connection_recovery_limit", None)
        self.data["counts"]["started"] += 1
        self.data["run"] = {"id": pending["run_id"], "start": pending["time"], "outcome": None}
        farm.event("ninja_attempt_started", run_id=pending["run_id"],
                   reset=self.data["counts"].get("resets", 0))

    def _confirm(self, name, spec):
        pending = self.data.get("pending")
        if name == "NR_HOME" and pending and pending.get("source") not in {"NR_HOME", "NR_AVAILABLE"}:
            farm.event("ninja_pending_ended_at_home", action=pending["id"], source=pending["source"])
            self.data["pending"] = None
            self.data.pop("connection_pending", None)
            self.data.pop("connection_targets", None)
            self.data.pop("transition_timed_out", None)
            pending = None
        if self.data.get("connection_pending"):
            if not pending or (pending["id"] == "retry_connection" and pending["source"] == "COMMUNICATION_ERROR"):
                if self._resolve_connection(name):
                    return
                if pending and time.time() - pending["time"] > pending.get("timeout", self.TRANSITION_TIMEOUT):
                    self.data["transition_timed_out"] = True
                return
        if not pending:
            return
        source_map = pending.get("ready_map_index")
        target_map = (spec or {}).get("ready_map_index")
        run = self.data.get("run") or {}
        if (pending.get("id") == "nr_team" and pending.get("source") == "NR_TEAM_SELECT"
                and not pending.get("on_confirm") and pending.get("returned") is True
                and name in self.READY_SCREENS and (spec or {}).get("verified_safe") is True
                and type(source_map) is int and type(target_map) is int
                and 1 <= source_map < 20 and target_map == source_map + 1
                and pending.get("ready_attempt_id") and pending["ready_attempt_id"] == run.get("id")
                and not run.get("outcome") and type(pending.get("ready_team")) is int
                and pending["ready_team"] >= 1 and pending["ready_team"] == self.data.get("current_team")):
            farm.event("ninja_ready_map_advanced", source_map=source_map, target_map=target_map,
                       team=pending["ready_team"], run_id=run["id"])
            self.data["pending"] = None
            self.data.pop("transition_timed_out", None)
            return
        if (self._connection_error(name, spec) and pending["id"] == "nr_start"
                and pending["source"] == "NR_START"):
            count = self.data.get("connection_recoveries", 0)
            if count >= self.CONNECTION_LIMIT:
                if not self.data.get("connection_recovery_limit"):
                    farm.event("ninja_connection_limit", attempts=count, source=pending["source"])
                self.data["connection_recovery_limit"] = True
                return
            self.data["connection_pending"] = copy.deepcopy(pending)
            self.data["connection_targets"] = list(spec["action"]["expected"])
            self.data["connection_recoveries"] = count + 1
            self.data["pending"] = None
            self.data.pop("transition_timed_out", None)
            farm.event("ninja_start_suspended", run_id=pending["run_id"], recovery=count + 1)
            return
        if (self._stamina_prompt(name, spec) and pending["id"] == "nr_start"
                and pending["source"] == "NR_START"):
            farm.event("action_rejected", stage=self.stage["id"], action=pending["id"],
                       source=pending["source"], target=name, reason="Insufficient stamina; mission was not started")
            self.data["pending"] = None
            self.data.pop("transition_timed_out", None)
            return
        rejected = (spec or {}).get("rejects_pending")
        if (self._phase(name, spec) == "team_defeated" and isinstance(rejected, dict)
                and rejected.get("id") == pending["id"] == "nr_team"
                and rejected.get("source") == pending["source"] == "NR_TEAM_SELECT"
                and not pending.get("on_confirm")):
            # A positively recognized dead-team screen proves that its disabled
            # Ready button rejected this input. This cannot discard a pending
            # team selection or mission start, which carry on_confirm changes.
            farm.event("action_rejected", stage=self.stage["id"], action=pending["id"],
                       source=pending["source"], target=name, reason="Confirmed defeated team has disabled Ready")
            self.data["pending"] = None
            self.data.pop("transition_timed_out", None)
            return
        if name != pending["source"] and name in pending["expected"]:
            changes = pending.get("on_confirm", {})
            if changes.get("begin_attempt"):
                self._begin_attempt(pending)
            if changes.get("early_exit_requested"):
                self.data["early_exit_requested"] = True
            if changes.get("early_exit_completed"):
                run = self.data.get("run")
                if run and not run.get("outcome"):
                    run.update(outcome="early_exit", ended=time.time())
                    self.data["counts"]["early_exits"] = self.data["counts"].get("early_exits", 0) + 1
                    farm.event("ninja_early_exit_completed", run_id=run["id"], cleared_map=10)
            selected = changes.get("team_index")
            if type(selected) is int:
                self.data["current_team"] = selected
                farm.event("ninja_team_selected", team=selected)
            farm.event("transition_confirmed", stage=self.stage["id"], action=pending["id"],
                       source=pending["source"], target=name,
                       seconds=round(time.time() - pending["time"], 2))
            self.data["pending"] = None
            self.data.pop("transition_timed_out", None)
        elif time.time() - pending["time"] > pending.get("timeout", self.TRANSITION_TIMEOUT):
            if not self.data.get("transition_timed_out"):
                farm.event("ninja_transition_timeout", action=pending["id"],
                           source=pending["source"], observed=name)
            self.data["transition_timed_out"] = True

    def update(self, frame, name, spec):
        previous = self.data.get("screen")
        if name != previous:
            farm.event("screen", previous=previous, screen=name, stage=self.stage["id"])
        self.data["screen"] = name
        self._confirm(name, spec)
        phase = self._phase(name, spec)
        if name == "NR_HOME":
            run = self.data.get("run")
            if run and not run.get("outcome"):
                run.update(outcome="abandoned", ended=time.time())
                farm.event("ninja_mission_returned_home", run_id=run["id"])
        if self._stamina_prompt(name, spec) and not self.data.get("stamina_wait_episode"):
            self.data["stamina_until"] = max(self.data.get("stamina_until", 0),
                                            time.time() + farm.stamina_wait_minutes() * 60)
            self.data["stamina_wait_episode"] = True
            farm.event("ninja_stamina_wait", until=self.data["stamina_until"])
        if name == "UNKNOWN":
            self.data.setdefault("unknown_since", time.time())
            self.evidence(frame, name)
        else:
            self.data.pop("unknown_since", None)
        if spec:
            teams = spec.get("available_teams")
            if teams is not None:
                if not isinstance(teams, list) or any(type(team) is not int or team < 1 for team in teams):
                    raise RuntimeError("Invalid visually observed Ninja Road teams")
                self.data["available_teams"] = sorted(set(teams))
            for setting in ("auto", "ninjutsu"):
                enabled = spec.get(setting + "_enabled", spec.get("battle_" + setting + "_enabled"))
                if type(enabled) is bool:
                    if self.data.get(setting + "_enabled") is not enabled:
                        farm.event("ninja_setting_observed", setting=setting, enabled=enabled,
                                   team=self.data.get("current_team"))
                    self.data[setting + "_enabled"] = enabled
        adopt_team = (spec or {}).get("adopt_team")
        run = self.data.get("run")
        if (phase == "combat" and type(adopt_team) is int and adopt_team >= 1
                and run is None and not self.data.get("pending")):
            self.data["run"] = {"id": uuid.uuid4().hex, "start": time.time(), "outcome": None,
                                "origin": "observed_combat", "duration_basis": "since_first_observation"}
            self.data["current_team"] = adopt_team
            self.data["defeated_teams"] = []
            self.data["counts"]["recovered_missions"] = self.data["counts"].get("recovered_missions", 0) + 1
            farm.event("ninja_attempt_adopted", team=adopt_team, run_id=self.data["run"]["id"])
        if phase == "combat" and all((spec or {}).get(setting + "_enabled",
                (spec or {}).get("battle_" + setting + "_enabled")) is True for setting in ("auto", "ninjutsu")):
            run = self.data.get("run") or {}
            verified = [run.get("id"), self.data.get("current_team")]
            if self.data.get("settings_verified_for") != verified:
                self.data["settings_verified_for"] = verified
                self.data["counts"]["settings_verified"] = self.data["counts"].get("settings_verified", 0) + 1
                farm.event("ninja_settings_verified", team=self.data.get("current_team"), run_id=run.get("id"))
        if phase == "team_defeated" or (spec or {}).get("team_defeated") is True:
            team = self.data.get("current_team")
            if type(team) is int and team not in self.data["defeated_teams"]:
                self.data["defeated_teams"].append(team)
                farm.event("ninja_team_defeated", team=team)
        if phase == "all_defeated":
            self.data["defeated_teams"] = sorted(set(self.data["defeated_teams"])
                | set(self.data.get("available_teams", []))
                | ({self.data["current_team"]} if self.data.get("current_team") else set()))
        run = self.data.get("run")
        if phase in {"all_defeated", "complete"} and run and not run.get("outcome"):
            outcome = "failed" if phase == "all_defeated" else "completed"
            run["outcome"] = outcome
            run["ended"] = time.time()
            self.data["counts"][outcome] += 1
            farm.event("ninja_attempt_" + outcome, run_id=run["id"],
                       teams=list(self.data["defeated_teams"]))
        if self.data.get("pending"):
            self.data["blocked"] = ("Communication recovery limit reached; original start remains safely latched"
                if self.data.get("connection_recovery_limit") else "Transition timed out; input remains safely latched"
                if self.data.get("transition_timed_out") else "Awaiting verified Ninja Road transition")
        elif self.data.get("connection_pending"):
            self.data["blocked"] = "Original mission start suspended during verified connection recovery"
        elif name == "UNKNOWN":
            expired = time.time() - self.data["unknown_since"] >= self.UNKNOWN_TIMEOUT
            self.data["blocked"] = ("Ninja Road recovery required; unknown screen, no input"
                if expired else "Unknown Ninja Road screen; observing without input")
        else:
            self.data["blocked"] = None
        self.save()

    def _choose_action(self, name, spec):
        action = copy.deepcopy((spec or {}).get("action"))
        choices = (spec or {}).get("team_choices")
        if name == "NR_RETREAT_CONFIRM" and not self.data.get("early_exit_requested"):
            self.data["blocked"] = "Retreat was not requested by Early Exit; no confirmation sent"
            return None, {}
        if not action and choices is None:
            return None, {}
        if name == "COMMUNICATION_ERROR" or (action or {}).get("id") == "retry_connection":
            if (not self._connection_error(name, spec) or not self.data.get("connection_pending")
                    or self.data.get("connection_recoveries", 0) > self.CONNECTION_LIMIT):
                self.data["blocked"] = "Unverified connection recovery; pending mission input preserved"
                return None, {}
        elif self.data.get("connection_pending"):
            self.data["blocked"] = "Waiting to resolve the suspended mission start; no new input"
            return None, {}
        if name == "STAMINA_PROMPT" or spec.get("phase") == "stamina" or (action or {}).get("id") == "dismiss_stamina":
            if not self._stamina_prompt(name, spec):
                self.data["blocked"] = "Unverified stamina cancellation; no input"
                return None, {}
        if (name == "NR_START" and (action or {}).get("id") == "nr_start"
                and self.data.get("stamina_until", 0) > time.time()):
            self.data["blocked"] = "Waiting for natural stamina regeneration"
            return None, {}
        if spec.get("require_team_slots"):
            teams = spec.get("available_teams", [])
            if not teams or teams != list(range(1, max(teams) + 1)):
                self.data["blocked"] = "Waiting for a complete, visually verified Ninja Road team list"
                return None, {}
        changes = copy.deepcopy(spec.get("on_confirm", {}))
        if spec.get("attempt_start"):
            changes["begin_attempt"] = True
        if type(spec.get("team_index")) is int:
            changes["team_index"] = spec["team_index"]
        if choices is not None:
            defeated = set(self.data.get("defeated_teams", []))
            current = self.data.get("current_team") or 0
            available = [choice for choice in choices
                         if type(choice.get("index")) is int
                         and choice["index"] > current and choice["index"] not in defeated]
            if not available:
                self.data["blocked"] = "No visually verified unused Ninja Road team is available"
                return None, {}
            choice = min(available, key=lambda candidate: candidate["index"])
            action = copy.deepcopy(choice["action"])
            changes["team_index"] = choice["index"]
        team = changes.get("team_index")
        if team is not None:
            beginning = changes.get("begin_attempt") is True
            defeated = set() if beginning else set(self.data.get("defeated_teams", []))
            current = 0 if beginning else self.data.get("current_team") or 0
            observed = self.data.get("available_teams", [])
            if (type(team) is not int or team < 1 or team in defeated
                    or team <= current or (observed and team not in observed)
                    or (current and current not in defeated)):
                self.data["blocked"] = "Ninja Road team progression guard rejected selection"
                return None, {}
            remaining = sorted(candidate for candidate in observed if candidate > current and candidate not in defeated)
            if remaining and team != remaining[0]:
                self.data["blocked"] = "Ninja Road teams must be selected in their observed order"
                return None, {}
            if current == 0 and team != 1:
                self.data["blocked"] = "A fresh Ninja Road attempt must start with Team 1"
                return None, {}
        # These toggles are only authorized by positive evidence of the off state.
        setting = spec.get("enable_setting")
        if setting in {"auto", "ninjutsu"}:
            enabled = spec.get(setting + "_enabled", spec.get("battle_" + setting + "_enabled"))
            if enabled is not False:
                self.data["blocked"] = "Ninja Road toggle lacks verified disabled state"
                return None, {}
        return action, changes

    def tick(self, act=True):
        if farm.STOP.exists() and act:
            return 0
        if act:
            self.consume_stamina_request()
        if self.detector.changed():
            self.detector = farm.Detector(self.stage["id"])
            farm.event("patterns_reloaded", stage=self.stage["id"])
        foreground = self.device.foreground()
        if foreground != farm.PACKAGE:
            self.data["screen"] = "GAME_CLOSED" if foreground == "com.uncube.launcher3" else "OTHER_FOREGROUND"
            self.data["blocked"] = "Game is not foreground; no input"
            self.save()
            return 15
        frame, name, spec, _ = self.observe()
        if name != "UNKNOWN":
            time.sleep(.25)
            fresh, confirmed, fresh_spec, _ = self.observe()
            if confirmed != name:
                self.update(fresh, "UNKNOWN", None)
                return 1
            if (name in self.READY_SCREENS
                    and (spec or {}).get("ready_map_index") != (fresh_spec or {}).get("ready_map_index")):
                self.update(fresh, "UNKNOWN", None)
                return 1
            if "team_slots" in (spec or {}) and spec.get("available_teams") != (fresh_spec or {}).get("available_teams"):
                self.update(fresh, "UNKNOWN", None)
                return 1
            if (spec or {}).get("action", {}).get("point_anchor") is not None:
                point = spec["action"]["point"]
                fresh_point = fresh_spec["action"]["point"]
                settled, settled_name, settled_spec, _ = self.observe()
                if (settled_name != name or any(abs(a - b) > 1 for a, b in zip(point, fresh_point))
                        or any(abs(a - b) > 1 for a, b in zip(fresh_point, settled_spec["action"]["point"]))):
                    self.update(settled, "UNKNOWN", None)
                    return 1
                fresh, fresh_spec = settled, settled_spec
            frame, spec = fresh, fresh_spec
        self.update(frame, name, spec)
        if not act or farm.STOP.exists():
            return 3
        if farm.boss_swap_enabled() or self.data.get("boss_swap"):
            from automation.ninja_boss_swap import BossSwap
            if not hasattr(self, "boss_swap_controller"):
                self.boss_swap_controller = BossSwap(self)
            delay = self.boss_swap_controller.tick(frame, name, spec)
            if delay is not None:
                return delay
        if self.data.get("pending"):
            return 3
        action, changes = self._choose_action(name, spec)
        if not action:
            self.save()
            if name == "NR_START" and self.data.get("stamina_until", 0) > time.time():
                return 30
            return 3 if self._phase(name, spec) == "combat" else 5
        if not action.get("expected"):
            raise RuntimeError("Ninja Road input requires a visually verified expected transition")
        pending = {"id": action["id"], "source": name, "expected": action["expected"],
                   "time": time.time(), "run_id": uuid.uuid4().hex, "on_confirm": changes,
                   "available_teams": list(self.data.get("available_teams", [])),
                   "timeout": spec.get("transition_timeout", self.TRANSITION_TIMEOUT)}
        if name == "NR_TEAM_SELECT" and action["id"] == "nr_team" and not changes:
            pending.update(ready_map_index=spec.get("ready_map_index"),
                           ready_attempt_id=(self.data.get("run") or {}).get("id"),
                           ready_team=self.data.get("current_team"))
        self.data["pending"] = pending
        if name == "NR_START" and action["id"] == "nr_start":
            self.data.pop("stamina_wait_episode", None)
        self.save()
        farm.event("action_attempt", stage=self.stage["id"], screen=name,
                   action=action["id"], point=action.get("point"))
        try:
            self.device.perform(action)
        except farm.ActionNotDispatched as exc:
            self.data["pending"] = None
            self.data["blocked"] = str(exc)
            self.save()
            farm.event("action_not_sent", stage=self.stage["id"], action=action["id"], error=str(exc))
            raise
        except Exception as exc:
            farm.event("action_uncertain", stage=self.stage["id"], action=action["id"], error=str(exc))
            raise
        if "ready_map_index" in pending:
            pending["returned"] = True
            self.save()
        farm.event("action_sent", stage=self.stage["id"], action=action["id"])
        return 1
