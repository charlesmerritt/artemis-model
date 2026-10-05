"""Age-5 stand profiles by forest type group, measured on FIA plots.

Scaled nearest-neighbour establishment (``docs/adr/0002-scaled-nearest-neighbour-establishment.md``)
takes an added-back patch's forest type and species mix from its donor plot, and its
density and tree size from here: what a stand of that forest type group looks like at
the target age (``target_age`` in ``config/establishment.yaml``). A mature donor's live
TPA is what survived decades of mortality and thinning, not what was planted; its DBH
and height would put mature biomass on ground that was cut.

    uv run python -m pipeline.s1_initial_state.young_stand_profiles   # rewrites config/young_stand_profiles.yaml

Measurement (``--help`` for the knobs). Scanning ``TREE`` over the data drive takes about
four minutes; ``COND``, ``PLOT`` and ``SEEDLING`` take seconds each.

- **Conditions**: ``COND`` in Alabama, Florida and Georgia (``STATECD`` 1, 12, 13) with
  ``COND_STATUS_CD = 1`` (accessible forest), ``STDAGE`` within ``target_age ± 2`` and
  ``CONDPROP_UNADJ ≥ 0.999``: the condition covers the whole plot, so a plot's summed
  ``TPA_UNADJ`` is per acre of that one stand. ``PLOT.DESIGNCD = 1`` keeps the national
  mapped design only (fixed-area subplots and microplots, so ``TPA_UNADJ`` is comparable
  across plots). Remeasurements of one plot are separate observations.
- **Live trees**: ``TREE`` rows on those conditions with ``STATUSCD = 1`` (``DIA`` ≥ 1 in by
  FIA design).
- **Seedlings**: ``SEEDLING.TPA_UNADJ`` on the same conditions (``DIA`` < 1 in). Recorded as
  ``seedling_tpa`` but **not** part of the establishment density: TreeMap's own tree lists
  carry no seedling rows (every row has ``DIA`` ≥ 1 in), and a seedling has no DBH to scale.

Per group: ``n_plots``; ``tpa``, the median over plots of live TPA (a plot with no live
tree counts as 0); ``dbh_in``, the quadratic mean diameter of every live tree in the group,
TPA-weighted, so that ``tpa`` stems at ``dbh_in`` carry the group's basal area per stem;
``ht_ft``, the TPA-weighted mean height; ``seedling_tpa``, the median plot seedling TPA;
``planted_share``, the share of plots with ``STDORGCD = 1``.

A group measured on fewer than ``min_plots`` plots takes its parent pool's numbers
(softwood or hardwood), and failing that the all-plot pool. It keeps its own ``n_plots``
and records the pool in ``profile_from``; the fallback is logged.

Control numbers stay strings: SQLite returns ``PLT_CN`` as text and it is normalized with
:func:`pipeline.ids.as_id_series`, never cast through a number.
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from pipeline.ids import as_id_series

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_PATH = REPO_ROOT / "config" / "young_stand_profiles.yaml"
ESTABLISHMENT_PATH = REPO_ROOT / "config" / "establishment.yaml"

STATES = (1, 12, 13)          # Alabama, Florida, Georgia: TreeMap draws donors across state lines
AGE_HALF_WINDOW = 2           # STDAGE within target_age ± 2
MIN_PLOTS = 10                # below this a "median plot" is a handful of plots wearing a rule


class ForestTypeGroup(StrEnum):
    """FIA forest type groups the AOI's donors fall into, plus the pools a thin group borrows."""

    LONGLEAF_SLASH = "longleaf_slash"            # FORTYPCD 140-149
    LOBLOLLY_SHORTLEAF = "loblolly_shortleaf"    # 160-169
    OAK_PINE = "oak_pine"                        # 400-409
    OAK_GUM_CYPRESS = "oak_gum_cypress"          # 600-609
    OTHER_HARDWOOD = "other_hardwood"            # 500-998 outside the 600s
    OTHER = "other"                              # other softwoods, nonstocked (999), unknown
    SOFTWOOD = "softwood"                        # pool: the two southern pine groups
    HARDWOOD = "hardwood"                        # pool: oak/pine and the hardwood groups
    ALL = "all"                                  # pool: every plot


LEAF_GROUPS = (ForestTypeGroup.LONGLEAF_SLASH, ForestTypeGroup.LOBLOLLY_SHORTLEAF,
               ForestTypeGroup.OAK_PINE, ForestTypeGroup.OAK_GUM_CYPRESS,
               ForestTypeGroup.OTHER_HARDWOOD, ForestTypeGroup.OTHER)

