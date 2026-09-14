"""Shared age eligibility for owner prescriptions and comparison scenarios.

The policy is read from management_regimes.yaml. Age is measured at inventory for
the current cohort; a restart caller must supply the new cohort's age and epoch.
This module resolves schedules before FVS runs, never shifts an existing trajectory.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
import math
from pathlib import Path

import yaml


class EligibilityMode(StrEnum):
    MINIMUM_STAND_AGE = "minimum_stand_age"


class UnderageAction(StrEnum):
    DEFER = "defer"


class UnknownAgeAction(StrEnum):
    EXCLUDE_MANAGED_CANDIDATE = "exclude_managed_candidate"


@dataclass(frozen=True)
class HarvestEligibilityPolicy:
    mode: EligibilityMode
    minimum_age_years: float
    underage_action: UnderageAction
    unknown_age_action: UnknownAgeAction

    @classmethod
    def from_config(cls, config: dict) -> "HarvestEligibilityPolicy":
        """Reject unsupported or incomplete policy instead of implying enforcement."""
        block = config.get("harvest_eligibility")
        required = {"mode", "minimum_age_years", "underage_action", "unknown_age_action"}
        if not isinstance(block, dict) or set(block) != required:
            raise ValueError(f"harvest_eligibility requires exactly {sorted(required)}")
        minimum = block["minimum_age_years"]
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
            raise ValueError("harvest_eligibility.minimum_age_years must be a number")
        if not math.isfinite(minimum) or minimum < 0:
            raise ValueError("harvest_eligibility.minimum_age_years must be finite and nonnegative")
        try:
            return cls(EligibilityMode(block["mode"]), float(minimum),
                       UnderageAction(block["underage_action"]),
                       UnknownAgeAction(block["unknown_age_action"]))
        except ValueError as exc:
            raise ValueError(f"harvest_eligibility: {exc}") from exc


@lru_cache(maxsize=1)
def load_harvest_eligibility() -> HarvestEligibilityPolicy:
    """Load the single canonical policy; callers with custom config parse it explicitly."""
    path = Path(__file__).resolve().parents[1] / "config" / "management_regimes.yaml"
    with path.open() as stream:
        return HarvestEligibilityPolicy.from_config(yaml.safe_load(stream))


def usable_stand_age(value) -> float | None:
    """Unknown ages cannot certify an operation's eligibility; zero is a valid age."""
    if isinstance(value, bool):
        return None
    try:
        age = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return age if math.isfinite(age) and age >= 0 else None


def validate_projection(cycle_years: int, horizon_years: int) -> None:
    """The FVS schedule uses positive integral cycles and a nonnegative horizon."""
    if type(cycle_years) is not int or cycle_years <= 0:
        raise ValueError("cycle_years must be a positive integer")
    if type(horizon_years) is not int or horizon_years < 0:
        raise ValueError("horizon_years must be a nonnegative integer")


def enforce_schedule(
    years: dict, *, stand_age, inv_year: int, cycle_years: int,
    horizon_years: int, policy: HarvestEligibilityPolicy,
) -> tuple[dict, tuple[str, ...]]:
    """Defer a whole sequence, preserving intervals, then clip to the run horizon.

    ``end_year`` is an inclusive repeated-entry window bound, not a harvest event.
    Keep it explicit so template defaults cannot create out-of-horizon operations.
    """
    validate_projection(cycle_years, horizon_years)
    entries = [year for key, year in years.items() if key != "end_year"]
    if not entries:
        return {}, ()
    age = usable_stand_age(stand_age)
    if age is None:
        return {}, ("harvest eligibility: excluded managed candidate: unknown stand age",)
    age_at_first_entry = age + min(entries) - inv_year
    delay = max(0, math.ceil((policy.minimum_age_years - age_at_first_entry) / cycle_years)) * cycle_years
    notes = []
    if delay:
        notes.append(f"harvest eligibility: deferred whole sequence by {delay} years "
                     f"to minimum stand age {policy.minimum_age_years:g}")
    shifted = {key: year + delay for key, year in years.items()}
    horizon_end = inv_year + horizon_years
    kept = {key: year for key, year in shifted.items() if year <= horizon_end}
    if "end_year" in shifted:
        last_cycle = inv_year + (horizon_years // cycle_years) * cycle_years
        kept["end_year"] = min(shifted["end_year"], last_cycle)
        if "start_year" not in kept or kept["start_year"] > kept["end_year"]:
            kept = {}
    if kept != shifted:
        notes.append(f"harvest eligibility: schedule clipped to {horizon_end} horizon")
    return kept, tuple(notes)
