"""Helpers for women-captain ride preference (female passengers)."""

from __future__ import annotations

FEMALE_GENDER_VALUES = frozenset({"female", "f", "woman", "women"})

WOMEN_RIDERS_UNAVAILABLE_MESSAGE = (
    "No women captains are available nearby right now. "
    "Would you like to continue with other captains?"
)


def normalize_gender(value: str | None) -> str:
    return (value or "").strip().lower()


def is_female_gender(value: str | None) -> bool:
    return normalize_gender(value) in FEMALE_GENDER_VALUES


def ride_requires_women_captains(ride) -> bool:
    """True when this ride should only be offered to women captains."""
    return bool(getattr(ride, "prefer_women_riders", False)) and not bool(
        getattr(ride, "allow_all_riders", True)
    )
