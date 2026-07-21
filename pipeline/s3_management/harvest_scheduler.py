"""
Constrained harvest scheduler (Phase 4.1).

Allocates harvests across management units, per FVS 5-year cycle, honouring the TPO volume
caps parsed by ``pipeline.s3_management.tpo_targets``. Follows
`notes/management-pipeline-plan.md` Step 4.1:

  - Priority: **oldest stand first** (the plan's chosen rule).
  - Each cycle, walk candidate units (those whose regime schedules a harvest that cycle) in
    priority order and harvest a unit only if doing so keeps **every active constraint
    dimension** within its remaining cycle budget — total, by county, by owner group, or a
    combination. Units are whole stands, so harvest is all-or-nothing per unit.
  - TPO caps are annual cubic feet; a cycle budget is ``annual × cycle_years``.

The scheduler is a pure allocator over a units table — it does not run FVS. It decides
*which* units harvest in *which* cycle within the caps; the managed FVS run and the volume
model that supplies ``removable_volume`` are separate steps.

Constraint dimensions can be enabled independently (the plan asks to study each in
isolation, then combined), via the ``dims`` argument.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CYCLE_YEARS = 5
TOTAL, COUNTY, OWNER = "total", "county", "owner_group"


def to_cycle_budget(annual_caps: dict[str, float], cycle_years: int = DEFAULT_CYCLE_YEARS) -> dict[str, float]:
    """Convert annual cuft caps to a per-cycle budget (annual × cycle length)."""
    return {k: float(v) * cycle_years for k, v in annual_caps.items()}


def _dim_key(unit: pd.Series, dim: str) -> str:
    """The budget key a unit consumes for a given constraint dimension."""
    if dim == TOTAL:
        return ""
    return str(unit[dim])


def _build_budgets(
    dims: Sequence[str],
    caps: dict[str, dict[str, float]],
    cycle_years: int,
) -> dict[str, dict[str, float]]:
    """Remaining-budget ledger per active dimension, in per-cycle cuft."""
    budgets: dict[str, dict[str, float]] = {}
    for dim in dims:
        if dim == TOTAL:
            budgets[TOTAL] = to_cycle_budget(caps.get(TOTAL, {}), cycle_years)
        else:
            if dim not in caps:
                raise ValueError(f"dimension {dim!r} is active but no caps were provided for it")
            budgets[dim] = to_cycle_budget(caps[dim], cycle_years)
    return budgets


def allocate_cycle(
    units: pd.DataFrame,
    caps: dict[str, dict[str, float]],
    dims: Sequence[str] = (TOTAL,),
    priority_col: str = "stand_age",
    volume_col: str = "removable_volume",
    cycle_years: int = DEFAULT_CYCLE_YEARS,
) -> pd.DataFrame:
    """
    Allocate a single cycle's harvest.

    ``units`` are the candidates for this cycle (already filtered to those whose regime cuts
    now), with at least ``unit_id``, ``priority_col``, ``volume_col``, and a column per
    active non-total dimension. ``caps`` maps each dimension to ``{key: annual_cuft}`` — for
    TOTAL the single key is ``""``. Returns ``units`` plus ``harvested`` (bool),
    ``volume_removed``, and ``blocked_by`` (the first dimension that had no room, or "").
    """
    for dim in dims:
        if dim != TOTAL and dim not in units.columns:
            raise ValueError(f"active dimension {dim!r} is missing from the units table")

    budgets = _build_budgets(dims, caps, cycle_years)

    ordered = units.sort_values(
        [priority_col, volume_col], ascending=[False, False], kind="stable"
    )

    harvested, removed, blocked = {}, {}, {}
    for idx, unit in ordered.iterrows():
        vol = float(unit[volume_col])
        block = ""
        for dim in dims:
            key = _dim_key(unit, dim)
            if budgets[dim].get(key, 0.0) < vol:
                block = dim
                break
        if block:
            harvested[idx], removed[idx], blocked[idx] = False, 0.0, block
        else:
            for dim in dims:
                budgets[dim][_dim_key(unit, dim)] -= vol
            harvested[idx], removed[idx], blocked[idx] = True, vol, ""

    out = units.copy()
    out["harvested"] = pd.Series(harvested)
    out["volume_removed"] = pd.Series(removed)
    out["blocked_by"] = pd.Series(blocked)
    logger.info(
        "Cycle allocation: %d/%d units harvested, %.0f cuft removed",
        int(out["harvested"].sum()), len(out), out["volume_removed"].sum(),
    )
    return out


def schedule_harvests(
    units_by_cycle: pd.DataFrame,
    caps: dict[str, dict[str, float]],
    dims: Sequence[str] = (TOTAL,),
    cycle_col: str = "cycle",
    priority_col: str = "stand_age",
    volume_col: str = "removable_volume",
    cycle_years: int = DEFAULT_CYCLE_YEARS,
) -> pd.DataFrame:
    """
    Run the allocator over every cycle. ``units_by_cycle`` is a long table with one row per
    (unit × cycle it is a harvest candidate in). Returns the concatenated per-cycle schedule.
    """
    frames = []
    for cycle, group in units_by_cycle.groupby(cycle_col, sort=True):
        allocated = allocate_cycle(
            group, caps, dims=dims, priority_col=priority_col,
            volume_col=volume_col, cycle_years=cycle_years,
        )
        frames.append(allocated)
    if not frames:
        return units_by_cycle.assign(harvested=pd.Series(dtype=bool),
                                     volume_removed=pd.Series(dtype=float),
                                     blocked_by=pd.Series(dtype=str))
    schedule = pd.concat(frames).sort_index()
    return schedule


def summarize_schedule(schedule: pd.DataFrame, cycle_col: str = "cycle") -> pd.DataFrame:
    """Harvested volume and unit count per cycle."""
    harvested = schedule[schedule["harvested"]]
    return (
        harvested.groupby(cycle_col)
        .agg(units_harvested=("unit_id", "count"), volume_removed=("volume_removed", "sum"))
        .reset_index()
    )
