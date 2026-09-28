"""
Put an owner class on a plot-keyed FVS trajectory.

`FVSOut.db` carries no ownership: a case is a FIA plot projected forward, and FIA plots do
not partition by owner. The ownership-segmented run at
`raw.hard_ownership_boundaries.stand_init_csv` does — it cuts management units at hard
ownership boundaries and records, for every unit, the `PLT_CN` that initialized it, its
`Acres`, its `OWN_TYPE` (Private / Corporate / Federal / State / County / NGO / Other /
Unknown) and its `MGMT_TYPE` (Upland / Riparian).

**A plot is not one owner.** TreeMap imputes the same plot onto units all over the
landscape, and in the five-county run 319 of the 375 crosswalked plots initialize units in
more than one owner class. So this module does not pick a dominant owner and attach it to
the trajectory — that would assign a plot's whole expansion to whichever class happened to
hold the most acres. It computes *acre shares*: plot 4489…9998 is 42% Private, 31%
Corporate, 27% State, and its trajectory contributes to all three in those proportions.
`apportion` is what applies them, and the acres it hands back are the ownership run's own
management-unit acres, not the FVS sampling weight — a different weight basis, declared as
such in `config/fvs_outputs.yaml` and carried on every emitted table.

Plots the crosswalk does not cover (cases outside the segmentation run's area of interest)
are reported under `owner_classes.unattributed_label` rather than dropped, so an owner
table still foots to every case in the run.

Usage:
    from pipeline.s6_outputs import owner_attribution

    shares = owner_attribution.load_owner_shares(stand_init_csv)
    owned = owner_attribution.apportion(stand_years, shares)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from pipeline.ids import as_id_series, report_key_overlap
from pipeline.s6_outputs.fvs_out_db import load_output_config

logger = logging.getLogger(__name__)

# Columns read from the ownership run's stand-init table. Everything else in it describes
# the unit's inventory, which this stage takes from FVS output instead.
CROSSWALK_COLUMNS = ("PLT_CN", "OWN_TYPE", "MGMT_TYPE", "Acres")

SHARE_COLUMNS = ["Stand_CN", "owner_class", "management_type", "owner_acres", "owner_share"]


class OwnerCrosswalkError(RuntimeError):
    """The ownership crosswalk cannot be used to attribute this run."""


def load_owner_shares(stand_init_csv: str | Path) -> pd.DataFrame:
    """
    Acre shares per (plot, owner class, management type), summing to 1.0 within a plot.

    `PLT_CN` is read as text and normalized, never parsed as a number: it is a 15-digit
    FIA control number and a float64 round-trip silently reformats it into a key that
    matches nothing on the other side of the join (`pipeline/ids.py`).
    """
    stand_init_csv = Path(stand_init_csv)
    if not stand_init_csv.exists():
        raise OwnerCrosswalkError(f"ownership crosswalk not found: {stand_init_csv}")

    units = pd.read_csv(
        stand_init_csv, usecols=list(CROSSWALK_COLUMNS), dtype={"PLT_CN": "string"},
    )
    missing = [c for c in CROSSWALK_COLUMNS if c not in units.columns]
    if missing:
        raise OwnerCrosswalkError(
            f"{stand_init_csv} has no {', '.join(missing)} column; it is not the "
            "ownership-segmented stand-init table this crosswalk expects."
        )

    units["PLT_CN"] = as_id_series(units["PLT_CN"], column="PLT_CN")
    units = units[units["Acres"] > 0]
    if units.empty:
        raise OwnerCrosswalkError(f"{stand_init_csv} has no units with positive acreage")

    acres = (
        units.groupby(["PLT_CN", "OWN_TYPE", "MGMT_TYPE"], as_index=False)["Acres"]
        .sum()
        .rename(columns={
            "PLT_CN": "Stand_CN", "OWN_TYPE": "owner_class",
            "MGMT_TYPE": "management_type", "Acres": "owner_acres",
        })
    )
    plot_acres = acres.groupby("Stand_CN")["owner_acres"].transform("sum")
    acres["owner_share"] = acres["owner_acres"] / plot_acres

    logger.info(
        "owner crosswalk: %d plots, %.0f acres, %d plot(s) spanning more than one owner class",
        acres["Stand_CN"].nunique(), acres["owner_acres"].sum(),
        int((acres.groupby("Stand_CN")["owner_class"].nunique() > 1).sum()),
    )
    return acres[SHARE_COLUMNS]


def apportion(stand_years: pd.DataFrame, shares: pd.DataFrame,
              *, config: dict | None = None) -> pd.DataFrame:
    """
    Split each stand-year across the owner classes its plot's acres fall in.

    One input row becomes one row per (owner class, management type) the plot touches,
    carrying that slice's acres. Rows whose plot the crosswalk does not cover come back
    once, under the unattributed label, with no acres on the owner basis — they keep their
    sampling weight so the case count still reconciles against the overall table.
    """
    config = config or load_output_config()
    unattributed = config["owner_classes"]["unattributed_label"]

    report_key_overlap(
        stand_years["Stand_CN"], shares["Stand_CN"],
        left_name="FVSOut.FVS_Cases.Stand_CN", right_name="ownership crosswalk PLT_CN",
    )

    attributed = stand_years.merge(shares, on="Stand_CN", how="inner")
    covered = set(attributed["Stand_CN"])
    missed = stand_years[~stand_years["Stand_CN"].isin(covered)].copy()
    missed["owner_class"] = unattributed
    missed["management_type"] = unattributed
    missed["owner_acres"] = 0.0
    missed["owner_share"] = 0.0

    out = pd.concat([attributed, missed], ignore_index=True)
    logger.info(
        "owner attribution: %d of %d case(s) crosswalked (%.1f%%), %d left %s",
        len(covered), stand_years["Stand_CN"].nunique(),
        100 * len(covered) / max(stand_years["Stand_CN"].nunique(), 1),
        missed["Stand_CN"].nunique(), unattributed,
    )
    return out


def coverage(owned: pd.DataFrame, *, config: dict | None = None) -> dict:
    """How much of the run the crosswalk reached — reported beside every owner table."""
    config = config or load_output_config()
    unattributed = config["owner_classes"]["unattributed_label"]
    attributed = owned[owned["owner_class"] != unattributed]
    return {
        "cases_total": int(owned["Stand_CN"].nunique()),
        "cases_attributed": int(attributed["Stand_CN"].nunique()),
        "owner_acres": float(
            attributed.drop_duplicates(["Stand_CN", "owner_class", "management_type"])
            ["owner_acres"].sum()
        ),
        "owner_classes": sorted(attributed["owner_class"].unique().tolist()),
    }
