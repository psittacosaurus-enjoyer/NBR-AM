"""Offline Pearl-safety policy tests; no controller, ADB, or device access."""

import copy
from pathlib import Path
import unittest

import cv2

from automation.controller import Detector
from automation.safety import (
    PearlSafetyError,
    STAMINA_EXPECTED_STATES,
    STAMINA_NO_CORRELATION,
    STAMINA_NO_DIFFERENCE,
    STAMINA_NO_REGION,
    STAMINA_NO_TEMPLATE,
    anchor_center,
    is_premium_action_id,
    premium_action_markers,
    require_pearl_free_action_id,
    require_pearl_safe_action,
    require_pearl_safe_stamina_prompt,
)


def stamina_state(stage="universal"):
    no_anchor = {
        "region": list(STAMINA_NO_REGION),
        "template": STAMINA_NO_TEMPLATE,
        "correlation": STAMINA_NO_CORRELATION,
        "difference": STAMINA_NO_DIFFERENCE,
    }
    return {
        "name": "STAMINA_PROMPT",
        "verified_safe": True,
        "recovery": True,
        "phase": "stamina" if stage == "ninja_road" else None,
        "anchors": [
            {"region": [121, 510, 778, 586], "template": "question.png"},
            {"region": [114, 630, 795, 899], "template": "dialog.png"},
            no_anchor,
        ],
        "action": {
            "id": "dismiss_stamina",
            "kind": "tap",
            "point": list(anchor_center(no_anchor)),
            "point_anchor": 2,
            "expected": list(STAMINA_EXPECTED_STATES[stage]),
        },
    }


class PearlSafetyTests(unittest.TestCase):
    def test_saved_stamina_fixture_resolves_to_the_guarded_no_center(self):
        root = Path(__file__).resolve().parents[1]
        frame = cv2.imread(str(root / "tests/fixtures/rich_b/not_enough_stamina.png"))
        self.assertIsNotNone(frame)
        name, state, _ = Detector("universal").classify(frame)
        self.assertEqual(name, "STAMINA_PROMPT")
        self.assertEqual(state["action"]["point"], list(anchor_center(state["anchors"][2])))
        require_pearl_safe_action("universal", name, state)

    def test_premium_looking_action_ids_are_exposed_and_rejected(self):
        cases = {
            "spend_pearl": "pearl",
            "confirmPurchase": "purchase",
            "refresh_stamina": "refresh",
            "revive_team": "revive",
            "REVIVAL_CONFIRM": "revival",
            "continueMission": "continue",
        }
        for action_id, marker in cases.items():
            with self.subTest(action_id=action_id):
                self.assertTrue(is_premium_action_id(action_id))
                self.assertIn(marker, premium_action_markers(action_id))
                with self.assertRaises(PearlSafetyError):
                    require_pearl_free_action_id(action_id)

    def test_known_free_action_ids_are_allowed(self):
        for action_id in ("dismiss_stamina", "retry_connection", "start_mission"):
            with self.subTest(action_id=action_id):
                self.assertFalse(is_premium_action_id(action_id))
                self.assertEqual(premium_action_markers(action_id), frozenset())
                require_pearl_free_action_id(action_id)
        for action_id in (None, "", "   ", 7):
            with self.subTest(action_id=action_id):
                with self.assertRaises(PearlSafetyError):
                    require_pearl_free_action_id(action_id)

    def test_exact_universal_and_ninja_road_cancellations_are_safe(self):
        for stage in STAMINA_EXPECTED_STATES:
            with self.subTest(stage=stage):
                state = stamina_state(stage)
                require_pearl_safe_stamina_prompt(stage, state)
                require_pearl_safe_action(stage, "STAMINA_PROMPT", state)

    def test_stamina_cancel_policy_fails_closed_on_mutations(self):
        mutations = {
            "unverified": lambda state: state.update(verified_safe=False),
            "not recovery": lambda state: state.update(recovery=False),
            "wrong screen": lambda state: state.update(name="UNKNOWN"),
            "premium action": lambda state: state["action"].update(id="refresh_stamina"),
            "wrong action": lambda state: state["action"].update(id="retry"),
            "wrong kind": lambda state: state["action"].update(kind="swipe"),
            "wrong targets": lambda state: state["action"].update(expected=["READY_TO_START"]),
            "point offset": lambda state: state["action"].update(point_offset=[0, 0]),
            "input mode": lambda state: state["action"].update(input_mode="adb_tap"),
            "static nearby point": lambda state: state["action"].update(point=[300, 1017]),
            "wrong point anchor": lambda state: state["action"].update(point_anchor=1),
            "boolean point anchor": lambda state: state["action"].update(point_anchor=True),
            "wrong template": lambda state: state["anchors"][2].update(template="yes.png"),
            "moved anchor": lambda state: state["anchors"][2].update(region=[500, 982, 699, 1054]),
            "search anchor": lambda state: state["anchors"][2].update(search_region=[0, 0, 900, 1600]),
            "template alternative": lambda state: state["anchors"][2].update(alternatives=["yes.png"]),
            "weak correlation": lambda state: state["anchors"][2].update(correlation=0.5),
            "weak difference": lambda state: state["anchors"][2].update(difference=100),
            "malformed targets": lambda state: state["action"].update(expected=None),
            "malformed region": lambda state: state["anchors"][2].update(region=None),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                state = stamina_state()
                mutate(state)
                with self.assertRaises(PearlSafetyError):
                    require_pearl_safe_stamina_prompt("universal", state)

    def test_ninja_road_requires_its_exact_phase_and_targets(self):
        for mutation in (
                lambda state: state.pop("phase"),
                lambda state: state.update(phase="unverified"),
                lambda state: state["action"].update(expected=["READY_TO_START"]),
        ):
            state = stamina_state("ninja_road")
            mutation(state)
            with self.assertRaises(PearlSafetyError):
                require_pearl_safe_stamina_prompt("ninja_road", state)

    def test_unknown_stage_and_non_mapping_state_are_rejected(self):
        with self.assertRaises(PearlSafetyError):
            require_pearl_safe_stamina_prompt("other", stamina_state())
        with self.assertRaises(PearlSafetyError):
            require_pearl_safe_stamina_prompt("universal", None)
        with self.assertRaises(PearlSafetyError):
            require_pearl_safe_action("universal", "READY_TO_START", None)

    def test_generic_gate_rejects_premium_action_on_any_screen(self):
        state = copy.deepcopy(stamina_state())
        state["name"] = "OTHER"
        state["action"]["id"] = "confirm_pearl_purchase"
        with self.assertRaises(PearlSafetyError):
            require_pearl_safe_action("universal", "OTHER", state)

    def test_generic_gate_rejects_mismatched_screen_metadata(self):
        state = stamina_state()
        with self.assertRaises(PearlSafetyError):
            require_pearl_safe_action("universal", "READY_TO_START", state)


if __name__ == "__main__":
    unittest.main()
