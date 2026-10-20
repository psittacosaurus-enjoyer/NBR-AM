"""Optional Ninja Road front/back swaps, gated by the active character's turn."""
from __future__ import annotations

import base64
from functools import lru_cache
import time

from automation import controller as farm


class BossSwap:
    def __init__(self, engine):
        self.engine = engine
        self.config = farm.read_json(farm.ROOT / "assets/patterns/ninja_road/boss_swap.json")
        self._references = {}
        self._active_masks = {}

    @property
    def data(self):
        return self.engine.data

    def save(self):
        self.engine.save()

    @staticmethod
    def pack(patch):
        ok, encoded = farm.cv2.imencode(".png", patch)
        if not ok:
            raise RuntimeError("Cannot persist Boss Swap visual guard")
        return base64.b64encode(encoded).decode("ascii")

    @staticmethod
    @lru_cache(maxsize=32)
    def unpack(value):
        return farm.cv2.imdecode(farm.np.frombuffer(base64.b64decode(value), farm.np.uint8), 0)

    @staticmethod
    def matches(left, right):
        if left.shape != right.shape:
            return False
        correlation = float(farm.cv2.matchTemplate(left, right, farm.cv2.TM_CCOEFF_NORMED)[0, 0])
        difference = float(farm.np.abs(left.astype(farm.np.int16) - right).mean())
        return correlation >= .96 and difference <= 12

    @staticmethod
    def crop(frame, region):
        x1, y1, x2, y2 = region
        return farm.cv2.cvtColor(frame[y1:y2, x1:x2], farm.cv2.COLOR_BGR2GRAY)

    def reference(self, frame, name):
        spec = self.config[name]
        path = farm.ROOT / spec["template"]
        signature = farm.Detector.file_signature([path])
        cached = self._references.get(name)
        if cached is None or cached[0] != signature:
            template = farm.cv2.imread(str(path), 0)
            if template is None:
                raise RuntimeError("Missing Boss Swap visual reference")
            if farm.Detector.file_signature([path]) != signature:
                raise RuntimeError("Boss Swap visual reference changed while loading")
            self._references[name] = (signature, template)
        else:
            template = cached[1]
        return self.matches(self.crop(frame, spec["region"]), template)

    def elements(self, frame, column):
        """Read both role badges, independent of the character portrait art."""
        result = []
        for x, y in ((86 + 295 * column, 1338), (31 + 295 * column, 1418)):
            hsv = farm.cv2.cvtColor(frame[y-8:y+8, x-8:x+8], farm.cv2.COLOR_BGR2HSV)
            hues = hsv[:, :, 0][(hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 100)]
            if len(hues) < 60:
                result.append(None)
                continue
            peak = int(farm.np.bincount(hues, minlength=180).argmax())
            offsets = (hues.astype(farm.np.int16) - peak + 90) % 180 - 90
            cluster = offsets[farm.np.abs(offsets) <= 12]
            result.append((peak + float(cluster.mean())) % 180
                          if len(cluster) >= .7 * len(hues) else None)
        return result

    @staticmethod
    def same_element(left, right):
        return (left is not None and right is not None
                and abs((left - right + 90) % 180 - 90) <= 12)

    def same_elements(self, current, original):
        return all(self.same_element(a, b) for a, b in zip(current, original))

    def inverted_elements(self, current, before):
        return (before and not self.same_element(*before)
                and self.same_elements(current, before[::-1]))

    def reserve(self, frame, column):
        return self.crop(frame, [20 + 295 * column, 1450, 130 + 295 * column, 1535])

    def face_fit(self, front, reserve):
        # Compare this pair's own art across the two portrait sizes. The active
        # rim changes size slightly; no particular character is a reference.
        if float(front.std()) < 12:
            return (-1, float("inf"))
        best = (-1, float("inf"))
        for scale in (.60, .62, .64, .66, .68, .70):
            reduced = farm.cv2.resize(front, None, fx=scale, fy=scale,
                                      interpolation=farm.cv2.INTER_AREA)
            scores = farm.cv2.matchTemplate(reserve, reduced, farm.cv2.TM_CCOEFF_NORMED)
            _, correlation, _, (x, y) = farm.cv2.minMaxLoc(scores)
            matched = reserve[y:y+reduced.shape[0], x:x+reduced.shape[1]]
            difference = float(farm.np.abs(matched.astype(farm.np.int16) - reduced).mean())
            if correlation > best[0]:
                best = (correlation, difference)
        return best

    def face_in_reserve(self, front, reserve, correlation_min=.90, difference_max=25):
        correlation, difference = self.face_fit(front, reserve)
        if correlation >= correlation_min and difference <= difference_max:
            return True
        # Charge rings cast a diagonal flare over the outer portrait and its
        # forehead. The same face's eyes, nose and mouth remain visible inside
        # both roles. Require a stronger match on that interior instead of
        # relaxing the whole-portrait guard for every character.
        correlation, difference = self.face_fit(front[24:72, 12:73], reserve)
        return correlation >= max(.96, correlation_min) and difference <= min(16, difference_max)

    def identical_pair(self, frame, column, elements):
        return (self.same_element(*elements)
                and self.face_in_reserve(self.crop(frame, self.config["portraits"][column]),
                                         self.reserve(frame, column), .93, 20))

    def _known_indistinguishable_restore(self, state, pending, frame, column,
                                       portrait, before, elements):
        # Entry already proved this pair's identity in the same run/team.
        # Buffs can obscure its badges and cross-size art later. Reuse that
        # proof only after one returned restore attempt and two unchanged roles.
        if (state["phase"] != "restore" or column not in state.get("indistinguishable", [])
                or pending.get("returned") is not True or not pending.get("reserve_before")):
            return False
        original_elements = state.get("original_elements", {}).get(str(column))
        saved_elements = pending.get("elements_before")
        for pair in (original_elements, saved_elements):
            if (not isinstance(pair, list) or len(pair) != 2
                    or any(type(hue) not in (int, float) for hue in pair)
                    or not self.same_element(*pair)):
                return False
        if not self.same_elements(original_elements, saved_elements):
            return False
        if any(current is not None and not self.same_element(current, original)
               for current, original in zip(elements, original_elements)):
            return False
        original = state.get("original", {}).get(str(column))
        return bool(original and self.same_portrait(portrait, before)
                    and self.matches(self.reserve(frame, column), self.unpack(pending["reserve_before"]))
                    and self.restored_portrait(portrait, self.unpack(original))
                    and self.settled_active(frame) == column)

    def inverted_pair(self, frame, column, portrait, before, pending, elements):
        if (not pending.get("reserve_before") or self.matches(portrait, before)
                or (pending.get("elements_before")
                    and not self.same_elements(elements, pending["elements_before"][::-1]))):
            return False
        reserve_before = self.unpack(pending["reserve_before"])
        baseline = self.face_fit(before, reserve_before)
        forward = self.face_fit(portrait, reserve_before)
        reverse = self.face_fit(before, self.reserve(frame, column))
        # Similar art can crossmatch even when only the front changed. Both
        # directions must fit better than the saved, unswapped pair itself.
        return all(correlation >= .90 and difference <= 25
                   and (correlation >= baseline[0] + .025 or difference <= baseline[1] - 6)
                   for correlation, difference in (forward, reverse))

    def changed_portrait(self, portrait, before):
        correlation = float(farm.cv2.matchTemplate(portrait, before, farm.cv2.TM_CCOEFF_NORMED)[0, 0])
        difference = float(farm.np.abs(portrait.astype(farm.np.int16) - before).mean())
        # Genuine swaps measured .035-.052 / 64.49-90.37, unchanged portraits
        # measured .999-1.000 / .02-1.65. Reject transient charge/glow changes.
        return correlation < .8 and difference >= 30

    def same_portrait(self, portrait, before):
        # The selected frame enlarges slightly as its turn starts; tolerate that
        # motion only when proving a rejected swipe after an observed info panel.
        correlation = float(farm.cv2.matchTemplate(portrait, before, farm.cv2.TM_CCOEFF_NORMED)[0, 0])
        difference = float(farm.np.abs(portrait.astype(farm.np.int16) - before).mean())
        return correlation >= .90 and difference <= 20

    def restored_portrait(self, portrait, original):
        # A boss-entry flash can tint the saved face. The live restored face
        # measured .883 / 20.1; wrong characters measured .04-.06 / 78-95.
        # This identity guard is used only alongside a proven portrait change.
        correlation = float(farm.cv2.matchTemplate(portrait, original, farm.cv2.TM_CCOEFF_NORMED)[0, 0])
        difference = float(farm.np.abs(portrait.astype(farm.np.int16) - original).mean())
        return correlation >= .85 and difference <= 30

    def auto(self, frame):
        if self.reference(frame, "auto_on"):
            return True
        if self.reference(frame, "auto_off"):
            return False
        return None

    def active(self, frame):
        # All turn guards live in this strip; converting the battlefield on
        # every poll adds work and makes terrain an unnecessary input.
        hsv = farm.cv2.cvtColor(frame[1270:1460], farm.cv2.COLOR_BGR2HSV)
        gold = farm.cv2.inRange(hsv, farm.np.array(self.config["gold_min"]),
                                farm.np.array(self.config["gold_max"]))
        candidates = []
        for column, region in enumerate(self.config["active_regions"]):
            x1, y1, x2, y2 = region
            if farm.cv2.countNonZero(gold[y1-1270:y2-1270, x1:x2]) < self.config["active_min_pixels"]:
                continue
            # Qualify every band candidate with the outer scroll shape before
            # rejecting ambiguity: a gold strip in another portrait is harmless.
            if column not in self._active_masks:
                y, x = farm.np.ogrid[1270:1460, 0:900]
                radius_squared = (x - (180 + 295 * column)) ** 2 + (y - 1418) ** 2
                self._active_masks[column] = (
                    (radius_squared >= 115 ** 2) & (radius_squared < 140 ** 2) & (y < 1450)
                    & ~((x < 115 + 295 * column) & (y < 1370))
                    & ~((x > 230 + 295 * column) & (y < 1370)))
            if farm.np.count_nonzero(gold[self._active_masks[column]]) >= 260:
                candidates.append(column)
        return candidates[0] if len(candidates) == 1 else None

    def complete(self, state, column, swapped=True):
        state["done"].append(column)
        if state["phase"] == "enter" and swapped:
            state["swapped"].append(column)
        state["pending"] = None
        state["advance"] = len(state["done"]) < len(state.get("targets", [0, 1, 2]))
        state["advance_from"] = column
        state["wait_since"] = time.time()

    def settled_active(self, frame):
        column = self.active(frame)
        now = time.monotonic()
        previous = getattr(self, "_turn_candidate", None)
        if column is None:
            self._turn_candidate = None
            return None
        if not previous or previous[0] != column or now - previous[2] > 10:
            self._turn_candidate = (column, now, now, 1)
            return None
        self._turn_candidate = (column, previous[1], now, previous[3] + 1)
        # Auto can stop during an attack with the outgoing frame still visible.
        return column if previous[3] >= 2 and now - previous[1] >= 2.5 else None

    def dispatch(self, state, action, purpose, **values):
        self._turn_candidate = None
        state["pending"] = {"purpose": purpose, "time": time.time(), **values}
        self.save()  # Stop/restart must never replay an uncertain swipe.
        farm.event("ninja_boss_swap_action", purpose=purpose, column=values.get("column"),
                   phase=state["phase"])
        try:
            self.engine.device.perform(action)
        except farm.ActionNotDispatched:
            state["pending"] = None
            if purpose == "pulse_on":
                state.pop("advanced", None)
                state.pop("skipped_from", None)
            self.save()
            raise
        state["pending"]["returned"] = True
        self.save()

    def toggle(self, state, purpose, **values):
        self.dispatch(state, {"id": "nr_auto", "kind": "tap", "point": [623, 56]}, purpose, **values)

    def pulse(self, state, retry=0):
        self.toggle(state, "pulse_on", retry=retry)
        # Observe ON before issuing OFF, in this same worker turn. The ordinary
        # worker polling delay would otherwise allow several characters to act.
        for _ in range(5):
            if farm.STOP.exists():
                return
            time.sleep(.15)
            frame = self.engine.device.capture()
            if self.auto(frame) is True:
                self.toggle(state, "pulse_off")
                return

    def tick(self, frame, name, spec):
        state = self.data.get("boss_swap")
        enabled = farm.boss_swap_enabled()
        if not state and not enabled:
            return None
        team_key = [(self.data.get("run") or {}).get("id"), self.data.get("current_team")]
        ended = name in {"NR_EARLY_EXIT_PREPARE", "NR_EARLY_EXIT", "NR_RETREAT_CONFIRM", "NR_TEAM_DEFEATED", "NR_ALL_DEFEATED", "MISSION_COMPLETE", "NR_RESULTS", "NR_RETURN", "NR_HOME", "NR_ENTRY", "NR_START"}
        if state and (ended or state["team_key"] != team_key):
            self.data.pop("boss_swap", None)
            self.save()
            state = None
        if ended or self.data.get("pending") or self.data.get("connection_pending"):
            return None
        if name == "NR_CHARACTER_INFO":
            if not state:
                return None  # Ordinary guarded recovery closes this panel.
            pending = state.get("pending")
            if pending and pending["purpose"] == "close_info":
                return .5  # Never repeat an uncertain close.
            if pending and pending["purpose"] != "swipe":
                return .5
            self.dispatch(state, spec["action"], "close_info", interrupted=pending)
            return .5
        ready = name == "NR_TEAM_SELECT"
        combat = name in {"NR_COMBAT", "NR_COMBAT_AUTO_OFF", "NR_COMBAT_NINJUTSU_OFF"}
        if not ready and not combat:
            return .5 if state else None
        if not state:
            if not enabled or not (self.reference(frame, "boss") or (ready and any(
                    self.reference(frame, counter) for counter in self.config.get("boss_ready_maps", [])))):
                return None
            map_patch = self.crop(frame, self.config["map_region"])
            state = {"phase": "enter", "team_key": team_key, "boss_map": self.pack(map_patch),
                     "swapped": [], "done": [], "original": {}, "pending": None}
            self.data["boss_swap"] = state
            self.save()
            farm.event("ninja_boss_swap_started", team=self.data.get("current_team"))
        pending = state.get("pending")
        if state["phase"] == "boss" and not ready and not pending:
            return None
        auto = self.auto(frame)
        if pending:
            purpose = pending["purpose"]
            if purpose == "close_info":
                interrupted = pending.get("interrupted")
                if interrupted:
                    column = interrupted["column"]
                    portrait = self.crop(frame, self.config["portraits"][column])
                    if self.same_portrait(portrait, self.unpack(interrupted["before"])):
                        state["rejected_column"] = column
                        state["pending"] = None
                        farm.event("ninja_boss_swap_rejected", column=column + 1)
                    else:
                        state["pending"] = interrupted
                else:
                    state["pending"] = None
                self.save()
                return .5
            elif purpose == "swipe":
                column = pending["column"]
                portrait = self.crop(frame, self.config["portraits"][column])
                before = self.unpack(pending["before"])
                elements = self.elements(frame, column)
                original_elements = state.get("original_elements", {}).get(str(column))
                restored = (state["phase"] != "restore"
                            or (self.restored_portrait(portrait, self.unpack(state["original"][str(column)]))
                                and (not original_elements or self.same_elements(elements, original_elements))))
                if (auto is False and self._known_indistinguishable_restore(
                        state, pending, frame, column, portrait, before, elements)):
                    self.complete(state, column)
                    self.data["blocked"] = None
                    farm.event("ninja_boss_swap_visually_indistinguishable", column=column + 1,
                               phase=state["phase"], legacy_guard=False, proof="persisted_roles")
                elif (auto is False and restored and (self.changed_portrait(portrait, before)
                        or self.inverted_elements(elements, pending.get("elements_before"))
                        or self.inverted_pair(frame, column, portrait, before, pending, elements))):
                    self.complete(state, column)
                    farm.event("ninja_boss_swap_verified", column=column + 1, phase=state["phase"])
                elif (auto is False and restored and pending.get("returned") and self.matches(portrait, before)
                      and (not pending.get("elements_before")
                           or (self.same_element(*pending["elements_before"])
                               and self.same_elements(elements, pending["elements_before"])))
                      and (not pending.get("reserve_before")
                           or self.face_in_reserve(before, self.unpack(pending["reserve_before"]), .93, 20))
                      and self.identical_pair(frame, column, elements)
                      and self.settled_active(frame) == column):
                    # Some distinct units share portrait art and element. One
                    # returned swipe was attempted, but its effect is visually
                    # indistinguishable. Never replay it; require one matching
                    # restoration attempt instead of skipping on face identity.
                    self.complete(state, column)
                    if column not in state.setdefault("indistinguishable", []):
                        state["indistinguishable"].append(column)
                    self.data["blocked"] = None
                    farm.event("ninja_boss_swap_visually_indistinguishable", column=column + 1,
                               phase=state["phase"], legacy_guard=not bool(pending.get("reserve_before")))
                elif time.time() - pending["time"] > 15:
                    key = f"{state['phase']}:{column}"
                    deferrals = state.get("swipe_deferrals", {})
                    if (pending.get("returned") and auto is False
                            and self.matches(portrait, before)
                            and (not pending.get("elements_before")
                                 or self.same_elements(elements, pending["elements_before"]))
                            and (not pending.get("reserve_before")
                                 or self.matches(self.reserve(frame, column), self.unpack(pending["reserve_before"])))
                            and not deferrals.get(key)
                            and self.settled_active(frame) is not None):
                        # A completed command plus the unchanged face proves
                        # no swap occurred. Do not replay it in this turn: defer
                        # this character until another actor has taken a turn.
                        state.setdefault("swipe_deferrals", {})[key] = 1
                        state["pending"] = None
                        state["rejected_column"] = column
                        self.data["blocked"] = None
                        self.save()
                        farm.event("ninja_boss_swap_no_effect", column=column + 1, phase=state["phase"])
                        return .5
                    self.data["blocked"] = "Boss Swap: portrait change not verified; swipe remains latched"
                    self.save()
                    return 2
                else:
                    return .5
            elif purpose in {"stop_auto", "pulse_off"} and auto is False:
                state["pending"] = None
            elif purpose == "pulse_off" and ready and auto is True:
                # A pulse starts in combat. A new Ready screen proves the map
                # ended across its OFF tap; converge from the verified ON state.
                farm.event("ninja_boss_swap_pulse_crossed_map")
                self.toggle(state, "stop_auto")
                return .1
            elif purpose == "pulse_on" and auto is True:
                self.toggle(state, "pulse_off")
                return .1
            elif (purpose == "pulse_on" and auto is False and pending.get("returned")
                  and time.time() - pending["time"] > 5):
                if pending.get("retry", 0) >= 1:
                    self.data["blocked"] = "Boss Swap: Auto ON retry was not verified; input remains latched"
                    self.save()
                    return 2
                if self.settled_active(frame) is not None:
                    farm.event("ninja_boss_swap_auto_on_rejected", retry=1)
                    self.pulse(state, retry=1)
                return .5
            elif purpose == "resume_auto" and auto is True:
                state["pending"] = None
                if state["phase"] == "restore":
                    self.data.pop("boss_swap", None)
                    self.save()
                    farm.event("ninja_boss_swap_restored", team=self.data.get("current_team"))
                    return None
                state["phase"] = "boss"
            else:
                if time.time() - pending["time"] > 15:
                    self.data["blocked"] = "Boss Swap: Auto transition not verified; input remains latched"
                    self.save()
                return .5
            self.save()
        if state["phase"] in {"enter", "boss"}:
            if ready and not self.matches(self.crop(frame, self.config["map_region"]), self.unpack(state["boss_map"])):
                state.update(phase="restore", done=[], targets=list(state["swapped"]), advance=False)
                # An Auto pulse may clear the boss before its rejected actor
                # changes. The next map needs fresh turn bookkeeping.
                for key in ("rejected_column", "skipped_from", "advanced"):
                    state.pop(key, None)
                self._turn_candidate = None
                self.save()
                farm.event("ninja_boss_swap_restoring", team=self.data.get("current_team"))
            elif state["phase"] == "boss" and not ready:
                return None
        if auto is None:
            return .5
        if ready and auto is True:
            self.toggle(state, "stop_auto")
            return .1
        if state["phase"] == "restore":
            for column in state["targets"]:
                if (column not in state["done"] and column not in state.get("indistinguishable", [])
                        and self.matches(self.crop(frame, self.config["portraits"][column]),
                                                              self.unpack(state["original"][str(column)]))
                        and (str(column) not in state.get("original_elements", {})
                             or self.same_elements(self.elements(frame, column), state["original_elements"][str(column)]))):
                    state["done"].append(column)
                    self.save()
                    farm.event("ninja_boss_swap_already_restored", column=column + 1)
        if len(state["done"]) == len(state.get("targets", [0, 1, 2])):
            if ready:
                # Even when no swaps remain, enter the next map with Auto OFF.
                # The normal combat settings action can resume it after Ready.
                if state["phase"] == "restore":
                    self.data.pop("boss_swap", None)
                    farm.event("ninja_boss_swap_restored", team=self.data.get("current_team"))
                else:
                    state["phase"] = "boss"
                self.save()
                farm.event("ninja_boss_swap_ready_auto_off", phase=state["phase"])
                return None
            if auto is False:
                self.toggle(state, "resume_auto")
                return .1
            if state["phase"] == "restore":
                self.data.pop("boss_swap", None)
                farm.event("ninja_boss_swap_restored", team=self.data.get("current_team"))
            else:
                state["phase"] = "boss"
            self.save()
            return None
        if auto is True:
            self.toggle(state, "stop_auto")
            return .1
        if ready:
            farm.event("ninja_boss_swap_ready_auto_off", phase=state["phase"])
            return None  # The existing, guarded Ready action enters manual combat.
        if state.get("advance") and not state.get("advanced"):
            # The accepted swipe and this frame already prove Auto is OFF.
            # Advance immediately; only the next swipe needs a settled marker.
            state["advanced"] = True
            self.pulse(state)
            return .1
        column = self.settled_active(frame)
        if column is None:
            return .5  # Attack/skill animations are not an active-character turn.
        if state.get("rejected_column") == column:
            if state.get("skipped_from") != column:
                state["skipped_from"] = column
                self.pulse(state)
            return .5
        state.pop("rejected_column", None)
        if state.get("advance"):
            if column != state["advance_from"]:
                state["advance"] = False
                state.pop("advanced", None)
                self.save()
            else:
                if time.time() - state["wait_since"] > 20:
                    self.data["blocked"] = "Boss Swap: waiting for the next active character"
                    self.save()
                return .5
        targets = state.get("targets", [0, 1, 2])
        if column in state["done"] or column not in targets:
            if state.get("skipped_from") != column:
                state["skipped_from"] = column
                self.pulse(state)
            return .5
        state.pop("skipped_from", None)
        state.pop("advanced", None)
        elements = self.elements(frame, column)
        portrait = self.pack(self.crop(frame, self.config["portraits"][column]))
        if state["phase"] == "enter":
            state["original"][str(column)] = portrait
            if all(hue is not None for hue in elements):
                state.setdefault("original_elements", {})[str(column)] = elements
        self.dispatch(state, self.config["swipes"][column], "swipe", column=column, before=portrait,
                      reserve_before=self.pack(self.reserve(frame, column)),
                      **({"elements_before": elements} if all(hue is not None for hue in elements) else {}))
        return .5