PARENT = {
    ForestTypeGroup.LONGLEAF_SLASH: ForestTypeGroup.SOFTWOOD,
    ForestTypeGroup.LOBLOLLY_SHORTLEAF: ForestTypeGroup.SOFTWOOD,
    ForestTypeGroup.OAK_PINE: ForestTypeGroup.HARDWOOD,   # FIA counts oak/pine with the hardwood types
    ForestTypeGroup.OAK_GUM_CYPRESS: ForestTypeGroup.HARDWOOD,
    ForestTypeGroup.OTHER_HARDWOOD: ForestTypeGroup.HARDWOOD,
    ForestTypeGroup.OTHER: ForestTypeGroup.ALL,
    ForestTypeGroup.SOFTWOOD: ForestTypeGroup.ALL,
    ForestTypeGroup.HARDWOOD: ForestTypeGroup.ALL,
}

# First match wins, so the 600s are claimed before the 500-998 catch-all.
_RANGES = ((ForestTypeGroup.LONGLEAF_SLASH, 140, 149), (ForestTypeGroup.LOBLOLLY_SHORTLEAF, 160, 169),
           (ForestTypeGroup.OAK_PINE, 400, 409), (ForestTypeGroup.OAK_GUM_CYPRESS, 600, 609),
           (ForestTypeGroup.OTHER_HARDWOOD, 500, 998))


def forest_type_group(fortypcd) -> ForestTypeGroup:
    """The group of one FIA ``FORTYPCD``; a missing code is ``OTHER``.

    >>> forest_type_group(142), forest_type_group(608), forest_type_group(999)
    (<ForestTypeGroup.LONGLEAF_SLASH: 'longleaf_slash'>, <ForestTypeGroup.OAK_GUM_CYPRESS: 'oak_gum_cypress'>, <ForestTypeGroup.OTHER: 'other'>)
    """
    if fortypcd is None or pd.isna(fortypcd):
        return ForestTypeGroup.OTHER
    code = int(fortypcd)
    for group, low, high in _RANGES:
        if low <= code <= high:
            return group
    return ForestTypeGroup.OTHER


def pool_members(pool: ForestTypeGroup) -> set[ForestTypeGroup]:
    """The leaf groups a group or pool stands for (a leaf stands for itself)."""
    if pool is ForestTypeGroup.ALL:
        return set(LEAF_GROUPS)
    if pool in LEAF_GROUPS:
        return {pool}
    return {g for g in LEAF_GROUPS if PARENT[g] is pool}


@dataclass(frozen=True)
class YoungStandProfile:
    """Density and size of a forest type group at the target age."""

    tpa: float            # live trees per acre (DIA >= 1 in)
    dbh_in: float         # TPA-weighted quadratic mean diameter
    ht_ft: float          # TPA-weighted mean height
    n_plots: int          # the group's own plots, even when it borrows a pool's numbers
    profile_from: ForestTypeGroup

    @property
    def basal_area_ft2(self) -> float:
        """Live basal area per acre the profile implies."""
        return self.tpa * BA_FACTOR * self.dbh_in**2


BA_FACTOR = math.pi / (4 * 144)   # ft² of basal area per (inch of DBH)²


# ── the pure aggregation ────────────────────────────────────────────────────────────────


def _plot_table(conditions: pd.DataFrame, trees: pd.DataFrame, seedlings: pd.DataFrame) -> pd.DataFrame:
    """One row per plot: its group, live TPA and seedling TPA (0 where it has none)."""
    plots = conditions[["PLT_CN", "FORTYPCD", "STDORGCD"]].copy()
    plots["PLT_CN"] = as_id_series(plots["PLT_CN"], column="PLT_CN")
    plots["group"] = [forest_type_group(c) for c in plots["FORTYPCD"]]
    for name, frame in (("tpa", trees), ("seedling_tpa", seedlings)):
        per_plot = (frame.assign(PLT_CN=as_id_series(frame["PLT_CN"], column="PLT_CN"))
                    .groupby("PLT_CN")["TPA_UNADJ"].sum())
        plots[name] = plots["PLT_CN"].map(per_plot).fillna(0.0).astype(float)
    return plots


