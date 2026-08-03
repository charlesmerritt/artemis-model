"""
Management-regime assignment (Phase 3.2), driven by `config/management_regimes.yaml`.

Two questions, deliberately separated:

    **What is this unit assigned by default?**  :func:`assign_prescription` — one
    deterministic prescription per unit, from ``(owner class, forest type, riparian
    exposure, stand age)``. This is what generates keyfiles today.

    **What is this unit allowed to be?**  :func:`eligible_prescriptions` — the two or
    three prescriptions the landscape scheduler may choose among once the FVS trajectory
    library exists (`artemis.txt`: "stand × eligible management prescription"). Declaring
    the menu now means the trajectory library can be built for the right set from the
    start, instead of being regenerated when the scheduler arrives.

Owner classes come from `pipeline/s3_management/owner_classes.py`, which resolves the
Harris ownership raster against the parcel layer. The regime library, the per-owner menus,
and the scheduling rules are all in `config/management_regimes.yaml` — this module is the
resolver, not the policy.

Scheduling: prescriptions declare entries either by **stand age** (a 22-year-old plantation
on a 25-year rotation is cut in 3 years, not in 30) or by **fixed offsets** from the
inventory year. Age-based scheduling needs ``stand_age``; without it the prescription falls
back to its offsets, which is also what reproduces the pre-config behaviour exactly.

Ownership codes follow the LETO / RDS-2025-0045 lookup (3 Family, 4 Corporate/Other
Private, 5 Tribal, 6 Federal, 7 State, 8 Local). This is a documented policy for review,
not a calibrated behaviour model.

Usage:
    from pipeline.s3_management.regime_assignment import assign_prescription
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "stand_age": 22})
    p.prescription_id   # 'pine_plantation_short_rotation'
    p.params            # {'thin_year': 2027, 'clearcut_year': 2027, ...} → resolved
    p.regen_slot        # 'planted_pine_regen'
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from pipeline.s3_management.owner_classes import MASKED, classify_owner

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "management_regimes.yaml"

# LETO / RDS-2025-0045 ownership classes. Kept as module constants because other modules
# (and the LAMPS scheduler plan) import them directly.
FAMILY, CORPORATE, TRIBAL, FEDERAL, STATE, LOCAL = 3, 4, 5, 6, 7, 8
PUBLIC_OWNERS = {FEDERAL, STATE, TRIBAL, LOCAL}

# A unit with at least this share in a stream-management zone is treated as riparian.
# Riparian exclusion is absolute — see `overrides.riparian` in the config and
# notes/methodology-directions.md item 2.
RIPARIAN_SMZ_PCT = 50.0

# FIA forest-type-group codes that are pine (longleaf-slash 140s, loblolly-shortleaf 160s,
# other eastern softwoods 170s).
_PINE_FORTYP_MIN, _PINE_FORTYP_MAX = 140, 179
_PINE_WORDS = ("pine", "loblolly", "slash", "longleaf", "shortleaf")

# FIA hardwood forest-type groups (oak/hickory through other hardwoods).
_HARDWOOD_FORTYP_MIN, _HARDWOOD_FORTYP_MAX = 500, 998
_HARDWOOD_WORDS = ("oak", "hickory", "gum", "cypress", "maple", "beech", "hardwood",
                   "elm", "ash", "cottonwood", "tupelo", "bay")

PINE, HARDWOOD, OTHER = "pine", "hardwood", "other"


@dataclass(frozen=True)
class Prescription:
    """One unit's assigned prescription, resolved to a renderable template + parameters."""

    prescription_id: str
    template: str
    params: dict
    owner_class: str
    forest_type_branch: str
    regen_slot: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


@lru_cache(maxsize=None)
def load_regimes_config(path: str | None = None) -> dict:
    """Load and cache `config/management_regimes.yaml`."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---- forest type ----------------------------------------------------------------------

def is_pine(unit: Mapping) -> bool:
    """Heuristic: is the unit's forest type a pine type? Accepts a numeric FORTYPCD or a name."""
    code = unit.get("FORTYPCD", unit.get("forest_type_code"))
    if code is not None:
        try:
            return _PINE_FORTYP_MIN <= int(float(code)) <= _PINE_FORTYP_MAX
        except (TypeError, ValueError):
            pass
    name = unit.get("ForTypName", unit.get("forest_type", unit.get("FOREST_TYPE")))
    if isinstance(name, str):
        return any(w in name.lower() for w in _PINE_WORDS)
    return False


def is_hardwood(unit: Mapping) -> bool:
    """Counterpart to :func:`is_pine` for the hardwood forest-type groups."""
    code = unit.get("FORTYPCD", unit.get("forest_type_code"))
    if code is not None:
        try:
            return _HARDWOOD_FORTYP_MIN <= int(float(code)) <= _HARDWOOD_FORTYP_MAX
        except (TypeError, ValueError):
            pass
    name = unit.get("ForTypName", unit.get("forest_type", unit.get("FOREST_TYPE")))
    if isinstance(name, str):
        lower = name.lower()
        return any(w in lower for w in _HARDWOOD_WORDS) and not is_pine(unit)
    return False


