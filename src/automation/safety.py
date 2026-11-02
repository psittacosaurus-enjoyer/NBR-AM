"""Fail-closed policy for actions that could encounter premium-currency UI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class PearlSafetyError(ValueError):
    """An action cannot be proven safe from Pearl spending."""


PREMIUM_ACTION_MARKERS = (
    "pearl",
    "purchase",
    "refresh",
    "revive",
    "revival",
    "continue",
)

STAMINA_PROMPT = "STAMINA_PROMPT"
STAMINA_CANCEL_ACTION = "dismiss_stamina"
STAMINA_NO_TEMPLATE = "assets/patterns/rich_b/stamina_prompt/2.png"
STAMINA_NO_REGION = (200, 982, 399, 1054)
STAMINA_NO_CORRELATION = 0.96
STAMINA_NO_DIFFERENCE = 12
STAMINA_EXPECTED_STATES = {
    "universal": ("READY_TO_RETRY", "READY_TO_START"),
    "ninja_road": ("NR_START",),
}


def premium_action_markers(action_id: object) -> frozenset[str]:
    """Return premium-currency markers found in an action identifier."""
    if not isinstance(action_id, str):
        return frozenset()
    normalized = action_id.casefold()
    return frozenset(marker for marker in PREMIUM_ACTION_MARKERS if marker in normalized)


def is_premium_action_id(action_id: object) -> bool:
    """Whether an action identifier looks capable of premium-currency use."""
    return bool(premium_action_markers(action_id))


def require_pearl_free_action_id(action_id: object) -> None:
    """Reject malformed or premium-looking action identifiers."""
    if not isinstance(action_id, str) or not action_id.strip():
        raise PearlSafetyError("Action ID must be a non-empty string")
    markers = premium_action_markers(action_id)
    if markers:
        raise PearlSafetyError(
            "Premium-looking action ID refused: " + ", ".join(sorted(markers))
        )


def _exact_int_sequence(value: object, length: int, label: str) -> tuple[int, ...]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or len(value) != length or any(type(item) is not int for item in value)):
        raise PearlSafetyError(f"{label} must contain exactly {length} integers")
    return tuple(value)


def _exact_string_sequence(value: object, label: str) -> tuple[str, ...]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or any(not isinstance(item, str) for item in value)):
        raise PearlSafetyError(f"{label} must be a sequence of strings")
    return tuple(value)


def anchor_center(anchor: object) -> tuple[int, int]:
    """Return the same integer anchor center used by the image detector."""
    if not isinstance(anchor, Mapping):
        raise PearlSafetyError("Stamina No-button anchor is missing")
    x1, y1, x2, y2 = _exact_int_sequence(anchor.get("region"), 4, "Anchor region")
    if x1 >= x2 or y1 >= y2:
        raise PearlSafetyError("Anchor region is empty or reversed")
    return (x1 + x2) // 2, (y1 + y2) // 2


def require_pearl_safe_stamina_prompt(stage_id: object, state: object) -> None:
    """Require a recognized stamina dialog whose only action is the known No button.

    ``state`` is the classified state after point-anchor resolution. Requiring the
    resolved action point to equal the guarded anchor center prevents a catalog
    coordinate from authorizing a nearby Yes button.
    """
    if not isinstance(stage_id, str) or stage_id not in STAMINA_EXPECTED_STATES:
        raise PearlSafetyError(f"Unsupported stamina recovery stage: {stage_id!r}")
    if not isinstance(state, Mapping) or state.get("name") != STAMINA_PROMPT:
        raise PearlSafetyError("Expected a classified STAMINA_PROMPT state")
    if state.get("verified_safe") is not True or state.get("recovery") is not True:
        raise PearlSafetyError("Stamina cancellation is not verified recovery")
    if stage_id == "ninja_road" and state.get("phase") != "stamina":
        raise PearlSafetyError("Ninja Road stamina recovery has the wrong phase")

    action = state.get("action")
    if not isinstance(action, Mapping):
        raise PearlSafetyError("Stamina cancellation action is missing")
    require_pearl_free_action_id(action.get("id"))
    if action.get("id") != STAMINA_CANCEL_ACTION or action.get("kind") != "tap":
        raise PearlSafetyError("Stamina prompt does not use the approved cancel tap")
    if "point_offset" in action or "input_mode" in action:
        raise PearlSafetyError("Stamina cancel cannot use a point offset or input mode")
    expected = _exact_string_sequence(action.get("expected"), "Expected states")
    if expected != STAMINA_EXPECTED_STATES[stage_id]:
        raise PearlSafetyError("Stamina cancel has unexpected recovery targets")

    anchors = state.get("anchors")
    if not isinstance(anchors, list):
        raise PearlSafetyError("Stamina prompt anchors are missing")
    point_anchor = action.get("point_anchor")
    if type(point_anchor) is not int or not 0 <= point_anchor < len(anchors):
        raise PearlSafetyError("Stamina cancel has an invalid point anchor")
    no_anchor = anchors[point_anchor]
    if not isinstance(no_anchor, Mapping):
        raise PearlSafetyError("Stamina No-button anchor is malformed")
    if no_anchor.get("template") != STAMINA_NO_TEMPLATE:
        raise PearlSafetyError("Stamina cancel is not anchored to the known No button")
    if "search_region" in no_anchor or "alternatives" in no_anchor:
        raise PearlSafetyError("Stamina No-button anchor must be fixed and exact")
    region = _exact_int_sequence(no_anchor.get("region"), 4, "Anchor region")
    if region != STAMINA_NO_REGION:
        raise PearlSafetyError("Stamina No-button anchor region changed")
    if (no_anchor.get("correlation") != STAMINA_NO_CORRELATION
            or no_anchor.get("difference") != STAMINA_NO_DIFFERENCE):
        raise PearlSafetyError("Stamina No-button recognition guards changed")

    point = _exact_int_sequence(action.get("point"), 2, "Action point")
    if point != anchor_center(no_anchor):
        raise PearlSafetyError("Stamina cancel point is not the exact No-button center")


def require_pearl_safe_action(stage_id: object, screen: object, state: object) -> None:
    """Reject premium-looking actions and strictly validate stamina cancellation."""
    if not isinstance(state, Mapping):
        raise PearlSafetyError("Classified action state is missing")
    action = state.get("action")
    if not isinstance(action, Mapping):
        raise PearlSafetyError("Classified action is missing")
    require_pearl_free_action_id(action.get("id"))
    if screen != state.get("name"):
        raise PearlSafetyError("Classified screen and action state do not match")
    if screen == STAMINA_PROMPT:
        require_pearl_safe_stamina_prompt(stage_id, state)