def _pool_profile(plots: pd.DataFrame, trees: pd.DataFrame) -> dict:
    """The numbers for one set of plots and the live trees on them."""
    t = trees[trees["PLT_CN"].isin(plots["PLT_CN"])]
    w = t["TPA_UNADJ"].to_numpy(float)
    has_ht = t["HT"].notna().to_numpy()
    total = w.sum()
    return {
        "n_plots": int(len(plots)),
        "tpa": float(plots["tpa"].median()) if len(plots) else math.nan,
        "dbh_in": math.sqrt((w * t["DIA"].to_numpy(float) ** 2).sum() / total) if total else math.nan,
        "ht_ft": (float(np.average(t["HT"].to_numpy(float)[has_ht], weights=w[has_ht]))
                  if w[has_ht].sum() else math.nan),
        "seedling_tpa": float(plots["seedling_tpa"].median()) if len(plots) else math.nan,
        "planted_share": float((plots["STDORGCD"] == 1).mean()) if len(plots) else math.nan,
    }


def young_stand_profiles(conditions: pd.DataFrame, trees: pd.DataFrame, seedlings: pd.DataFrame,
                         *, min_plots: int = MIN_PLOTS) -> pd.DataFrame:
    """Profiles indexed by :class:`ForestTypeGroup`: every leaf group and every pool.

    ``conditions`` has one row per young plot (``PLT_CN``, ``FORTYPCD``, ``STDORGCD``);
    ``trees`` its live trees (``PLT_CN``, ``TPA_UNADJ``, ``DIA``, ``HT``); ``seedlings`` its
    seedling counts (``PLT_CN``, ``TPA_UNADJ``). Rows are already restricted to the
    matching condition. A leaf with fewer than ``min_plots`` plots takes the numbers of
    the nearest pool up :data:`PARENT` that has enough.
    """
    plots = _plot_table(conditions, trees, seedlings)
    trees = trees.assign(PLT_CN=as_id_series(trees["PLT_CN"], column="PLT_CN"))
    measured = {g: _pool_profile(plots[plots["group"].isin(pool_members(g))], trees)
                for g in ForestTypeGroup}
    rows = {}
    for group in ForestTypeGroup:
        source = group
        while measured[source]["n_plots"] < min_plots and source is not ForestTypeGroup.ALL:
            source = PARENT[source]
        if source is not group:
            logger.warning("%s: %d plots < %d; taking the %s pool's profile (%d plots)", group,
                           measured[group]["n_plots"], min_plots, source, measured[source]["n_plots"])
        rows[group] = measured[source] | {"n_plots": measured[group]["n_plots"], "profile_from": source}
    return pd.DataFrame.from_dict(rows, orient="index")


# ── reading FIA ─────────────────────────────────────────────────────────────────────────


CONDITIONS_SQL = """
SELECT PLT_CN, CONDID, STATECD, INVYR, FORTYPCD, STDAGE, STDORGCD, CONDPROP_UNADJ
FROM COND
WHERE STATECD IN ({states}) AND COND_STATUS_CD = 1
  AND STDAGE BETWEEN ? AND ? AND CONDPROP_UNADJ >= 0.999
""".strip()
PLOTS_SQL = "SELECT CN AS PLT_CN, DESIGNCD FROM PLOT WHERE STATECD IN ({states})"
TREES_SQL = """
SELECT PLT_CN, CONDID, SPCD, DIA, HT, TPA_UNADJ
FROM TREE
WHERE STATECD IN ({states}) AND STATUSCD = 1 AND TPA_UNADJ > 0
""".strip()
SEEDLINGS_SQL = """
SELECT PLT_CN, CONDID, SPCD, TPA_UNADJ
FROM SEEDLING
WHERE STATECD IN ({states}) AND TPA_UNADJ > 0
""".strip()


def _sql(template: str, states: tuple[int, ...]) -> str:
    return template.format(states=", ".join(str(int(s)) for s in states))


