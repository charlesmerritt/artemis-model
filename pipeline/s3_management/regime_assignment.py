"""
Management-regime assignment (Phase 3.2), driven by `config/management_regimes.yaml`.

The default choice feeds the greedy baseline; the eligible menu feeds trajectory-library
generation and, ultimately, simulated-annealing selection. Riparian units expose only
``no_management``, so the no-entry rule is structural rather than a weighted preference.

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


def _riparian_override(config: dict | None = None) -> dict:
    """The riparian override block, validated. Absolute is the only supported mode."""
    override = (config or load_regimes_config())["overrides"]["riparian"]
    if not override.get("absolute", False):
        raise ValueError(
            "overrides.riparian.absolute must be true — riparian exclusion has no "
            "non-absolute path to fall back to. See notes/methodology-directions.md item 2."
        )
    return override


def _is_riparian(unit: Mapping, override: dict) -> bool:
    """Whether a unit trips the riparian override, per the field and threshold in config."""
    field = override["field"]
    value = unit.get(field, unit.get(field.lower(), 0.0)) or 0.0
    try:
        return float(value) >= float(override["min_value"])
    except (TypeError, ValueError):
        return False


# A unit with at least this share in a stream-management zone is treated as riparian.
# Read from `overrides.riparian.min_value` rather than duplicated here, so editing the
# config is what changes the behaviour. Kept as a module constant because other modules
# and the LAMPS scheduler plan import it directly.
RIPARIAN_SMZ_PCT = float(_riparian_override()["min_value"])


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
    """
    The unit's stand age, or ``None`` when it is missing or unusable.

    Three things count as unusable, and all three arrive from real data rather than from
    contrived inputs:

      - ``NaN``. A pandas row with no age carries ``NaN``, not ``None``, and ``float(NaN)``
        succeeds — so without this check the age would flow into ``math.ceil(NaN)`` and
        abort the whole assignment instead of taking the documented offset fallback.
      - Infinity, for the same reason.
      - A negative age, which is an FIA sentinel rather than a measurement. Treating it as
        a real age puts the rotation harvest before the inventory year.

    All three fall back to the prescription's offsets, which is exactly what a missing age
    does.
    """
    for key in ("stand_age", "STDAGE", "unit_age", "AGE"):
        if key in unit and unit[key] is not None:
            try:
                age = float(unit[key])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(age) or age < 0:
                return None
            return age
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
        years = {
            key: inv_year + _snap_to_cycle(offset, cycle_years)
            for key, offset in offsets.items()
        }
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


# The entry years each template cannot render without. Checked symmetrically, because the
# horizon filter can drop either half of a pair: a rotation whose thin lands inside the
# horizon but whose harvest does not is just as reachable as the reverse.
_TEMPLATE_REQUIRED_YEARS = {
    "no_management": set(),
    "clearcut": {"year"},
    "thin_from_below": {"year"},
    "thin_from_below_repeated": {"start_year"},
    "selection_harvest": {"start_year"},
    "plantation_rotation": {"thin_year", "clearcut_year"},
}


def _template_for(spec: dict, year_params: dict) -> str:
    """
    The template that actually renders, after schedule resolution may have dropped entries.

    Downgrades rather than rendering a template whose required years are incomplete. A
    rotation that kept only its final harvest is a clearcut; one that kept only its thin is
    a thin; one that kept neither is no management. Asking for a missing year instead would
    raise ``KeyError`` deep in parameter assembly.
    """
    template = spec["template"]
    if not year_params:
        return "no_management"

    if template == "plantation_rotation":
        has_thin = "thin_year" in year_params
        has_clearcut = "clearcut_year" in year_params
        if has_thin and has_clearcut:
            return template
        if has_clearcut:
            return "clearcut"          # only the final harvest survived
        if has_thin:
            return "thin_from_below"   # only the commercial thin survived
        return "no_management"

    if not _TEMPLATE_REQUIRED_YEARS[template] <= set(year_params):
        return "no_management"
    return template


def _rename_for_template(template: str, year_params: dict) -> dict:
    """Map resolved year keys onto the parameter names each template builder expects."""
    if template == "clearcut":
        return {"year": year_params.get("year", year_params.get("clearcut_year"))}
    if template == "thin_from_below":
        # A downgraded rotation carries its entry as `thin_year`; a native thin as `year`.
        return {"year": year_params.get("year", year_params.get("thin_year"))}
    if template == "plantation_rotation":
        return {"thin_year": year_params["thin_year"], "clearcut_year": year_params["clearcut_year"]}
    return dict(year_params)


# ---- assignment -----------------------------------------------------------------------

def eligible_prescriptions(
    owner_class: str,
    forest_branch: str | None = None,
    config: dict | None = None,
) -> list[str]:
    """
    The prescriptions the scheduler may choose among for an owner class.

    ``forest_branch`` (``pine`` / ``hardwood`` / ``other``) filters the menu by each
    prescription's ``forest_types``. Pass it for any stand-level call: without it an
    industrial *hardwood* stand is offered both pine-plantation prescriptions, and
    selecting one would apply pine thinning parameters and inject a planted-pine
    regeneration tree list into a hardwood trajectory. Omitting it returns the owner's
    whole menu, which is only meaningful for describing the policy rather than scheduling
    a stand.

    Prescriptions marked ``forest_types: [any]`` survive every branch, and `no_management`
    is appended when the config marks it universally eligible — declining to harvest is
    always a legal choice, and for riparian units it is the only one.
    """
    config = config or load_regimes_config()
    spec = config["owner_classes"].get(owner_class)
    if spec is None:
        raise ValueError(
            f"unknown owner class {owner_class!r}; choices: {sorted(config['owner_classes'])}"
        )

    eligible = list(spec["eligible"])
    if forest_branch is not None:
        if forest_branch not in (PINE, HARDWOOD, OTHER):
            raise ValueError(
                f"unknown forest branch {forest_branch!r}; choices: {[PINE, HARDWOOD, OTHER]}"
            )
        eligible = [
            name for name in eligible
            if _prescription_allows_branch(config["prescriptions"][name], forest_branch)
        ]

    if config.get("no_management_universally_eligible") and "no_management" not in eligible:
        eligible.append("no_management")
    return eligible


def _prescription_allows_branch(spec: dict, branch: str) -> bool:
    """Whether a prescription's declared ``forest_types`` admit a forest branch.

    The ``other`` branch is unknown-or-mixed forest type, so only ``any`` prescriptions
    admit it — a pine rotation on a stand that may not be pine is a guess, not a policy.
    """
    forest_types = spec.get("forest_types", ["any"])
    if "any" in forest_types:
        return True
    return branch in forest_types


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

    # Masking comes first, ahead of every override. Non-forest and water are not land the
    # model projects at all, so they must never receive an assignment — not even
    # `no_management`, which would put water in the growth outputs as an unharvested stand.
    # Riparian is an absolute rule *among forested land*, not a reason to admit masked land.
    if owner_class == MASKED:
        raise ValueError(
            "unit resolves to a masked ownership value (non-forest or water); mask these "
            "out before regime assignment — see config/projection.yaml `ownership.mask_values`"
        )

    # Riparian is absolute and geometric: among forested land it precedes ownership.
    override = config["overrides"]["riparian"]
    if _is_riparian(unit, override):
        return Prescription(
            prescription_id=override["prescription"], template="no_management", params={},
            owner_class=owner_class, forest_type_branch=branch, regen_slot=None,
            notes=("riparian override: no entry, ever",),
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
