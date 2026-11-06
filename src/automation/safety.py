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


TITLE_SCREEN = "TITLE_START"
TITLE_ACTION = "title_start"
TITLE_TAP_POINT = (450, 1008)
TITLE_REQUIRED_TEMPLATES = (
    "assets/patterns/boot/title_delete_cache.png",
    "assets/patterns/boot/title_data_transfer.png",
)
QUICKSAVE_SCREEN = "NR_QUICKSAVE_PROMPT"
QUICKSAVE_ACTION = "nr_resume_quicksave"
QUICKSAVE_TEXT_TEMPLATE = "assets/patterns/boot/nr_quicksave_text.png"
QUICKSAVE_YES_TEMPLATE = "assets/patterns/boot/nr_quicksave_yes.png"
QUICKSAVE_YES_REGION = (492, 884, 692, 954)
EXACT_CORRELATION = 0.96
EXACT_DIFFERENCE = 12


def _fixed_anchor_templates(state: Mapping) -> dict:
    anchors = state.get("anchors")
    if not isinstance(anchors, list):
        raise PearlSafetyError("State anchors are missing")
    found = {}
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            raise PearlSafetyError("State anchor is malformed")
        if "search_region" in anchor or "alternatives" in anchor or "binarize" in anchor:
            continue
        if (anchor.get("correlation") == EXACT_CORRELATION
                and anchor.get("difference") == EXACT_DIFFERENCE):
            found[anchor.get("template")] = anchor
    return found


def require_pearl_safe_title_tap(state: object) -> None:
    """The title tap must hit only the fixed Tap To Start text.

    The title screen also carries Delete Cache, Data Transfer and Discord
    buttons; both top buttons must be recognized at their exact positions so a
    different layout can never route this fixed point onto a button.
    """
    if not isinstance(state, Mapping) or state.get("name") != TITLE_SCREEN:
        raise PearlSafetyError("Expected a classified TITLE_START state")
    if state.get("verified_safe") is not True:
        raise PearlSafetyError("Title tap is not verified safe")
    action = state.get("action")
    if not isinstance(action, Mapping) or action.get("id") != TITLE_ACTION or action.get("kind") != "tap":
        raise PearlSafetyError("Title screen does not use the approved title tap")
    if "point_anchor" in action or "point_offset" in action or "input_mode" in action:
        raise PearlSafetyError("Title tap must use its fixed point")
    if _exact_int_sequence(action.get("point"), 2, "Action point") != TITLE_TAP_POINT:
        raise PearlSafetyError("Title tap point changed")
    anchors = _fixed_anchor_templates(state)
    if any(template not in anchors for template in TITLE_REQUIRED_TEMPLATES):
        raise PearlSafetyError("Title tap requires the exact title button anchors")


def require_pearl_safe_quicksave_resume(state: object) -> None:
    """Only the Yes button of the exact Ninja Road quicksave question."""
    if not isinstance(state, Mapping) or state.get("name") != QUICKSAVE_SCREEN:
        raise PearlSafetyError("Expected a classified NR_QUICKSAVE_PROMPT state")
    if state.get("verified_safe") is not True:
        raise PearlSafetyError("Quicksave resume is not verified safe")
    action = state.get("action")
    if not isinstance(action, Mapping) or action.get("id") != QUICKSAVE_ACTION or action.get("kind") != "tap":
        raise PearlSafetyError("Quicksave prompt does not use the approved resume tap")
    if "point_offset" in action or "input_mode" in action:
        raise PearlSafetyError("Quicksave resume cannot use a point offset or input mode")
    anchors = state.get("anchors")
    point_anchor = action.get("point_anchor")
    if not isinstance(anchors, list) or type(point_anchor) is not int or not 0 <= point_anchor < len(anchors):
        raise PearlSafetyError("Quicksave resume has an invalid point anchor")
    yes = anchors[point_anchor]
    if not isinstance(yes, Mapping) or yes.get("template") != QUICKSAVE_YES_TEMPLATE:
        raise PearlSafetyError("Quicksave resume is not anchored to the known Yes button")
    if "search_region" in yes or "alternatives" in yes:
        raise PearlSafetyError("Quicksave Yes anchor must be fixed and exact")
    if _exact_int_sequence(yes.get("region"), 4, "Anchor region") != QUICKSAVE_YES_REGION:
        raise PearlSafetyError("Quicksave Yes anchor region changed")
    if yes.get("correlation") != EXACT_CORRELATION or yes.get("difference") != EXACT_DIFFERENCE:
        raise PearlSafetyError("Quicksave Yes recognition guards changed")
    if QUICKSAVE_TEXT_TEMPLATE not in _fixed_anchor_templates(state):
        raise PearlSafetyError("Quicksave resume requires the exact quicksave question text")
    point = _exact_int_sequence(action.get("point"), 2, "Action point")
    if point != anchor_center(yes):
        raise PearlSafetyError("Quicksave resume point is not the exact Yes-button center")


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
    if screen == TITLE_SCREEN or action.get("id") == TITLE_ACTION:
        require_pearl_safe_title_tap(state)
    if screen == QUICKSAVE_SCREEN or action.get("id") == QUICKSAVE_ACTION:
        if stage_id != "ninja_road":
            raise PearlSafetyError("Quicksave resume is only verified for Ninja Road")
        require_pearl_safe_quicksave_resume(state)