def read_fia(db: Path, states: tuple[int, ...], stdage: tuple[int, int]):
    """The young conditions and their live trees and seedlings, from the FIA SQLite.

    The tables have no indexes, so each query is one sequential scan and the joins
    happen here, on ``(PLT_CN, CONDID)`` as strings.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cond = pd.read_sql(_sql(CONDITIONS_SQL, states), con, params=list(stdage))
        plots = pd.read_sql(_sql(PLOTS_SQL, states), con)
        logger.info("scanning TREE (about four minutes)")
        trees = pd.read_sql(_sql(TREES_SQL, states), con)
        seedlings = pd.read_sql(_sql(SEEDLINGS_SQL, states), con)
    finally:
        con.close()
    for frame in (cond, plots, trees, seedlings):
        frame["PLT_CN"] = as_id_series(frame["PLT_CN"], column="PLT_CN")
    design_one = plots.loc[plots["DESIGNCD"] == 1, "PLT_CN"]
    cond = cond[cond["PLT_CN"].isin(design_one)]
    logger.info("%d young conditions on national-design plots", len(cond))
    keys = cond[["PLT_CN", "CONDID"]]
    return cond, trees.merge(keys, on=["PLT_CN", "CONDID"]), seedlings.merge(keys, on=["PLT_CN", "CONDID"])


# ── the committed config ────────────────────────────────────────────────────────────────


def load_young_stand_profiles(path: Path = PROFILES_PATH) -> dict[ForestTypeGroup, YoungStandProfile]:
    """The committed profiles, keyed by group (leaves and pools)."""
    block = yaml.safe_load(Path(path).read_text())["profiles"]
    return {ForestTypeGroup(g): YoungStandProfile(float(p["tpa"]), float(p["dbh_in"]), float(p["ht_ft"]),
                                                  int(p["n_plots"]), ForestTypeGroup(p["profile_from"]))
            for g, p in block.items()}


def target_age(path: Path = ESTABLISHMENT_PATH) -> int:
    return int(yaml.safe_load(Path(path).read_text())["target_age"])


HEADER = """\
# Age-{age} stand profiles by forest type group, measured on FIA plots. GENERATED; do not edit.
# Regenerate: uv run python -m pipeline.s1_initial_state.young_stand_profiles
# What they are for and why: docs/adr/0002-scaled-nearest-neighbour-establishment.md.
# Method and definitions: the module docstring of pipeline/s1_initial_state/young_stand_profiles.py.
#   tpa           median plot live TPA (TREE, STATUSCD 1, DIA >= 1 in); the establishment density
#   dbh_in        TPA-weighted quadratic mean diameter of those trees
#   ht_ft         TPA-weighted mean height of those trees
#   seedling_tpa  median plot SEEDLING TPA (DIA < 1 in): recorded, NOT added to the density
#   profile_from  the group whose plots gave the numbers (a pool when n_plots < min_plots)
"""


def to_yaml(profiles: pd.DataFrame, source: dict, age: int) -> str:
    def clean(row: pd.Series) -> dict:
        return {"n_plots": int(row["n_plots"]), "tpa": round(float(row["tpa"]), 1),
                "dbh_in": round(float(row["dbh_in"]), 2), "ht_ft": round(float(row["ht_ft"]), 1),
                "seedling_tpa": round(float(row["seedling_tpa"]), 1),
                "planted_share": round(float(row["planted_share"]), 2),
                "profile_from": str(row["profile_from"])}

    body = {"source": source,
            "profiles": {str(g): clean(profiles.loc[g]) for g in ForestTypeGroup}}
    return HEADER.format(age=age) + yaml.dump(body, Dumper=_BlockDumper, sort_keys=False, width=200,
                                              default_flow_style=None)


class _BlockDumper(yaml.SafeDumper):
    """Multi-line strings (the SQL) as literal blocks, so the committed query reads as SQL."""


_BlockDumper.add_representer(
    str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style="|" if "\n" in v else None))


def main() -> None:
    from pipeline.data_access import data_paths

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=Path(data_paths()["raw"]["fia_sqlite"]["db"]))
    parser.add_argument("--states", type=int, nargs="+", default=list(STATES))
    parser.add_argument("--min-plots", type=int, default=MIN_PLOTS)
    parser.add_argument("--out", type=Path, default=PROFILES_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    age = target_age()
    stdage = (age - AGE_HALF_WINDOW, age + AGE_HALF_WINDOW)
    states = tuple(args.states)
    cond, trees, seedlings = read_fia(args.db, states, stdage)
    profiles = young_stand_profiles(cond, trees, seedlings, min_plots=args.min_plots)
    source = {
        "database": str(args.db),
        "states": list(states),
        "stdage": list(stdage),
        "target_age": age,
        "min_plots": args.min_plots,
        "plots": int(cond["PLT_CN"].nunique()),
        "inventory_years": [int(cond["INVYR"].min()), int(cond["INVYR"].max())],
        "filters": ("COND_STATUS_CD = 1; CONDPROP_UNADJ >= 0.999 (single-condition plots); "
                    "PLOT.DESIGNCD = 1; live trees STATUSCD = 1; joined on (PLT_CN, CONDID)"),
        "conditions_sql": _sql(CONDITIONS_SQL, states),
        "plots_sql": _sql(PLOTS_SQL, states),
        "trees_sql": _sql(TREES_SQL, states),
        "seedlings_sql": _sql(SEEDLINGS_SQL, states),
    }
    args.out.write_text(to_yaml(profiles, source, age))
    print(profiles.to_string())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