def forest_type_branch(unit: Mapping) -> str:
    """Which default branch a unit takes: ``pine``, ``hardwood``, or ``other``.

    ``other`` covers the oak/pine group, nonstocked, and — importantly — units whose forest
    type is simply unknown, which is why every owner class declares an ``other`` default.
    """
    if is_pine(unit):
        return PINE
    if is_hardwood(unit):
        return HARDWOOD
    return OTHER


# ---- scheduling -----------------------------------------------------------------------

def _snap_to_cycle(years_out: float, cycle_years: int) -> int:
    """Round a lead time up to the next whole cycle, never sooner than one cycle out."""
    if years_out <= 0:
        return cycle_years
    return max(cycle_years, int(math.ceil(years_out / cycle_years)) * cycle_years)


def _stand_age(unit: Mapping) -> float | None:
    for key in ("stand_age", "STDAGE", "unit_age", "AGE"):
        if key in unit and unit[key] is not None:
            try:
                return float(unit[key])
            except (TypeError, ValueError):
                continue
    return None


def resolve_schedule(
    spec: dict,
    *,
    inv_year: int,
    cycle_years: int,
    horizon_years: int,
    stand_age: float | None,
) -> tuple[dict, tuple[str, ...]]:
    """
    Resolve a prescription's schedule into absolute entry years.

    Returns ``(year_params, notes)``. Age-based schedules place each entry at
    ``target_age - stand_age`` years out, snapped up to a cycle boundary; without a stand
    age they fall back to the prescription's offsets. Entries past the horizon are dropped
    and noted — the keyfile only runs to ``inv_year + horizon_years``.
    """
    schedule = spec["schedule"]
    mode = schedule["mode"]
    offsets = schedule.get("offsets", {})
    notes: list[str] = []
    horizon_end = inv_year + horizon_years

    if mode == "none":
        return {}, ()

    if mode == "age_based" and stand_age is None:
        mode = "offset_based"
        notes.append("age_based schedule fell back to offsets: no stand_age on the unit")

    if mode == "offset_based":
        years = {key: inv_year + offset for key, offset in offsets.items()}
    elif mode == "age_based":
        years = {}
        if "first_thin_age" in schedule:
            years["thin_year"] = inv_year + _snap_to_cycle(
                schedule["first_thin_age"] - stand_age, cycle_years
            )
        rotation_key = "clearcut_year" if "first_thin_age" in schedule else "year"
        years[rotation_key] = inv_year + _snap_to_cycle(
            schedule["rotation_years"] - stand_age, cycle_years
        )
        # An overmature stand whose thin lands at or after its rotation harvest does not
        # get thinned — it gets harvested. Dropping the thin here is what makes that
        # explicit rather than emitting two entries in the same cycle.
        if "thin_year" in years and years["thin_year"] >= years[rotation_key]:
            del years["thin_year"]
            notes.append("thin dropped: stand is at or past rotation age")
    else:
        raise ValueError(f"unknown schedule mode {mode!r} in management_regimes.yaml")

    kept = {key: year for key, year in years.items() if year <= horizon_end}
    if len(kept) < len(years):
        notes.append(f"{len(years) - len(kept)} entry/entries dropped past the {horizon_end} horizon")
    return kept, tuple(notes)


# The parameter names each template builder in pipeline/s4_fvs/regime_templates.py reads.
# Also serves as a config check: a `params:` key a template cannot consume is a typo, and
# silently passing it through would make the mistake invisible in the rendered keyfile.
_TEMPLATE_PARAMS = {
    "no_management": set(),
    "clearcut": {"year"},
    "thin_from_below": {"year", "max_dbh", "proportion"},
    "thin_from_below_repeated": {"start_year", "end_year", "interval", "proportion", "max_dbh"},
    "selection_harvest": {"start_year", "end_year", "interval", "proportion"},
    "plantation_rotation": {"thin_year", "clearcut_year", "thin_proportion", "thin_max_dbh"},
}


def _template_for(spec: dict, year_params: dict) -> str:
    """The template that actually renders, after schedule resolution may have dropped entries."""
    template = spec["template"]
    if not year_params:
        return "no_management"
    if template == "plantation_rotation" and "thin_year" not in year_params:
        # Only the final harvest survived; that is a clearcut, not a rotation.
        return "clearcut"
    return template


def _rename_for_template(template: str, year_params: dict) -> dict:
    """Map resolved year keys onto the parameter names each template builder expects."""
    if template == "clearcut":
        year = year_params.get("year", year_params.get("clearcut_year"))
        return {"year": year}
    if template == "plantation_rotation":
        return {"thin_year": year_params["thin_year"], "clearcut_year": year_params["clearcut_year"]}
    return dict(year_params)


# ---- assignment -----------------------------------------------------------------------

