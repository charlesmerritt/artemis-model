"""
Canonical age-class distributions from an FVS run.

One shape underlies every table this stage emits: *acres by age class, per reporting year,
cut by one or more attributes*. `distribution` builds it; the named helpers below pin the
cuts ARTEMIS reports on — forest type (pine against hardwood), owner class, management
type, and area.

Three decisions worth knowing before reading a number off one of these tables.

**Acres, not stands.** An FVS case is one FIA plot, and plots do not represent equal area.
Counting cases would make a 4-acre plot weigh as much as a 9,600-acre one. Every table is
acre-weighted, and `weight_basis` on each row names which acres: `sampling_weight` is the
FVS expansion acreage and covers the whole run; `owner_acres` is management-unit acreage
from the ownership-segmented run and covers only the plots inside its area of interest.
The two do not sum to the same total and must not be added together.

**Classes are spans, not points.** `age_class` is the lower bound of a `width`-year class
and `age_class_label` prints the span, with the top class open-ended (`140+`). A no-
management projection runs the right tail well past any closed top class.

**Age can be unknown.** FVS reports age 0 where FIA carried no condition age. Unless
`age_classes.unknown_age_is_class_zero` says otherwise these rows go to their own
`unknown` class, because folding them into the youngest class would invent regeneration
that the run never projected.

Usage:
    from pipeline.s6_outputs import age_class

    by_type  = age_class.by_forest_type(stand_years, years)
    by_owner = age_class.by_owner(owned_stand_years, years)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from pipeline.s4_fvs.fallback_treelists import forest_type_group, forest_type_group_code
from pipeline.s6_outputs.fvs_out_db import load_output_config, validate_landscape_cases

logger = logging.getLogger(__name__)

UNKNOWN_AGE = "unknown"
SAMPLING_WEIGHT = "sampling_weight"
OWNER_ACRES = "owner_acres"

# Sort key that keeps `unknown` last however the frame is ordered.
_UNKNOWN_SORT = 10**6


def age_class_bounds(config: dict | None = None) -> tuple[int, int]:
    config = config or load_output_config()
    classes = config["age_classes"]
    width, top = int(classes["width"]), int(classes["max"])
    if width <= 0:
        raise ValueError("age_classes.width must be positive")
    if top % width:
        raise ValueError(
            f"age_classes.max ({top}) must be a multiple of age_classes.width ({width}); "
            "otherwise the open top class starts mid-class and its label lies"
        )
    return width, top


def assign_age_class(ages, *, config: dict | None = None) -> pd.DataFrame:
    """
    Bin ages into `age_class` (lower bound, or `unknown`) and a printable label.

    Ages above `age_classes.max` collapse into the open top class; negative ages are not
    a thing FVS writes and raise rather than bin into a nonsense class.
    """
    config = config or load_output_config()
    width, top = age_class_bounds(config)
    zero_is_class = bool(config["age_classes"]["unknown_age_is_class_zero"])

    age = pd.to_numeric(pd.Series(ages).reset_index(drop=True), errors="coerce")
    if (age < 0).any():
        raise ValueError("FVS reported a negative stand age; the output database is wrong")

    unknown = age.isna() if zero_is_class else (age.isna() | (age == 0))
    lower = (np.floor(age / width) * width).clip(upper=top)

    label = lower.astype("Int64").astype("string") + "-" + \
        (lower + width - 1).astype("Int64").astype("string")
    label = label.where(lower != top, f"{top}+")

    return pd.DataFrame({
        "age_class": lower.astype("Int64").astype("string").where(~unknown, UNKNOWN_AGE),
        "age_class_label": label.where(~unknown, UNKNOWN_AGE),
        "age_class_sort": lower.where(~unknown, _UNKNOWN_SORT),
    })


def attribute(stand_years: pd.DataFrame, *, config: dict | None = None) -> pd.DataFrame:
    """Add the reporting attributes every canonical table groups on."""
    config = config or load_output_config()
    labels = config["forest_type_labels"]
    subgroup_labels = {int(k): v for k, v in config["forest_type_subgroup_labels"].items()}
    state_labels = config["areas"]["state_labels"]

    out = pd.concat(
        [stand_years.reset_index(drop=True),
         assign_age_class(stand_years["Age"], config=config)],
        axis=1,
    )
    broad = stand_years["ForTyp"].map(lambda c: forest_type_group(c))
    out["forest_type_group"] = broad.fillna("unknown").to_numpy()
    out["forest_type_label"] = out["forest_type_group"].map(labels).fillna("Unknown")

    subgroup = stand_years["ForTyp"].map(forest_type_group_code)
    out["fia_forest_type_subgroup"] = subgroup.astype("Int64").to_numpy()
    out["fia_forest_type_label"] = (
        subgroup.map(subgroup_labels)
        .fillna(subgroup.map(lambda c: f"FIA type {c}" if pd.notna(c) else "Unknown"))
        .to_numpy()
    )
    out["state"] = stand_years["state_fips"].map(state_labels).fillna(
        stand_years["state_fips"].radd("FIPS ")
    ).to_numpy()
    # No county-name table is declared anywhere in this repo, so a county is reported by
    # its five-digit FIPS with the state spelled out — enough to look up, and honest about
    # being a code rather than printing a name that was never joined.
    out["county"] = out["state"] + " " + stand_years["county_fips"].fillna("unknown")

    # The acre weights, under the names `distribution` stamps as the weight basis. A cut
    # that has no owner attribution still carries the sampling weight, so the overall and
    # owner tables can be reconciled case by case.
    out[SAMPLING_WEIGHT] = stand_years["SamplingWt"].to_numpy()
    return out


def distribution(attributed: pd.DataFrame, years, by, *,
                 weight: str = SAMPLING_WEIGHT) -> pd.DataFrame:
    """
    Acres by age class per year, cut by `by`, with each cut's within-year share.

    `weight` names the acre column *and* the basis stamped on every row. `share` is
    computed within (year, cut) so a stacked or faceted plot reads as a composition of
    that cut, not of the run; `acres` is what totals across cuts.
    """
    validate_landscape_cases(attributed)
    by = [by] if isinstance(by, str) else list(by)
    missing = [c for c in [*by, weight] if c not in attributed.columns]
    if missing:
        raise KeyError(f"cannot group by {', '.join(missing)}: column not in the frame")

    rows = attributed[attributed["Year"].isin(list(years))]
    if rows.empty:
        raise ValueError(f"no stand-years in the reporting grid {sorted(years)}")

    grouped = (
        rows.groupby(["Year", *by, "age_class", "age_class_label", "age_class_sort"],
                     dropna=False, observed=True)
        .agg(acres=(weight, "sum"), cases=("CaseID", "nunique"))
        .reset_index()
    )
    total = grouped.groupby(["Year", *by], observed=True)["acres"].transform("sum")
    grouped["share"] = np.where(total > 0, grouped["acres"] / total, np.nan)
    grouped["weight_basis"] = weight
    return grouped.sort_values(["Year", *by, "age_class_sort"]).reset_index(drop=True)


# ---- the canonical cuts ----------------------------------------------------------------

def overall(attributed, years):
    """Age-class distribution of the whole run — the total every other table foots to."""
    out = distribution(attributed, years, [], weight=SAMPLING_WEIGHT)
    return out


def by_forest_type(attributed, years):
    """Pine against hardwood (and the oak/pine middle), the split the report leads with."""
    return distribution(attributed, years, "forest_type_label", weight=SAMPLING_WEIGHT)


def by_forest_type_detail(attributed, years):
    """The FIA forest type groups inside those broad classes — longleaf/slash vs
    loblolly/shortleaf, oak/hickory vs oak/gum/cypress."""
    return distribution(attributed, years, ["forest_type_label", "fia_forest_type_label"],
                        weight=SAMPLING_WEIGHT)


def by_owner(attributed, years):
    """Age-class distribution within each owner class. Owner-acre basis."""
    return distribution(attributed, years, "owner_class", weight=OWNER_ACRES)


def by_owner_and_forest_type(attributed, years):
    """Both requested cuts at once: is corporate pine younger than private pine?"""
    return distribution(attributed, years, ["owner_class", "forest_type_label"],
                        weight=OWNER_ACRES)


def by_management_type(attributed, years):
    """Upland units against streamside management zones, which are never harvested."""
    return distribution(attributed, years, ["management_type", "forest_type_label"],
                        weight=OWNER_ACRES)


def by_state(attributed, years):
    """Age-class distribution per state, split by forest type inside each one."""
    return distribution(attributed, years, ["state", "forest_type_label"],
                        weight=SAMPLING_WEIGHT)


def by_county(attributed, years):
    """The same cut at county resolution — the finest area the output database supports."""
    return distribution(attributed, years, ["county", "county_fips", "forest_type_label"],
                        weight=SAMPLING_WEIGHT)


def mean_age_trajectory(attributed, years, by=None, *, weight: str = SAMPLING_WEIGHT):
    """
    Acre-weighted mean stand age per year — the age-class tables collapsed to one line.

    A no-management run's mean age rises by one year per year unless mortality or type
    change moves acres between classes, so this is the quickest read on whether a run did
    anything but age.
    """
    validate_landscape_cases(attributed)
    by = [] if by is None else ([by] if isinstance(by, str) else list(by))
    rows = attributed[attributed["Year"].isin(list(years))].copy()
    known = rows["Age"].notna() & (rows["Age"] > 0)
    rows["known_acres"] = rows[weight].where(known, 0.0)
    rows["age_acres"] = rows["Age"].where(known, 0.0) * rows["known_acres"]
    out = rows.groupby(["Year", *by], observed=True, dropna=False).agg(
        acres=("known_acres", "sum"), age_acres=("age_acres", "sum"),
    ).reset_index()
    out["mean_age"] = out["age_acres"] / out["acres"].where(out["acres"] > 0)
    out = out.drop(columns="age_acres")
    out["weight_basis"] = weight
    return out
