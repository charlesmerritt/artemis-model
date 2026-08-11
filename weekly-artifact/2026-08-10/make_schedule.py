"""
Build the constrained harvest schedule for the five-county Florida pilot
(`notes/management-pipeline-plan.md` Phase 4.1) from real ARTEMIS inputs.

This is a driver: every modelling decision is made by the repository's own modules —

  * `pipeline.s3_management.tpo_targets`      : TPO annual cuft caps (Phase 1.1)
  * `pipeline.s3_management.regime_assignment`: deterministic regime per unit (Phase 3.2)
  * `pipeline.s4_fvs.regime_templates`        : the harvest events each regime schedules (3.1)
  * `pipeline.s3_management.harvest_scheduler`: the constrained allocator (Phase 4.1)
  * `pipeline.s4_fvs.paint_fvs_to_raster`     : the TM_ID -> PLT_CN crosswalk loader

What the driver itself contributes is the *units table* the allocator consumes, which
Phase 2.3 (unit x stand crosswalk) does not yet provide. Stated plainly:

  Scheduling unit = (TreeMap plot TM_ID) x (county) x (ownership class).

That is a stand-and-attribute unit, not a parcel-derived management unit: it is the
finest partition of the pilot landscape that is both attributable today (from the
TreeMap 5-county raster, the county polygons, and the Harris et al. 2025 ownership
raster) and joinable to the completed FVS no-management baseline. When the Phase 2.3
crosswalk lands, only `build_units()` changes.

Volume rule (also the driver's, and deliberately simple): a unit's removable volume in
a harvest event is

    removable_cuft = proportion(event) * merch_cuft_per_acre(stand, event year) * acres

with `proportion` taken from the regime's own `ThinDBH` operations. For DBH-windowed
thins this is an upper bound on removals, because the window excludes the large trees
that carry most merchantable volume. Demand is therefore a screening upper bound.

Usage (from the repo root, with the R2 inputs staged under ./data — see the artifact
README for the exact keys):

    uv run python weekly-artifact/2026-08-10/make_schedule.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s3_management import harvest_scheduler as hs  # noqa: E402
from pipeline.s3_management.regime_assignment import assign_regimes  # noqa: E402
from pipeline.s3_management.tpo_targets import parse_tpo_workbook  # noqa: E402
from pipeline.s4_fvs.paint_fvs_to_raster import load_crosswalk  # noqa: E402
from pipeline.s4_fvs.regime_templates import DEFAULT_INV_YEAR, build_thins  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_schedule")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"

TREEMAP_TIF = DATA / "interim/treemap5co/TreeMap2022_CONUS_5FlCntys.tif"
CROSSWALK = DATA / "interim/treemap_link/FL_5county_TreeMap_TMIDs.csv"
TRAJECTORY = DATA / "interim/no_management_fl5co_fvs_output/fvs_trajectory.csv"
COUNTIES_SHP = DATA / "interim/counties/countyp010g.shp"
TPO_XLSX = DATA / "raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx"
OWNERSHIP_VSI = "/vsis3/artemis-r2/data/RDS-2025-0045/Data/US_forest_ownership.tif"
UNITS_CACHE = DATA / "interim/schedule_units_attributed.csv"

ACRES_PER_PIXEL = 900.0 / 4046.8564224  # 30 m TreeMap cell
PILOT_COUNTIES = ["Baker", "Columbia", "Hamilton", "Suwannee", "Union"]
# The TPO workbook spells Suwannee with one 'n' (see tpo_targets module docstring).
COUNTY_TO_TPO = {c: ("Suwanee" if c == "Suwannee" else c) for c in PILOT_COUNTIES}

# Harris et al. 2025 ownership classes -> (regime-assignment OWN_CODE, TPO owner group).
# 0 Unknown / 1 Non-Forest / 2 Water carry no TPO group and are dropped from scheduling.
OWNER_CLASSES = {
    3: ("Family Forest", "Private"),
    4: ("Corporate/Other Private", "Private"),
    5: ("Tribal", "Other public"),
    6: ("Federal", "Federal (NF)"),
    7: ("State", "Other public"),
    8: ("Local", "Other public"),
}

CYCLE_YEARS = hs.DEFAULT_CYCLE_YEARS
HORIZON_START = DEFAULT_INV_YEAR + 1        # first scheduled cycle covers 2023-2027
N_CYCLES = 10                               # 50-year horizon, matching the FVS baseline
TPO_PERIOD = "all_years"


# --------------------------------------------------------------------------------------
# Stage A — attribute the pilot landscape: TM_ID x county x ownership
# --------------------------------------------------------------------------------------

def _r2_gdal_env() -> None:
    """Point GDAL's /vsis3 at the same R2 bucket rclone is configured for."""
    endpoint = os.environ["RCLONE_CONFIG_R2_ENDPOINT"].removeprefix("https://").removeprefix("http://")
    os.environ.update(
        AWS_ACCESS_KEY_ID=os.environ["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
        AWS_SECRET_ACCESS_KEY=os.environ["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
        AWS_S3_ENDPOINT=endpoint,
        AWS_VIRTUAL_HOSTING="FALSE",
        AWS_HTTPS="YES",
        AWS_REGION="auto",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    )


def attribute_pixels() -> pd.DataFrame:
    """Pixel-count table over (tm_id, county, owner_class) for the 5-county TreeMap grid."""
    import geopandas as gpd
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from rasterio.vrt import WarpedVRT

    with rasterio.open(TREEMAP_TIF) as src:
        tm = src.read(1)
        profile = src.profile
        nodata = src.nodata
    log.info("TreeMap grid %s, nodata=%s", tm.shape, nodata)

    counties = gpd.read_file(COUNTIES_SHP)
    counties = counties[(counties["STATE"] == "FL") & (counties["NAME"].isin(PILOT_COUNTIES))]
    counties = counties.to_crs(profile["crs"])
    if len(counties) != len(PILOT_COUNTIES):
        raise RuntimeError(f"expected {len(PILOT_COUNTIES)} pilot counties, got {len(counties)}")
    county_codes = {name: i + 1 for i, name in enumerate(sorted(counties["NAME"]))}
    county_grid = rasterize(
        ((geom, county_codes[name]) for geom, name in zip(counties.geometry, counties["NAME"])),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )
    log.info("Rasterized counties: %s", county_codes)

    _r2_gdal_env()
    log.info("Warping the Harris 2025 ownership raster onto the TreeMap grid (windowed read from R2)")
    with rasterio.open(OWNERSHIP_VSI) as osrc:
        with WarpedVRT(
            osrc, crs=profile["crs"], transform=profile["transform"],
            width=profile["width"], height=profile["height"],
            resampling=Resampling.nearest,
        ) as vrt:
            owner_grid = vrt.read(1)
    log.info("Ownership classes present: %s", np.unique(owner_grid).tolist())

    valid = (tm != nodata) & (county_grid > 0)
    df = pd.DataFrame({
        "tm_id": tm[valid].astype("int64"),
        "county_code": county_grid[valid],
        "owner_class": owner_grid[valid],
    })
    counts = df.value_counts().rename("pixel_count").reset_index()
    code_to_county = {v: k for k, v in county_codes.items()}
    counts["county"] = counts["county_code"].map(code_to_county)
    return counts.drop(columns="county_code")


def build_units() -> pd.DataFrame:
    """One row per schedulable unit: TM_ID x county x ownership class, with acres."""
    if UNITS_CACHE.exists():
        log.info("Reusing cached attribution %s", UNITS_CACHE)
        units = pd.read_csv(UNITS_CACHE, dtype={"PLT_CN": "string"})
    else:
        counts = attribute_pixels()
        # TM_ID -> PLT_CN comes from the painter's own loader; forest type rides along.
        xwalk = load_crosswalk(CROSSWALK)
        fortyp = pd.read_csv(
            CROSSWALK, usecols=["Value", "FORTYPCD", "ForTypName"]
        ).rename(columns={"Value": "tm_id"}).drop_duplicates("tm_id")
        units = counts.merge(xwalk, on="tm_id", how="inner").merge(fortyp, on="tm_id", how="left")
        UNITS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        units.to_csv(UNITS_CACHE, index=False)
        log.info("Wrote attribution cache %s", UNITS_CACHE)

    units["acres"] = units["pixel_count"] * ACRES_PER_PIXEL
    units["owner_name"] = units["owner_class"].map(lambda c: OWNER_CLASSES.get(c, (None, None))[0])
    units["owner_group"] = units["owner_class"].map(lambda c: OWNER_CLASSES.get(c, (None, None))[1])
    units["OWN_CODE"] = units["owner_class"]
    dropped = units["owner_group"].isna()
    log.info(
        "Dropping %d pieces (%.0f ac) on unknown/non-forest/water ownership classes",
        int(dropped.sum()), units.loc[dropped, "acres"].sum(),
    )
    units = units[~dropped].copy()
    units["unit_id"] = (
        "TM" + units["tm_id"].astype(str) + "_" + units["county"] + "_O" + units["owner_class"].astype(str)
    )
    return units


# --------------------------------------------------------------------------------------
# Stage B — regimes and the FVS baseline trajectory
# --------------------------------------------------------------------------------------

def assign_unit_regimes(units: pd.DataFrame) -> pd.DataFrame:
    """Repo Phase 3.2 rule. SMZ_Pct is 0 everywhere: no riparian layer is joined yet."""
    units = units.assign(SMZ_Pct=0.0)
    out = assign_regimes(units, inv_year=DEFAULT_INV_YEAR)
    log.info("Regime mix by acres:\n%s", out.groupby("regime")["acres"].sum().sort_values(ascending=False))
    return out


def load_trajectory() -> pd.DataFrame:
    df = pd.read_csv(TRAJECTORY, dtype={"stand_cn": "string", "stand_id": "string"})
    log.info("FVS baseline: %d rows, %d stands, %d-%d",
             len(df), df["stand_cn"].nunique(), df["calendar_year"].min(), df["calendar_year"].max())
    return df


def stand_state_at(traj: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    """Per stand, the most recent projected row at or before each target year."""
    frames = []
    base = traj[["stand_cn", "calendar_year", "age", "merch_cuft", "total_cuft", "basal_area"]]
    base = base.sort_values("calendar_year")
    for year in years:
        snap = (
            base[base["calendar_year"] <= year]
            .groupby("stand_cn", as_index=False)
            .last()
            .assign(target_year=year)
        )
        frames.append(snap)
    return pd.concat(frames, ignore_index=True)


def build_candidates(units: pd.DataFrame, traj: pd.DataFrame) -> pd.DataFrame:
    """One row per (unit x harvest event) the regime library schedules inside the horizon."""
    events = []
    for regime, params in units[["regime", "regime_params"]].drop_duplicates("regime").itertuples(index=False):
        for thin in build_thins(regime, params):
            cycle = (thin.year - HORIZON_START) // CYCLE_YEARS
            if not 0 <= cycle < N_CYCLES:
                continue
            events.append({
                "regime": regime, "event_year": thin.year, "cycle": int(cycle) + 1,
                "proportion": thin.proportion, "max_dbh": thin.max_dbh,
            })
    events = pd.DataFrame(events)
    log.info("Harvest events scheduled by the regime library:\n%s", events.to_string(index=False))

    cand = units.merge(events, on="regime", how="inner")
    state = stand_state_at(traj, sorted(cand["event_year"].unique().tolist()))
    cand = cand.merge(
        state.rename(columns={"stand_cn": "PLT_CN", "target_year": "event_year"}),
        on=["PLT_CN", "event_year"], how="left", validate="many_to_one",
    )
    missing = cand["merch_cuft"].isna()
    if missing.any():
        log.warning("Dropping %d candidate rows with no FVS row at or before the event year", int(missing.sum()))
        cand = cand[~missing]
    cand["stand_age"] = cand["age"]
    cand["removable_volume"] = cand["proportion"] * cand["merch_cuft"] * cand["acres"]
    cand["county_tpo"] = cand["county"].map(COUNTY_TO_TPO)
    log.info("Candidates: %d unit-events over %d cycles, %.0f cuft gross demand",
             len(cand), cand["cycle"].nunique(), cand["removable_volume"].sum())
    return cand


# --------------------------------------------------------------------------------------
# Stage C — allocate under the TPO caps
# --------------------------------------------------------------------------------------

def caps_from_tpo(targets: dict) -> dict[str, dict[str, float]]:
    county = {name: v[TPO_PERIOD] for name, v in targets["by_county"].items()
              if name in COUNTY_TO_TPO.values()}
    owner = {name: v[TPO_PERIOD] for name, v in targets["by_owner_group"].items()
             if name != "All owners"}
    total = {"": targets["by_county"]["All five counties"][TPO_PERIOD]}
    return {hs.TOTAL: total, hs.COUNTY: county, hs.OWNER: owner}


SCENARIOS = {
    "total_only": (hs.TOTAL,),
    "county_only": (hs.COUNTY,),
    "owner_only": (hs.OWNER,),
    "all_combined": (hs.TOTAL, hs.COUNTY, hs.OWNER),
}


def run_scenarios(cand: pd.DataFrame, caps: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the repo allocator once per constraint scenario; return schedule + summary."""
    # The allocator keys county budgets off a column literally named `county`, so the TPO
    # spelling takes that name and the source spelling is kept as `county_name`.
    sched_units = cand.rename(columns={"county": "county_name", "county_tpo": hs.COUNTY,
                                       "owner_group": hs.OWNER})
    schedules, summaries = [], []
    for name, dims in SCENARIOS.items():
        result = hs.schedule_harvests(sched_units, caps, dims=dims)
        result = result.assign(scenario=name)
        schedules.append(result)

        per_cycle = hs.summarize_schedule(result)
        per_cycle["scenario"] = name
        per_cycle["demand_volume"] = result.groupby("cycle")["removable_volume"].sum().to_numpy()
        per_cycle["units_candidate"] = result.groupby("cycle")["unit_id"].count().to_numpy()
        summaries.append(per_cycle)
        log.info("[%s] %d/%d unit-events harvested, %.3g of %.3g cuft demand met",
                 name, int(result["harvested"].sum()), len(result),
                 result["volume_removed"].sum(), result["removable_volume"].sum())

    schedule = pd.concat(schedules, ignore_index=True)
    summary = pd.concat(summaries, ignore_index=True)
    cycle_cap = caps[hs.TOTAL][""] * CYCLE_YEARS
    summary["total_cycle_cap"] = cycle_cap
    summary["cap_utilization"] = summary["volume_removed"] / cycle_cap
    return schedule, summary


def by_dimension(schedule: pd.DataFrame, caps: dict) -> pd.DataFrame:
    """Scheduled volume vs cap for every county and owner group, per scenario per cycle."""
    rows = []
    for dim, cap_key in ((hs.COUNTY, hs.COUNTY), (hs.OWNER, hs.OWNER)):
        grouped = schedule.groupby(["scenario", "cycle", dim], as_index=False).agg(
            demand_volume=("removable_volume", "sum"),
            volume_removed=("volume_removed", "sum"),
            units_harvested=("harvested", "sum"),
        )
        grouped["dimension"] = dim
        grouped = grouped.rename(columns={dim: "key"})
        grouped["cycle_cap"] = grouped["key"].map(caps[cap_key]) * CYCLE_YEARS
        rows.append(grouped)
    out = pd.concat(rows, ignore_index=True)
    out["cap_utilization"] = out["volume_removed"] / out["cycle_cap"]
    return out


# --------------------------------------------------------------------------------------
# Stage D — figure
# --------------------------------------------------------------------------------------

SCENARIO_COLOURS = {
    "total_only": "#1b6ca8", "county_only": "#2e8b57",
    "owner_only": "#d1701f", "all_combined": "#a4243b",
}
BLOCK_COLOURS = {hs.TOTAL: "#4c4c4c", hs.COUNTY: "#2e8b57", hs.OWNER: "#d1701f"}


def make_figure(schedule: pd.DataFrame, summary: pd.DataFrame, dim_summary: pd.DataFrame,
                caps: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mcf = 1e6
    cycle_cap = caps[hs.TOTAL][""] * CYCLE_YEARS
    cycles = list(range(1, N_CYCLES + 1))
    x = np.arange(len(cycles))
    labels = [f"{HORIZON_START + CYCLE_YEARS * (c - 1)}–{HORIZON_START + CYCLE_YEARS * c - 1}"
              for c in cycles]

    def series(df: pd.DataFrame, col: str) -> np.ndarray:
        s = df.groupby("cycle")[col].sum().reindex(cycles).fillna(0.0)
        return s.to_numpy() / mcf

    fig, axes = plt.subplots(2, 2, figsize=(14, 9.5))

    # (a) demand vs what each constraint scenario lets through
    ax = axes[0, 0]
    combined = summary[summary["scenario"] == "all_combined"]
    ax.bar(x, series(combined, "demand_volume"), color="#c9c9c9",
           label="demand asked for by the regime library")
    for name, colour in SCENARIO_COLOURS.items():
        ax.plot(x, series(summary[summary["scenario"] == name], "volume_removed"),
                marker="o", ms=5, lw=2, color=colour, label=f"scheduled — {name}")
    ax.axhline(cycle_cap / mcf, ls="--", c="k", lw=1.3,
               label=f"TPO five-county cap ({cycle_cap / mcf:,.0f} M cuft / cycle)")
    ax.set_title("(a) Harvest demand vs. TPO-constrained schedule", loc="left", fontsize=11)
    ax.set_ylabel("million cuft per 5-year cycle")
    ax.legend(fontsize=8, loc="upper right")

    # (b) county dimension — bars per county, cap as a matching horizontal rule
    ax = axes[0, 1]
    counties = dim_summary[(dim_summary["dimension"] == hs.COUNTY)
                           & (dim_summary["scenario"] == "all_combined")]
    keys = sorted(caps[hs.COUNTY])
    width = 0.8 / len(keys)
    for i, key in enumerate(keys):
        colour = plt.cm.tab10(i)
        ax.bar(x + (i - (len(keys) - 1) / 2) * width,
               series(counties[counties["key"] == key], "volume_removed"),
               width=width, color=colour, label=key)
        ax.axhline(caps[hs.COUNTY][key] * CYCLE_YEARS / mcf, ls=":", lw=1.2, color=colour)
    ax.set_title("(b) By county — scheduled (bars) vs. cap (dotted), all constraints combined",
                 loc="left", fontsize=11)
    ax.set_ylabel("million cuft per cycle")
    ax.set_ylim(0, max(caps[hs.COUNTY].values()) * CYCLE_YEARS / mcf * 1.45)
    ax.legend(fontsize=8, ncol=5, loc="upper center", framealpha=0.95)

    # (c) owner dimension — the public caps are two orders of magnitude below the private one
    ax = axes[1, 0]
    owners = dim_summary[(dim_summary["dimension"] == hs.OWNER)
                         & (dim_summary["scenario"] == "all_combined")]
    keys = ["Private", "Other public", "Federal (NF)"]
    width = 0.8 / len(keys)
    for i, key in enumerate(keys):
        colour = plt.cm.Dark2(i)
        ax.bar(x + (i - (len(keys) - 1) / 2) * width,
               series(owners[owners["key"] == key], "volume_removed"),
               width=width, color=colour, label=key)
        ax.axhline(caps[hs.OWNER][key] * CYCLE_YEARS / mcf, ls=":", lw=1.2, color=colour)
    ax.set_yscale("symlog", linthresh=1)
    ax.set_ylim(0, caps[hs.OWNER]["Private"] * CYCLE_YEARS / mcf * 30)
    ax.set_title("(c) By owner group — scheduled (bars) vs. cap (dotted), log scale",
                 loc="left", fontsize=11)
    ax.set_ylabel("million cuft per cycle (symlog)")
    ax.legend(fontsize=8, ncol=3, loc="upper center", framealpha=0.95)

    # (d) which constraint actually did the blocking, in the combined scenario
    ax = axes[1, 1]
    blocked = schedule[(schedule["scenario"] == "all_combined") & (~schedule["harvested"])]
    bottom = np.zeros(len(cycles))
    for dim, colour in BLOCK_COLOURS.items():
        vals = series(blocked[blocked["blocked_by"] == dim], "removable_volume")
        ax.bar(x, vals, bottom=bottom, color=colour, label=f"blocked by {dim}")
        bottom += vals
    ax.plot(x, series(summary[summary["scenario"] == "all_combined"], "volume_removed"),
            marker="o", ms=5, lw=2, color=SCENARIO_COLOURS["all_combined"], label="scheduled")
    ax.set_title("(d) Unmet demand, by the constraint that blocked it", loc="left", fontsize=11)
    ax.set_ylabel("million cuft per cycle")
    ax.legend(fontsize=8)

    for ax in axes.ravel():
        ax.grid(alpha=0.25, axis="y")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7.5)
        ax.set_xlabel("FVS cycle (calendar years)", fontsize=9)

    fig.suptitle(
        "ARTEMIS — constrained harvest schedule, five-county north Florida pilot\n"
        "FVS SN no-management baseline (693 stands, 925k ac attributed) × deterministic regime "
        f"library, TPO caps ({TPO_PERIOD.replace('_', ' ')} average), oldest-stand-first priority",
        fontsize=11.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=160)
    log.info("Wrote %s", path)


def main() -> None:
    units = assign_unit_regimes(build_units())
    traj = load_trajectory()
    cand = build_candidates(units, traj)
    targets = parse_tpo_workbook(TPO_XLSX)
    caps = caps_from_tpo(targets)
    log.info("Annual caps: %s", caps)

    schedule, summary = run_scenarios(cand, caps)
    dim_summary = by_dimension(schedule, caps)

    keep = [
        "scenario", "cycle", "event_year", "unit_id", "tm_id", "PLT_CN", "county_name", hs.OWNER,
        "owner_name", "ForTypName", "acres", "regime", "proportion", "stand_age",
        "merch_cuft", "removable_volume", "harvested", "volume_removed", "blocked_by",
    ]
    schedule[keep].sort_values(["scenario", "cycle", "unit_id"]).to_csv(
        OUT_DIR / "harvest_schedule.csv", index=False, float_format="%.4f")
    summary.to_csv(OUT_DIR / "schedule_summary_by_cycle.csv", index=False, float_format="%.4f")
    dim_summary.to_csv(OUT_DIR / "schedule_summary_by_dimension.csv", index=False, float_format="%.4f")

    units_out = units.groupby(["county", "owner_name", "regime"], as_index=False).agg(
        units=("unit_id", "count"), acres=("acres", "sum"))
    units_out.to_csv(OUT_DIR / "units_by_county_owner_regime.csv", index=False, float_format="%.2f")

    make_figure(schedule, summary, dim_summary, caps, OUT_DIR / "harvest_schedule.png")

    print("\n=== Landscape attributed ===")
    print(f"units: {len(units):,}   acres: {units['acres'].sum():,.0f}   stands: {units['PLT_CN'].nunique()}")
    print("\n=== Per-cycle totals ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
