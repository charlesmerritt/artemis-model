"""
Owner-class assignment (policy layer over `config/ownership_policy.yaml`).

One source, one vocabulary: a unit's ``OWN_CODE`` is its Harris et al. (2025) RDS-2025-0045
raster value, and its owner class is that value's Harris forest class. Non-forest and water
come back :data:`MASKED`, to be dropped before FVS.

Everything here is a pure function of a mapping plus the config — no raster I/O.

Usage:
    uv run python -m pipeline.s3_management.owner_classes     # print the classes

    >>> from pipeline.s3_management.owner_classes import classify_owner
    >>> a = classify_owner({"OWN_CODE": 6})
    >>> a.owner_class, a.tpo_group
    ('federal', 'Federal (NF)')
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ownership_policy.yaml"

# Sentinel for units the pipeline must drop before FVS (Harris non_forest / water).
MASKED = "masked"

# Field aliases accepted on a unit mapping for the Harris value, in priority order.
_HARRIS_FIELDS = ("OWN_CODE", "harris_value")


@dataclass(frozen=True)
class OwnerAssignment:
    """One unit's resolved ownership: the class, its TPO budget group, and QA flags."""

    owner_class: str
    tpo_group: str | None
    flags: tuple[str, ...] = field(default_factory=tuple)

    def has(self, flag: str) -> bool:
        return flag in self.flags


@lru_cache(maxsize=None)
def load_ownership_policy(path: str | None = None) -> dict:
    """Load and cache `config/ownership_policy.yaml`."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _first_present(unit: Mapping, names: tuple[str, ...]):
    for name in names:
        if name in unit and unit[name] is not None:
            return unit[name]
    return None


def _as_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def harris_value_to_class(value: int | None, policy: dict | None = None) -> str:
    """Map a Harris raster value to its owner class, or :data:`MASKED`.

    ``None`` (no value on the unit) is ``"unknown"``. A number outside the Harris legend —
    the raster's nodata, or a code from another vocabulary — raises.
    """
    policy = policy or load_ownership_policy()
    if value is None:
        return "unknown"
    if value in policy["masked_harris_values"]:
        return MASKED
    for name, spec in policy["classes"].items():
        if value in spec["harris_values"]:
            return name
    raise ValueError(f"OWN_CODE {value} is not a Harris RDS-2025-0045 raster value (0-8)")


def _check_label(value: int | None, label, policy: dict) -> None:
    """Reject a row whose ``OWN_TYPE`` is not the Harris label for its ``OWN_CODE``."""
    if value is None or not isinstance(label, str):
        return
    expected = dict(zip(policy["masked_harris_values"], policy["masked_leto_labels"]))
    for spec in policy["classes"].values():
        expected.update((v, spec["leto_label"]) for v in spec["harris_values"])
    if expected.get(value) != label:
        raise ValueError(
            f"OWN_CODE {value} is labelled {label!r}, but Harris value {value} is "
            f"{expected.get(value)!r}: this table was not coded from the Harris RDS-2025-0045 "
            "raster (see config/ownership_policy.yaml)"
        )


def tpo_group_for(owner_class: str, policy: dict | None = None) -> str | None:
    """The `config/tpo_targets.yaml` owner group a class's volume is charged against."""
    policy = policy or load_ownership_policy()
    spec = policy["classes"].get(owner_class)
    return spec["tpo_group"] if spec else None


def classify_owner(unit: Mapping, policy: dict | None = None) -> OwnerAssignment:
    """
    Resolve one unit's owner class from its Harris raster value.

    ``unit`` is any mapping carrying ``OWN_CODE`` (or ``harris_value``). A unit with no
    value resolves to ``unknown`` with the ``unknown_ownership`` flag, so it still appears
    in the area accounting.
    """
    policy = policy or load_ownership_policy()
    value = _as_int(_first_present(unit, _HARRIS_FIELDS))
    _check_label(value, unit.get("OWN_TYPE"), policy)
    owner_class = harris_value_to_class(value, policy)
    if owner_class == MASKED:
        return OwnerAssignment(MASKED, None, ("masked",))
    flags = ("unknown_ownership",) if owner_class == "unknown" else ()
    return OwnerAssignment(owner_class, tpo_group_for(owner_class, policy), flags)


def classify_owners(units, policy: dict | None = None):
    """
    Row-wise assignment over a DataFrame/GeoDataFrame.

    Returns a copy with ``owner_class``, ``owner_group`` (the TPO group name the harvest
    scheduler budgets against), and ``owner_flags`` (semicolon-joined) added.
    """
    policy = policy or load_ownership_policy()
    df = units.copy()
    assignments = [classify_owner(row, policy) for _, row in df.iterrows()]
    df["owner_class"] = [a.owner_class for a in assignments]
    df["owner_group"] = [a.tpo_group for a in assignments]
    df["owner_flags"] = [";".join(a.flags) for a in assignments]
    return df


def main() -> None:
    policy = load_ownership_policy()
    print(f"ownership_policy.yaml v{policy['version']} — Harris RDS-2025-0045 classes")
    for name, spec in policy["classes"].items():
        print(f"  {name:<10} harris={spec['harris_values']}  tpo_group={spec['tpo_group']!r}")


if __name__ == "__main__":
    main()
