"""
Default management-regime assignment (Phase 3.2) — one regime per unit.

.. note::
   **Architecture status (2026-08-06).** Ownership class now defines a stand's *eligible
   set* of prescriptions, not its single regime: ARTEMIS builds a trajectory library per
   stand and a simulated-annealing scheduler selects within it. See
   `notes/trajectory-library-and-annealing.md` section 3, and
   `config/prescriptions.yaml` for the authoritative ownership -> eligible-set mapping.

   The eligible-set expander (``assign_eligible_prescriptions``) is not implemented yet.
   This deterministic single-regime rule stays in place meanwhile, and keeps a permanent
   job afterwards: it is what the greedy baseline schedule is built from
   (`harvest_scheduler.py`). Read what follows as "the default choice for a unit", not as
   "the only prescription a unit may receive".

   The riparian rule below is the one part that is *not* superseded — it becomes stronger.
   In the new design a riparian unit's library contains only ``no_management``, so no-entry
   is enforced by the absence of an alternative rather than by this rule firing first.

Maps each management unit to a silvicultural regime from
``pipeline.s4_fvs.regime_templates`` using a simple, deterministic rule on ownership,
forest type, and riparian exposure (from `notes/management-pipeline-plan.md` Step 3.2):

    - Riparian units (high SMZ %)          → no_management (BMP protection)
    - Federal / State / Tribal / Local     → selection_harvest (conservative public)
    - Family forest                        → thin_from_below (light thinning)
    - Corporate / other private, pine      → plantation_rotation
    - Corporate / other private, hardwood  → clearcut
    - Unknown ownership                     → thin_from_below (light default)

Ownership codes follow the LETO / RDS-2025-0045 lookup (3 Family, 4 Corporate/Other
Private, 5 Tribal, 6 Federal, 7 State, 8 Local). This is a first-draft policy for review,
not a calibrated behaviour model — the regime *parameters* are defaults keyed off the
inventory year.
"""

from __future__ import annotations

from collections.abc import Mapping

# LETO / RDS-2025-0045 ownership classes.
FAMILY, CORPORATE, TRIBAL, FEDERAL, STATE, LOCAL = 3, 4, 5, 6, 7, 8
PUBLIC_OWNERS = {FEDERAL, STATE, TRIBAL, LOCAL}

# A unit with at least this share in a stream-management zone is treated as riparian.
RIPARIAN_SMZ_PCT = 50.0

# FIA forest-type-group codes that are pine (loblolly-shortleaf, longleaf-slash).
_PINE_FORTYP_MIN, _PINE_FORTYP_MAX = 140, 179
_PINE_WORDS = ("pine", "loblolly", "slash", "longleaf", "shortleaf")


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


def _owner_code(unit: Mapping) -> int | None:
    code = unit.get("OWN_CODE", unit.get("owner_code"))
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def assign_regime(unit: Mapping, inv_year: int = 2022) -> tuple[str, dict]:
    """
    Return ``(regime_name, params)`` for a unit.

    ``unit`` is any mapping with (optionally) ``OWN_CODE``, ``SMZ_Pct``, and a forest-type
    field. Missing fields degrade gracefully to the light-thinning default.
    """
    smz = unit.get("SMZ_Pct", unit.get("smz_pct", 0.0)) or 0.0
    if float(smz) >= RIPARIAN_SMZ_PCT:
        return "no_management", {}

    owner = _owner_code(unit)

    if owner in PUBLIC_OWNERS:
        return "selection_harvest", {
            "start_year": inv_year + 10, "end_year": inv_year + 40,
            "interval": 10, "proportion": 0.20,
        }
    if owner == FAMILY:
        return "thin_from_below", {"year": inv_year + 10, "max_dbh": 8.0, "proportion": 0.35}
    if owner == CORPORATE:
        if is_pine(unit):
            return "plantation_rotation", {
                "thin_year": inv_year + 15, "thin_proportion": 0.40,
                "thin_max_dbh": 8.0, "clearcut_year": inv_year + 30,
            }
        return "clearcut", {"year": inv_year + 30}

    # Unknown / non-forest ownership → conservative light thin.
    return "thin_from_below", {"year": inv_year + 10, "max_dbh": 8.0, "proportion": 0.35}


def assign_regimes(units, inv_year: int = 2022):
    """Row-wise assignment over a DataFrame/GeoDataFrame; returns it with `regime` and
    `regime_params` columns added."""
    df = units.copy()
    assignments = [assign_regime(row, inv_year=inv_year) for _, row in df.iterrows()]
    df["regime"] = [a[0] for a in assignments]
    df["regime_params"] = [a[1] for a in assignments]
    return df
