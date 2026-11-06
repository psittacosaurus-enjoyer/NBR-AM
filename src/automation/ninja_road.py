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
    TRANSITION_RETRY_LIMIT = 2
    UNKNOWN_TIMEOUT = 180
    # Responsiveness. Unrecognized frames inside the shared transition grace
    # window (farm.TRANSITION_GRACE_SECONDS) are re-observed quickly without
    # recovery records; the bounded recovery ladder starts only after it.
    UNKNOWN_POLL_SECONDS = 0.5
    PENDING_POLL_SECONDS = 0.5   # Waiting for a sent tap's verified transition.
    COMBAT_POLL_SECONDS = 2.0    # Auto combat with nothing to do.
    IDLE_POLL_SECONDS = 1.0      # A recognized screen whose action is blocked.
    UNKNOWN_RETRY_LIMIT = 5
    FOREGROUND_RETRY_LIMIT = 3
    CONNECTION_LIMIT = 3
    RECOVERY_BASE_SECONDS = 5
    RECOVERY_MAX_SECONDS = 60
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

    @property
    def exit_mode(self):
        run = getattr(self, "data", {}).get("run") or {}
        if run and not run.get("outcome"):
            return run.get("exit_mode", "stage10")
        return farm.ninja_exit_mode()

    def stage15_controller(self):
        if not hasattr(self, "_stage15_controller"):
            from automation.ninja_stage15 import Stage15Strategy
            self._stage15_controller = Stage15Strategy(self)
        return self._stage15_controller

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
        stage15 = self.exit_mode in farm.STAGE15_MODES
        if name == "NR_TEAM_SELECT" and stage15:
            spec["ready_map_index"] = self.stage15_controller().vision.read_map(frame)
        elif name in self.CONNECTION_COMBAT and stage15:
            spec["combat_map_index"] = self.stage15_controller().vision.read_map(frame)
        if name == "NR_TEAM_SELECT" and (stage15 or farm.early_exit_enabled()):
            from automation.ninja_boss_swap import BossSwap
            if not hasattr(self, "boss_swap_controller"):
                self.boss_swap_controller = BossSwap(self)
            exit_map = (spec.get("ready_map_index") == 16 if stage15 else
                        self.boss_swap_controller.reference(frame, "early_exit_map"))
            if exit_map:
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
                if stage15:
                    spec["ready_map_index"] = 16
                    spec["exit_after_stage"] = 15
        if (name == "NR_TEAM_DEFEATED" and spec and spec.get("verified_safe") is True
                and farm.one_team_enabled()):
            name, spec = self._one_team_exit(frame, spec, stage15)
        if name in self.READY_SCREENS and spec:
            if not stage15:
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

    ONE_TEAM_EXIT_SCREENS = {"NR_ONE_TEAM_EXIT", "NR_ONE_TEAM_EXIT_PREPARE"}

    def _wrap_recovery(self):
        """Defeat recovery may return to an earlier living team (adjacent arrow).

        Needed whenever a run can leave teams out of order: Stage 16 Strategy's
        per-map plan, or Stage 16 Exit with the Team 2 on Map 13 option.
        """
        mode = self.exit_mode
        return mode == "stage15" or (mode == "stage15_clean" and farm.map13_team2_enabled())

    def _one_team_exit(self, frame, defeated, stage15):
        """One Team Run: retreat from a verified defeated-team screen.

        Reuses Early Exit's sequence (Auto off, Retreat, guarded confirmation)
        instead of advancing to the next team. The defeated phase is kept so
        defeat bookkeeping and rejected-Ready handling are unchanged.
        """
        from automation.ninja_boss_swap import BossSwap
        if not hasattr(self, "boss_swap_controller"):
            self.boss_swap_controller = BossSwap(self)
        try:
            reached = (self.stage15_controller().vision.read_map(frame) if stage15
                       else self.read_ready_map(frame))
        except Exception:
            reached = None
        spec = {"phase": "team_defeated", "team_defeated": True, "verified_safe": True,
                "one_team_exit": True,
                "exit_after_stage": reached - 1 if type(reached) is int and reached > 1 else None}
        if defeated.get("rejects_pending"):
            spec["rejects_pending"] = copy.deepcopy(defeated["rejects_pending"])
        auto = self.boss_swap_controller.auto(frame)
        if auto is False:
            name = "NR_ONE_TEAM_EXIT"
            spec.update(on_confirm={"early_exit_requested": True},
                        action={"id": "nr_retreat", "kind": "tap", "point": [230, 1208],
                                "expected": ["NR_RETREAT_CONFIRM"]})
        else:
            name = "NR_ONE_TEAM_EXIT_PREPARE"
            if auto is True:
                spec["action"] = {"id": "nr_auto", "kind": "tap", "point": [623, 56],
                                  "expected": ["NR_ONE_TEAM_EXIT"]}
        spec["name"] = name
        return name, spec

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

    @classmethod
    def _recovery_delay(cls, attempt):
        attempt = max(1, int(attempt))
        return min(cls.RECOVERY_MAX_SECONDS,
                   cls.RECOVERY_BASE_SECONDS * 2 ** min(attempt - 1, 10))

    @staticmethod
    def _wait_delay(deadline):
        return max(.25, deadline - time.time())

    def _recovery_context(self):
        return {**super()._recovery_context(),
                "last_known_screen": self.data.get("last_known_screen"),
                "current_team": self.data.get("current_team")}

    def _clear_transition_recovery(self):
        self.data.pop("transition_timed_out", None)
        self.data.pop("transition_recovery", None)

    @staticmethod
    def _action_guard(action):
        fields = ("id", "kind", "expected", "input_mode", "point", "point_anchor",
                  "point_offset", "start", "end", "duration")
        return {field: copy.deepcopy(action[field]) for field in fields if field in action}

    def _transition_expired(self, pending):
        attempted = pending.get("last_attempt_at", pending["time"])
        return time.time() - attempted > pending.get("timeout", self.TRANSITION_TIMEOUT)

    def _schedule_transition_recovery(self, pending, observed):
        key = [pending.get("run_id"), pending.get("id"), pending.get("source")]
        episode = self.data.get("transition_recovery")
        if not isinstance(episode, dict) or episode.get("key") != key:
            episode = {
                "key": key,
                "attempts": 0,
                "relaunches": 0,
                "next_attempt_at": time.time() + self._recovery_delay(1),
            }
            self.data["transition_recovery"] = episode
        elif episode.get("next_attempt_at", 0) <= 0:
            episode["next_attempt_at"] = (time.time()
                + self._recovery_delay(episode.get("attempts", 0) + 1))
        episode["last_observed"] = observed
        return episode

    def _verified_retry_action(self, name, spec, pending):
        if (name != pending.get("source") or not spec
                or spec.get("verified_safe") is not True):
            return None
        action, changes = self._choose_action(name, spec)
        if (not action or action.get("id") != pending.get("id")
                or action.get("kind") not in {"tap", "swipe"}
                or action.get("expected") != pending.get("expected")
                or changes != pending.get("on_confirm", {})
                or not isinstance(pending.get("retry_guard"), dict)
                or self._action_guard(action) != pending["retry_guard"]):
            return None
        farm.require_pearl_safe_action(self.stage["id"], name, {**spec, "action": action})
        return action

    def _recover_transition(self, name, spec):
        pending = self.data.get("pending")
        if not pending or not self.data.get("transition_timed_out"):
            return None
        episode = self._schedule_transition_recovery(pending, name)
        now = time.time()
        if now < episode.get("next_attempt_at", 0):
            return self._wait_delay(episode["next_attempt_at"])
        action = self._verified_retry_action(name, spec, pending)
        attempts = episode.get("attempts", 0)
        if action and attempts < self.TRANSITION_RETRY_LIMIT:
            attempt = attempts + 1
            episode.update(attempts=attempt, next_attempt_at=0,
                           last_retry_at=now)
            pending["last_attempt_at"] = now
            pending.pop("returned", None)
            self.data.pop("transition_timed_out", None)
            self.data["blocked"] = "Retrying the same freshly verified Ninja Road action"
            self.save()
            if farm.STOP.exists():
                return 0
            farm.event("ninja_transition_retry", attempt=attempt,
                       limit=self.TRANSITION_RETRY_LIMIT, action=pending["id"],
                       source=pending["source"], **self._recovery_context())
            try:
                self._perform_verified_action(action, name, spec)
            except Exception as exc:
                farm.event("ninja_transition_retry_error", attempt=attempt,
                           action=pending["id"], error=str(exc),
                           **self._recovery_context())
                raise
            if ("ready_map_index" in pending or
                    (action["id"] == "nr_start" and pending.get("exit_mode") in farm.STAGE15_MODES)):
                pending["returned"] = True
                self.save()
            farm.event("ninja_transition_retry_sent", attempt=attempt,
                       action=pending["id"], source=pending["source"])
            return 1

        # A launch is the broadest safe recovery available here: it is pinned
        # to the same verified Android identity and never presses an in-game UI.
        relaunch = episode.get("relaunches", 0) + 1
        delay = self._recovery_delay(relaunch)
        episode.update(attempts=0, relaunches=relaunch, next_attempt_at=0,
                       last_relaunch_at=now)
        pending["last_attempt_at"] = now
        self.data.pop("transition_timed_out", None)
        self.data["blocked"] = "Reopening the verified game after transition recovery retries"
        self.save()
        if farm.STOP.exists():
            return 0
        farm.event("ninja_transition_relaunch", relaunch=relaunch,
                   safe_retry_available=bool(action), **self._recovery_context())
        try:
            self.device.launch()
        except Exception as exc:
            farm.event("ninja_transition_relaunch_error", relaunch=relaunch,
                       error=str(exc), **self._recovery_context())
            raise
        farm.event("ninja_transition_relaunch_sent", relaunch=relaunch,
                   **self._recovery_context())
        return delay

    def _unknown_grace(self):
        if self.data.get("pending"):
            return farm.TRANSITION_GRACE_SECONDS["pending"]
        run = self.data.get("run") or {}
        if run.get("id") and run.get("outcome") is None:
            return farm.TRANSITION_GRACE_SECONDS["battle"]
        return farm.TRANSITION_GRACE_SECONDS["idle"]

    def _recover_unknown(self):
        now = time.time()
        episode = self.data.get("recognition_recovery")
        if not isinstance(episode, dict):
            since = self.data.get("unknown_since", now)
            if now - since < self._unknown_grace():
                # Map walks, loading and animations: look again soon. No input,
                # no recovery record; the ladder starts after the grace window.
                self.data["blocked"] = "Waiting for the screen transition to finish"
                self.save()
                return self.UNKNOWN_POLL_SECONDS
            episode = {
                "observations": 0,
                "relaunches": 0,
                "round_started_at": self.data.get("unknown_since", now),
                "next_attempt_at": 0,
            }
            self.data["recognition_recovery"] = episode
        if now < episode.get("next_attempt_at", 0):
            return self._wait_delay(episode["next_attempt_at"])
        attempt = episode.get("observations", 0) + 1
        episode["observations"] = attempt
        elapsed = max(0, now - episode.get("round_started_at", now))
        farm.event("ninja_recognition_failed", attempt=attempt,
                   elapsed=round(elapsed, 2), evidence=self.data.get("evidence"),
                   **self._recovery_context())
        if attempt < self.UNKNOWN_RETRY_LIMIT and elapsed < self.UNKNOWN_TIMEOUT:
            delay = self._recovery_delay(attempt)
            episode["next_attempt_at"] = now + delay
            self.data["blocked"] = "Unknown Ninja Road screen; bounded recognition retry scheduled"
            self.save()
            return delay

        relaunch = episode.get("relaunches", 0) + 1
        delay = self._recovery_delay(relaunch)
        episode.update(observations=0, relaunches=relaunch,
                       round_started_at=now, next_attempt_at=now + delay,
                       last_relaunch_at=now)
        self.data["blocked"] = "Recognition retries exhausted; reopening the verified game"
        self.save()
        if farm.STOP.exists():
            return 0
        farm.event("ninja_recognition_relaunch", relaunch=relaunch,
                   elapsed=round(elapsed, 2), **self._recovery_context())
        try:
            self.device.launch()
        except Exception as exc:
            farm.event("ninja_recognition_relaunch_error", relaunch=relaunch,
                       error=str(exc), **self._recovery_context())
            raise
        farm.event("ninja_recognition_relaunch_sent", relaunch=relaunch,
                   **self._recovery_context())
        return delay

    def _recover_foreground(self, foreground):
        now = time.time()
        episode = self.data.get("foreground_recovery")
        if not isinstance(episode, dict) or episode.get("foreground") != foreground:
            episode = {"foreground": foreground, "observations": 0,
                       "relaunches": 0, "next_attempt_at": 0}
            self.data["foreground_recovery"] = episode
        if now < episode.get("next_attempt_at", 0):
            return self._wait_delay(episode["next_attempt_at"])
        attempt = episode.get("observations", 0) + 1
        episode["observations"] = attempt
        farm.event("ninja_foreground_missing", foreground=foreground,
                   attempt=attempt, **self._recovery_context())
        if attempt < self.FOREGROUND_RETRY_LIMIT:
            delay = self._recovery_delay(attempt)
            episode["next_attempt_at"] = now + delay
            self.data["blocked"] = "Game is not foreground; verified relaunch retry scheduled"
            self.save()
            return delay
        relaunch = episode.get("relaunches", 0) + 1
        delay = self._recovery_delay(relaunch)
        episode.update(observations=0, relaunches=relaunch,
                       next_attempt_at=now + delay, last_relaunch_at=now)
        self.data["blocked"] = "Reopening the pinned game on the verified Android device"
        self.save()
        if farm.STOP.exists():
            return 0
        farm.event("ninja_foreground_relaunch", foreground=foreground,
                   relaunch=relaunch, **self._recovery_context())
        try:
            self.device.launch()
        except Exception as exc:
            farm.event("ninja_foreground_relaunch_error", foreground=foreground,
                       relaunch=relaunch, error=str(exc), **self._recovery_context())
            raise
        farm.event("ninja_foreground_relaunch_sent", foreground=foreground,
                   relaunch=relaunch, **self._recovery_context())
        return delay

    def _handle_unstable_observation(self, frame, act):
        self.update(frame, "UNKNOWN", None)
        if not act or farm.STOP.exists():
            return 3
        return self._recover_unknown()

    def _recover_visual_prerequisite(self, name, spec):
        """Recover missing visual facts without bypassing progression guards."""
        reason = None
        waiting_stamina = name == "NR_START" and self.data.get("stamina_until", 0) > time.time()
        if (not waiting_stamina and not self.data.get("pending")
                and not self.data.get("connection_pending")):
            if (self.exit_mode in farm.STAGE15_MODES and name in self.READY_SCREENS
                    and type((spec or {}).get("ready_map_index")) is not int):
                reason = "unambiguous_ready_map"
            elif name == "NR_START" and (spec or {}).get("require_team_slots"):
                teams = (spec or {}).get("available_teams", [])
                if not teams or teams != list(range(1, max(teams) + 1)):
                    reason = "complete_visible_team_slots"
        episode = self.data.get("visual_prerequisite_recovery")
        if reason is None:
            if episode:
                self.data.pop("visual_prerequisite_recovery", None)
                farm.event("ninja_visual_prerequisite_verified", target=name,
                           prerequisite=episode.get("reason"), **self._recovery_context())
                self.save()
            return None
        if (spec or {}).get("verified_safe") is not True:
            return None  # A safety rejection cannot authorize recovery input.
        now = time.time()
        if not isinstance(episode, dict) or (episode.get("screen"), episode.get("reason")) != (name, reason):
            episode = {"screen": name, "reason": reason, "attempts": 0,
                       "relaunches": 0, "next_attempt_at": 0}
            self.data["visual_prerequisite_recovery"] = episode
        if now < episode["next_attempt_at"]:
            return self._wait_delay(episode["next_attempt_at"])
        attempt = episode["attempts"] + 1
        delay = self._recovery_delay(attempt)
        episode.update(attempts=attempt, next_attempt_at=now + delay)
        self.data["blocked"] = "Waiting for verified Ninja Road visual prerequisites; bounded recovery scheduled"
        farm.event("ninja_visual_prerequisite_retry", prerequisite=reason,
                   attempt=attempt, delay_seconds=delay, **self._recovery_context())
        if attempt < self.UNKNOWN_RETRY_LIMIT:
            self.save()
            return delay
        relaunch = episode["relaunches"] + 1
        delay = self._recovery_delay(relaunch)
        episode.update(attempts=0, relaunches=relaunch, next_attempt_at=now + delay)
        self.data["blocked"] = "Visual prerequisites remain unavailable; reopening the verified game"
        self.save()
        if farm.STOP.exists():
            return 0
        farm.event("ninja_visual_prerequisite_relaunch", prerequisite=reason,
                   relaunch=relaunch, **self._recovery_context())
        self.device.launch()
        return delay

    def _schedule_connection_escalation(self, pending):
        episode = self.data.get("connection_escalation")
        if not isinstance(episode, dict):
            episode = {"attempts": 0, "next_attempt_at": 0}
            self.data["connection_escalation"] = episode
        if episode.get("next_attempt_at", 0) <= time.time():
            episode["next_attempt_at"] = (time.time()
                + self._recovery_delay(episode.get("attempts", 0) + 1))
        self.data["connection_recovery_limit"] = True
        farm.event("ninja_connection_limit", attempts=self.data.get("connection_recoveries", 0),
                   source=pending.get("source"), next_attempt_at=episode["next_attempt_at"],
                   **self._recovery_context())

    def _recover_connection_limit(self):
        if not self.data.get("connection_recovery_limit"):
            return None
        pending = self.data.get("pending")
        episode = self.data.get("connection_escalation") or {}
        now = time.time()
        if now < episode.get("next_attempt_at", 0):
            return self._wait_delay(episode["next_attempt_at"])
        attempt = episode.get("attempts", 0) + 1
        delay = self._recovery_delay(attempt)
        episode.update(attempts=attempt, next_attempt_at=now + delay,
                       last_relaunch_at=now)
        self.data["connection_escalation"] = episode
        self.data["connection_recoveries"] = 0
        self.data.pop("connection_recovery_limit", None)
        self._clear_transition_recovery()
        if pending:
            pending["last_attempt_at"] = now
        self.data["blocked"] = "Reopening the verified game after bounded connection recovery"
        self.save()
        if farm.STOP.exists():
            return 0
        farm.event("ninja_connection_relaunch", attempt=attempt,
                   **self._recovery_context())
        try:
            self.device.launch()
        except Exception as exc:
            farm.event("ninja_connection_relaunch_error", attempt=attempt,
                       error=str(exc), **self._recovery_context())
            raise
        farm.event("ninja_connection_relaunch_sent", attempt=attempt,
                   **self._recovery_context())
        return delay

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
        self._clear_transition_recovery()
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
        self.data.pop("one_team_exit", None)
        self.data.pop("stage15_strategy", None)
        self.data.pop("stage15_map", None)
        self.data.pop("stage15_formations", None)
        self.data.pop("exit_after_stage", None)
        self.data["connection_recoveries"] = 0
        self.data.pop("connection_recovery_limit", None)
        connection_recovery = self.data.pop("connection_escalation", None)
        if connection_recovery:
            farm.event("ninja_connection_recovery_verified",
                       relaunches=connection_recovery.get("attempts", 0),
                       target=self.data.get("screen"), run_id=pending.get("run_id"))
        self._clear_transition_recovery()
        self.data["counts"]["started"] += 1
        self.data["run"] = {"id": pending["run_id"], "start": pending["time"], "outcome": None,
                            "exit_mode": pending.get("exit_mode", "stage10")}
        farm.event("ninja_attempt_started", run_id=pending["run_id"],
                   reset=self.data["counts"].get("resets", 0))

    def _confirm(self, name, spec):
        pending = self.data.get("pending")
        if (name == "TITLE_START" and (spec or {}).get("verified_safe") is True
                and (pending or self.data.get("connection_pending"))
                and (pending or {}).get("source") != "TITLE_START"):
            # The title screen proves the game process restarted (crash or
            # emulator restart). No earlier tap can still be in flight, and it
            # is never replayed: the quicksave resume decides where play resumes.
            farm.event("ninja_pending_ended_at_title", action=(pending or {}).get("id"),
                       source=(pending or {}).get("source"),
                       connection_pending=bool(self.data.get("connection_pending")))
            self.data["pending"] = None
            self.data.pop("connection_pending", None)
            self.data.pop("connection_targets", None)
            self._clear_transition_recovery()
            pending = None
        if name == "NR_HOME" and pending and pending.get("source") not in {"NR_HOME", "NR_AVAILABLE"}:
            farm.event("ninja_pending_ended_at_home", action=pending["id"], source=pending["source"])
            self.data["pending"] = None
            self.data.pop("connection_pending", None)
            self.data.pop("connection_targets", None)
            self._clear_transition_recovery()
            pending = None
        if self.data.get("connection_pending"):
            if not pending or (pending["id"] == "retry_connection" and pending["source"] == "COMMUNICATION_ERROR"):
                if self._resolve_connection(name):
                    return
                if pending and self._transition_expired(pending):
                    if not self.data.get("transition_timed_out"):
                        farm.event("ninja_transition_timeout", action=pending["id"],
                                   source=pending["source"], observed=name)
                    self.data["transition_timed_out"] = True
                    self._schedule_transition_recovery(pending, name)
                return
        if not pending:
            return
        if (pending.get("id") == "nr_start" and pending.get("source") == "NR_START"
                and pending.get("exit_mode") in farm.STAGE15_MODES and pending.get("returned") is True
                and pending.get("on_confirm", {}).get("begin_attempt") is True
                and name in self.CONNECTION_COMBAT and (spec or {}).get("verified_safe") is True
                and (spec or {}).get("combat_map_index") == 1):
            # The startup overlay precedes initial Ready. It positively proves
            # acceptance and lets this mode configure Ninjutsu before Ready.
            self._begin_attempt(pending)
            self.data["current_team"] = 1
            self.data["stage15_map"] = 1
            self.data["pending"] = None
            self._clear_transition_recovery()
            farm.event("ninja_startup_combat_confirmed", run_id=pending["run_id"], team=1)
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
            self._clear_transition_recovery()
            return
        if (self._connection_error(name, spec) and pending["id"] == "nr_start"
                and pending["source"] == "NR_START"):
            count = self.data.get("connection_recoveries", 0)
            if count >= self.CONNECTION_LIMIT:
                if not self.data.get("connection_recovery_limit"):
                    self._schedule_connection_escalation(pending)
                return
            self.data["connection_pending"] = copy.deepcopy(pending)
            self.data["connection_targets"] = list(spec["action"]["expected"])
            self.data["connection_recoveries"] = count + 1
            self.data["pending"] = None
            self._clear_transition_recovery()
            farm.event("ninja_start_suspended", run_id=pending["run_id"], recovery=count + 1)
            return
        if (self._stamina_prompt(name, spec) and pending["id"] == "nr_start"
                and pending["source"] == "NR_START"):
            farm.event("action_rejected", stage=self.stage["id"], action=pending["id"],
                       source=pending["source"], target=name, reason="Insufficient stamina; mission was not started")
            self.data["pending"] = None
            self._clear_transition_recovery()
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
            self._clear_transition_recovery()
            return
        if name != pending["source"] and name in pending["expected"]:
            changes = pending.get("on_confirm", {})
            if changes.get("begin_attempt"):
                self._begin_attempt(pending)
            if changes.get("early_exit_requested"):
                self.data["early_exit_requested"] = True
                self.data["exit_after_stage"] = pending.get("exit_after_stage", 10)
                if pending.get("source") in self.ONE_TEAM_EXIT_SCREENS:
                    self.data["one_team_exit"] = True
            if changes.get("early_exit_completed"):
                run = self.data.get("run")
                if run and not run.get("outcome"):
                    run.update(outcome="early_exit", ended=time.time())
                    self.data["counts"]["early_exits"] = self.data["counts"].get("early_exits", 0) + 1
                    farm.event("ninja_early_exit_completed", run_id=run["id"],
                               cleared_map=self.data.get("exit_after_stage", 10),
                               one_team=self.data.get("one_team_exit") is True)
            selected = changes.get("team_index")
            if type(selected) is int:
                self.data["current_team"] = selected
                farm.event("ninja_team_selected", team=selected)
            farm.event("transition_confirmed", stage=self.stage["id"], action=pending["id"],
                       source=pending["source"], target=name,
                       seconds=round(time.time() - pending["time"], 2))
            self.data["pending"] = None
            self._clear_transition_recovery()
        elif self._transition_expired(pending):
            if not self.data.get("transition_timed_out"):
                farm.event("ninja_transition_timeout", action=pending["id"],
                           source=pending["source"], observed=name)
            self.data["transition_timed_out"] = True
            self._schedule_transition_recovery(pending, name)

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
            if previous and previous != "UNKNOWN":
                self.data["last_known_screen"] = previous
            self.data.setdefault("unknown_since", time.time())
            self.evidence(frame, name)
        else:
            recognition_recovery = self.data.pop("recognition_recovery", None)
            if recognition_recovery:
                farm.event("ninja_recognition_recovery_verified", target=name,
                           observations=recognition_recovery.get("observations", 0),
                           relaunches=recognition_recovery.get("relaunches", 0),
                           **self._recovery_context())
            self.data.pop("unknown_since", None)
            self.data["last_known_screen"] = name
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
            self.data["blocked"] = ("Connection retries exhausted; verified relaunch scheduled"
                if self.data.get("connection_recovery_limit") else "Transition timed out; guarded retry or relaunch scheduled"
                if self.data.get("transition_timed_out") else "Awaiting verified Ninja Road transition")
        elif self.data.get("connection_pending"):
            self.data["blocked"] = "Original mission start suspended during verified connection recovery"
        elif name == "UNKNOWN":
            expired = time.time() - self.data["unknown_since"] >= self.UNKNOWN_TIMEOUT
            self.data["blocked"] = ("Ninja Road recognition recovery escalating; no in-game input"
                if expired else "Unknown Ninja Road screen; observing without input")
        else:
            self.data["blocked"] = None
        self.save()

    def _choose_action(self, name, spec):
        action = copy.deepcopy((spec or {}).get("action"))
        choices = (spec or {}).get("team_choices")
        if (self.exit_mode in farm.STAGE15_MODES and (spec or {}).get("enable_setting") == "ninjutsu"
                and self.data.get("stage15_map", 0) < 5):
            return None, {}  # The legacy OFF-state action must not undo this mode's opening policy.
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
        if (type(spec.get("team_index")) is int and not
                (self.exit_mode in farm.STAGE15_MODES and name == "NR_TEAM_SELECT"
                 and spec["team_index"] == self.data.get("current_team"))):
            changes["team_index"] = spec["team_index"]
        if choices is not None:
            defeated = set(self.data.get("defeated_teams", []))
            current = self.data.get("current_team") or 0
            recovery15 = self._wrap_recovery() and self._phase(name, spec) == "team_defeated"
            available = [choice for choice in choices
                         if type(choice.get("index")) is int
                         and (recovery15 or choice["index"] > current) and choice["index"] not in defeated]
            if recovery15:
                # Strategies may return to earlier living teams. Reuse the
                # same recovery arrow without treating an earlier team as dead.
                observed = self.data.get("available_teams", [])
                following = [team for team in observed if team > current] + [team for team in observed if team < current]
                next_team = next((team for team in following if team not in defeated), None)
                recovery_action = copy.deepcopy(spec["action"])
                if next_team and current in observed:
                    index = observed.index(current)
                    if observed[(index + 1) % len(observed)] != next_team:
                        if observed[(index - 1) % len(observed)] != next_team:
                            self.data["blocked"] = "No adjacent living Ninja Road recovery team is verified"
                            return None, {}
                        recovery_action["point"] = [28, 1286]
                available = [{"index": next_team, "action": recovery_action}] if next_team else []
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
            recovery15 = self._wrap_recovery() and self._phase(name, spec) == "team_defeated"
            if (type(team) is not int or team < 1 or team in defeated
                    or (team <= current and not recovery15) or (observed and team not in observed)
                    or (current and current not in defeated)):
                self.data["blocked"] = "Ninja Road team progression guard rejected selection"
                return None, {}
            remaining = sorted(candidate for candidate in observed if candidate > current and candidate not in defeated)
            if remaining and team != remaining[0] and not recovery15:
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
        now = time.monotonic()
        settled = self.data.get("screen") not in (None, "UNKNOWN", "GAME_CLOSED", "OTHER_FOREGROUND")
        if (act and settled and now - getattr(self, "_foreground_checked_at", float("-inf"))
                < farm.FOREGROUND_RECHECK_SECONDS):
            # Observation reuse only: every input re-checks identity and
            # foreground inside Device.perform immediately before dispatch.
            foreground = farm.PACKAGE
        else:
            foreground = self.device.foreground()
            if foreground == farm.PACKAGE:
                self._foreground_checked_at = now
            else:
                self._foreground_checked_at = float("-inf")
        if foreground != farm.PACKAGE:
            self.data["screen"] = "GAME_CLOSED" if foreground == "com.uncube.launcher3" else "OTHER_FOREGROUND"
            self.data["blocked"] = "Game is not foreground; no input"
            self.save()
            return self._recover_foreground(foreground) if act else 15
        foreground_recovery = self.data.pop("foreground_recovery", None)
        if foreground_recovery:
            farm.event("ninja_foreground_restored", foreground=foreground,
                       observations=foreground_recovery.get("observations", 0),
                       relaunches=foreground_recovery.get("relaunches", 0),
                       **self._recovery_context())
            self.save()
        frame, name, spec, _ = self.observe()
        if name != "UNKNOWN":
            time.sleep(.25)
            fresh, confirmed, fresh_spec, _ = self.observe()
            if confirmed != name:
                return self._handle_unstable_observation(fresh, act)
            if (name in self.READY_SCREENS
                    and (spec or {}).get("ready_map_index") != (fresh_spec or {}).get("ready_map_index")):
                return self._handle_unstable_observation(fresh, act)
            if (name in self.CONNECTION_COMBAT
                    and (spec or {}).get("combat_map_index") != (fresh_spec or {}).get("combat_map_index")):
                return self._handle_unstable_observation(fresh, act)
            if "team_slots" in (spec or {}) and spec.get("available_teams") != (fresh_spec or {}).get("available_teams"):
                return self._handle_unstable_observation(fresh, act)
            if (spec or {}).get("action", {}).get("point_anchor") is not None:
                point = spec["action"]["point"]
                fresh_point = fresh_spec["action"]["point"]
                settled, settled_name, settled_spec, _ = self.observe()
                if (settled_name != name or any(abs(a - b) > 1 for a, b in zip(point, fresh_point))
                        or any(abs(a - b) > 1 for a, b in zip(fresh_point, settled_spec["action"]["point"]))):
                    return self._handle_unstable_observation(settled, act)
                fresh, fresh_spec = settled, settled_spec
            frame, spec = fresh, fresh_spec
        self.update(frame, name, spec)
        if not act or farm.STOP.exists():
            return 3
        if name == "UNKNOWN":
            return self._recover_unknown()
        delay = self._recover_connection_limit()
        if delay is not None:
            return delay
        delay = self._recover_transition(name, spec)
        if delay is not None:
            return delay
        delay = self._recover_visual_prerequisite(name, spec)
        if delay is not None:
            return delay
        if self.exit_mode in farm.STAGE15_MODES:
            delay = self.stage15_controller().tick(frame, name, spec)
            if delay is not None:
                return delay
        if farm.boss_swap_enabled() or self.data.get("boss_swap"):
            from automation.ninja_boss_swap import BossSwap
            if not hasattr(self, "boss_swap_controller"):
                self.boss_swap_controller = BossSwap(self)
            delay = self.boss_swap_controller.tick(frame, name, spec)
            if delay is not None:
                return delay
        if self.data.get("pending"):
            return self.PENDING_POLL_SECONDS
        action, changes = self._choose_action(name, spec)
        if not action:
            self.save()
            if name == "NR_START" and self.data.get("stamina_until", 0) > time.time():
                return 30
            return self.COMBAT_POLL_SECONDS if self._phase(name, spec) == "combat" else self.IDLE_POLL_SECONDS
        if not action.get("expected"):
            raise RuntimeError("Ninja Road input requires a visually verified expected transition")
        farm.require_pearl_safe_action(self.stage["id"], name, {**spec, "action": action})
        pending = {"id": action["id"], "source": name, "expected": action["expected"],
                   "time": time.time(), "run_id": uuid.uuid4().hex, "on_confirm": changes,
                   "available_teams": list(self.data.get("available_teams", [])),
                   "timeout": spec.get("transition_timeout", self.TRANSITION_TIMEOUT),
                   "retry_guard": self._action_guard(action)}
        if spec.get("attempt_start"):
            pending["exit_mode"] = farm.ninja_exit_mode()
        if "exit_after_stage" in spec:
            pending["exit_after_stage"] = spec["exit_after_stage"]
        if name == "NR_TEAM_SELECT" and action["id"] == "nr_team" and not changes:
            pending.update(ready_map_index=spec.get("ready_map_index"),
                           ready_attempt_id=(self.data.get("run") or {}).get("id"),
                           ready_team=self.data.get("current_team"))
        self._clear_transition_recovery()
        self.data["pending"] = pending
        if name == "NR_START" and action["id"] == "nr_start":
            self.data.pop("stamina_wait_episode", None)
        self.save()
        farm.event("action_attempt", stage=self.stage["id"], screen=name,
                   action=action["id"], point=action.get("point"))
        try:
            self._perform_verified_action(action, name, spec)
        except farm.ActionNotDispatched as exc:
            self.data["pending"] = None
            deferrals = getattr(self, "_dispatch_deferrals", 0) + 1
            if isinstance(exc.__cause__, farm.ScreenChanged) and deferrals <= farm.DISPATCH_DEFER_LIMIT:
                # The screen animated between recognition and dispatch and no
                # input was sent: look again shortly instead of a worker error.
                self._dispatch_deferrals = deferrals
                self.data["blocked"] = "Screen still animating; re-checking before input"
                self.save()
                farm.event("action_deferred", stage=self.stage["id"], action=action["id"],
                           screen=name, attempt=deferrals)
                return farm.DISPATCH_DEFER_SECONDS
            self._dispatch_deferrals = 0
            self.data["blocked"] = str(exc)
            self.save()
            farm.event("action_not_sent", stage=self.stage["id"], action=action["id"], error=str(exc))
            raise
        except Exception as exc:
            farm.event("action_uncertain", stage=self.stage["id"], action=action["id"], error=str(exc))
            raise
        if "ready_map_index" in pending or (action["id"] == "nr_start" and pending.get("exit_mode") in farm.STAGE15_MODES):
            pending["returned"] = True
            self.save()
        self._dispatch_deferrals = 0
        farm.event("action_sent", stage=self.stage["id"], action=action["id"])
        return 1