def eligible_prescriptions(owner_class: str, config: dict | None = None) -> list[str]:
    """
    The prescriptions the scheduler may choose among for an owner class.

    Includes `no_management` when the config marks it universally eligible — declining to
    harvest is always a legal choice, and for riparian units it is the only one.
    """
    config = config or load_regimes_config()
    spec = config["owner_classes"].get(owner_class)
    if spec is None:
        raise ValueError(
            f"unknown owner class {owner_class!r}; choices: {sorted(config['owner_classes'])}"
        )
    eligible = list(spec["eligible"])
    if config.get("no_management_universally_eligible") and "no_management" not in eligible:
        eligible.append("no_management")
    return eligible


def assign_prescription(
    unit: Mapping,
    inv_year: int | None = None,
    config: dict | None = None,
) -> Prescription:
    """
    Assign one unit's default prescription and resolve it to a renderable template.

    ``unit`` is any mapping. Recognised keys: ownership (``OWN_CODE`` and the parcel
    fields `owner_classes` reads), ``SMZ_Pct``, a forest-type field, and ``stand_age``.
    Missing fields degrade to the ``other`` branch and offset-based scheduling rather than
    raising — an unattributed unit still has to get a regime.
    """
    config = config or load_regimes_config()
    inv_year = config["inventory_year"] if inv_year is None else inv_year
    cycle_years = config["cycle_years"]
    horizon_years = config["horizon_years"]

    assignment = classify_owner(unit)
    owner_class = assignment.owner_class
    branch = forest_type_branch(unit)

    # Riparian is absolute and geometric: it precedes ownership entirely.
    smz = unit.get("SMZ_Pct", unit.get("smz_pct", 0.0)) or 0.0
    override = config["overrides"]["riparian"]
    if float(smz) >= RIPARIAN_SMZ_PCT:
        return Prescription(
            prescription_id=override["prescription"], template="no_management", params={},
            owner_class=owner_class, forest_type_branch=branch, regen_slot=None,
            notes=("riparian override: no entry, ever",),
        )

    if owner_class == MASKED:
        raise ValueError(
            "unit resolves to a masked ownership value (non-forest or water); mask these "
            "out before regime assignment — see config/projection.yaml `ownership.mask_values`"
        )

    prescription_id = config["owner_classes"][owner_class]["default"][branch]
    spec = config["prescriptions"][prescription_id]

    year_params, notes = resolve_schedule(
        spec, inv_year=inv_year, cycle_years=cycle_years,
        horizon_years=horizon_years, stand_age=_stand_age(unit),
    )
    template = _template_for(spec, year_params)
    if template == "no_management":
        params = {}
        if spec["template"] != "no_management":
            notes = (*notes, "resolved to no_management: no entry falls inside the horizon")
    else:
        params = {**_rename_for_template(template, year_params), **spec.get("params", {})}
        params = {k: v for k, v in params.items() if k in _TEMPLATE_PARAMS[template]}

    regen = spec.get("regen")
    regen_slot = regen["treelist_slot"] if regen and template != "no_management" else None

    return Prescription(
        prescription_id=prescription_id, template=template, params=params,
        owner_class=owner_class, forest_type_branch=branch, regen_slot=regen_slot,
        notes=(*notes, *assignment.flags),
    )


def assign_regime(unit: Mapping, inv_year: int = 2022) -> tuple[str, dict]:
    """
    Return ``(template_name, params)`` for a unit — the keyfile-facing view.

    Thin wrapper over :func:`assign_prescription` for callers that only need what
    `pipeline.s4_fvs.regime_templates.render_keyfile` consumes.
    """
    prescription = assign_prescription(unit, inv_year=inv_year)
    return prescription.template, prescription.params


def assign_regimes(units, inv_year: int = 2022):
    """Row-wise assignment over a DataFrame/GeoDataFrame; returns it with `regime` and
    `regime_params` columns added."""
    df = units.copy()
    assignments = [assign_regime(row, inv_year=inv_year) for _, row in df.iterrows()]
    df["regime"] = [a[0] for a in assignments]
    df["regime_params"] = [a[1] for a in assignments]
    return df


def assign_prescriptions(units, inv_year: int | None = None):
    """
    Row-wise :func:`assign_prescription` over a DataFrame/GeoDataFrame.

    Adds ``prescription``, ``regime`` (the template), ``regime_params``, ``owner_class``,
    ``regen_slot``, and ``assignment_notes``. ``owner_class`` and ``regen_slot`` are what
    the trajectory-library key and the regeneration restart need downstream.
    """
    df = units.copy()
    assignments = [assign_prescription(row, inv_year=inv_year) for _, row in df.iterrows()]
    df["prescription"] = [a.prescription_id for a in assignments]
    df["regime"] = [a.template for a in assignments]
    df["regime_params"] = [a.params for a in assignments]
    df["owner_class"] = [a.owner_class for a in assignments]
    df["regen_slot"] = [a.regen_slot for a in assignments]
    df["assignment_notes"] = [";".join(a.notes) for a in assignments]
    return df
