"""The young-plot library: real FIA plots an add-back pixel can carry (ADR 0003, #87).

A *young plot* is a plot from TreeMap 2022's own library, in Alabama, Florida or Georgia,
whose forest type is the *neighbour plot's* and whose FIA ``STDAGE`` is near the pixel's
*stand age* (``CONTEXT.md``). The plot's FIA age only indexes the library: the pixel's
2022 stand age is carried separately and is the authoritative one.

**The match.** :meth:`YoungPlotLibrary.match` searches the neighbour plot's forest type
(:attr:`MatchLevel.TYPE`), then its forest type group (:attr:`MatchLevel.GROUP`), each
within ±2 years of the stand age; with both empty the pixel is
:attr:`MatchLevel.UNMATCHED`, which #88 refuses. Within a pool, candidates rank by age gap,
then species mix, then the ``PLT_CN`` string.

**Coverage** (#87's acceptance report) for the forest types of an AOI's TreeMap pixels:

    uv run python -m pipeline.s1_initial_state.young_plot_library --aoi-raster RASTER --out coverage.csv

where ``RASTER`` holds TreeMap values over the AOI, e.g. a run's ``treemap2022_improved.tif``.

Control numbers stay strings end to end (AGENTS.md gotcha).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pandas as pd

from pipeline.ids import ID_DTYPE, as_id_series, normalize_id_columns
from pipeline.s1_initial_state.young_stand_profiles import (
    AGE_HALF_WINDOW,
    CONDITIONS_SQL,
    PLOTS_SQL,
    SEEDLINGS_SQL,
    STATES,
    forest_type_group,
    states_sql,
)

LIBRARY_STAND_AGES = range(0, 22)   # the stand ages the library serves; a young plot's STDAGE is within ±2
SEEDLING_AGES = range(0, 3)   # stand ages whose tree list carries the plot's seedlings
SEEDLING_DIA_IN = 0.1         # FIA seedlings are DIA < 1 in and have no recorded size
SEEDLING_HT_FT = 1.0          # FIA's minimum seedling height is 0.5 ft (softwoods), 1 ft (hardwoods)
TREE_COLUMNS = ["TM_ID", "PLT_CN", "STATUSCD", "TPA_UNADJ", "SPCD", "DIA", "HT", "CR"]
TREE_TABLE_CHUNK_ROWS = 2_000_000   # the 4.8 GB tree table is read in chunks, keeping young plots' rows


class MatchLevel(StrEnum):
    """Which pool a young plot came from: the cascade's fallback level."""

    TYPE = "type"          # the neighbour plot's FORTYPCD
    GROUP = "group"        # its forest type group
    UNMATCHED = "unmatched"   # no young plot of the type or group within ±2 years


@dataclass(frozen=True)
class YoungPlotMatch:
    """The young plot chosen for one (forest type, stand age), and how it was found."""

    level: MatchLevel
    age_gap: int                 # |STDAGE - stand age|
    plt_cn: str | None           # None at MatchLevel.UNMATCHED
    tm_id: str | None
    candidates: int              # plots in the pool at that age gap: what the tie-break chose among


def species_mix(trees: pd.DataFrame) -> pd.Series:
    """Each species' share of live TPA, indexed by ``SPCD``; empty for a plot with no trees.

    >>> species_mix(pd.DataFrame({"SPCD": [131, 131, 611], "TPA_UNADJ": [300.0, 100.0, 100.0]}))
    SPCD
    131    0.8
    611    0.2
    Name: TPA_UNADJ, dtype: float64
    """
    tpa = trees.groupby("SPCD")["TPA_UNADJ"].sum()
    total = tpa.sum()
    return tpa / total if total > 0 else tpa.iloc[:0]


def mix_distance(a: pd.Series, b: pd.Series) -> float:
    """Total variation distance between two species mixes: 0 identical, 1 nothing shared.

    A plot with no trees has no mix, so it is as far as possible from any other.
    """
    if a.empty or b.empty:
        return 1.0
    return 0.5 * float(a.sub(b, fill_value=0.0).abs().sum())


class YoungPlotLibrary:
    """Young plots with their TreeMap tree rows and FIA seedling rows.

    ``plots`` has one row per plot (``TM_ID``, ``PLT_CN``, ``FORTYPCD``, ``STDAGE``);
    ``trees`` its TreeMap live trees (``TM_ID``, ``SPCD``, ``TPA_UNADJ``, ``DIA``, ``HT``);
    ``seedlings`` its FIA seedlings (``PLT_CN``, ``SPCD``, ``TPA_UNADJ``).
    """

    def __init__(self, plots: pd.DataFrame, trees: pd.DataFrame, seedlings: pd.DataFrame):
        self.plots = plots
        self.trees = trees
        self.seedlings = seedlings

    def match(self, fortypcd: int, stand_age: int, neighbour_mix: pd.Series) -> YoungPlotMatch:
        """The young plot for a pixel of this stand age whose neighbour plot has this type."""
        gap = (self.plots["STDAGE"] - stand_age).abs()
        near = self.plots[gap <= AGE_HALF_WINDOW]
        group = forest_type_group(fortypcd)
        pools = ((MatchLevel.TYPE, near["FORTYPCD"] == fortypcd),
                 (MatchLevel.GROUP, near["FORTYPCD"].map(forest_type_group) == group))
        for level, members in pools:
            pool = near[members]
            if len(pool):
                return self._best(level, pool, gap[pool.index], neighbour_mix, stand_age)
        return YoungPlotMatch(MatchLevel.UNMATCHED, 0, None, None, 0)

    def coverage(self, fortypcds, stand_ages=LIBRARY_STAND_AGES) -> pd.DataFrame:
        """For each (forest type, stand age): the fallback level, age gap and candidate count.

        Matched without a neighbour plot's species mix, so it shows how deep the library
        is, not which plot a particular pixel gets.
        """
        rows = []
        for fortypcd in fortypcds:
            for stand_age in stand_ages:
                m = self.match(fortypcd, stand_age, pd.Series(dtype=float))
                rows.append({"FORTYPCD": fortypcd, "stand_age": stand_age, "level": m.level,
                             "age_gap": m.age_gap, "candidates": m.candidates})
        return pd.DataFrame(rows)

    def tree_list(self, match: YoungPlotMatch, stand_age: int) -> pd.DataFrame:
        """The young plot's trees, plus its seedlings as small trees at stand age 0–2.

        TreeMap tree lists carry no seedlings, and an FIA plot this young has almost no
        tree of 1 in DBH, so without them a young stand would be an empty list. The
        pixel's stand age decides, not the plot's FIA age. An unmatched pixel has no plot, so
        no tree list: the caller must not write one (ADR 0003 amendment).
        """
        if match.level is MatchLevel.UNMATCHED:
            raise ValueError("an unmatched pixel has no young plot, so no tree list")
        return self._trees(match.tm_id, match.plt_cn, stand_age)

    def _trees(self, tm_id: str, plt_cn: str, stand_age: int) -> pd.DataFrame:
        trees = self.trees[self.trees["TM_ID"] == tm_id]
        if stand_age not in SEEDLING_AGES:
            return trees.reset_index(drop=True)
        seedlings = self.seedlings[self.seedlings["PLT_CN"] == plt_cn]
        small = seedlings.assign(TM_ID=tm_id, STATUSCD=1, DIA=SEEDLING_DIA_IN, HT=SEEDLING_HT_FT)
        return pd.concat([trees, small[trees.columns.intersection(small.columns)]], ignore_index=True)

    def _best(self, level: MatchLevel, pool: pd.DataFrame, gap: pd.Series,
              neighbour_mix: pd.Series, stand_age: int) -> YoungPlotMatch:
        """Rank a pool by age gap, then species mix, then the PLT_CN string."""
        distance = [mix_distance(neighbour_mix, species_mix(self._trees(tm_id, plt_cn, stand_age)))
                    for tm_id, plt_cn in zip(pool["TM_ID"], pool["PLT_CN"])]
        ranked = pool.assign(age_gap=gap, distance=distance)
        best = ranked.sort_values(["age_gap", "distance", "PLT_CN"]).iloc[0]
        candidates = int((ranked["age_gap"] == best["age_gap"]).sum())
        return YoungPlotMatch(level, int(best["age_gap"]), best["PLT_CN"], best["TM_ID"], candidates)


# ── reading the sources ─────────────────────────────────────────────────────────────────


def read_young_plot_library(vat: Path, fia_db: Path, tree_table: Path, *,
                            states: tuple[int, ...] = STATES) -> YoungPlotLibrary:
    """The library from TreeMap's VAT and tree table and the FIA SQLite.

    A young plot is a VAT row whose plot passes ``young_stand_profiles``' filters (accessible,
    single-condition, national design, in ``states``) with ``STDAGE`` close enough to serve
    :data:`LIBRARY_STAND_AGES`. Its ``FORTYPCD`` is TreeMap's, the same source as the neighbour
    plot's (``county_improvement.donor_forest_types``). The VAT stores ``Value`` and
    ``PLT_CN`` as floats; :func:`as_id_series` restores their exact digits or refuses.
    """
    import pyogrio

    table = pyogrio.read_dataframe(vat, columns=["Value", "PLT_CN", "FORTYPCD"], read_geometry=False)
    library = pd.DataFrame({"TM_ID": as_id_series(table["Value"], column="TM_ID"),
                            "PLT_CN": as_id_series(table["PLT_CN"], column="PLT_CN"),
                            "FORTYPCD": table["FORTYPCD"].astype("Int64")})
    stdage = (max(LIBRARY_STAND_AGES[0] - AGE_HALF_WINDOW, 0), LIBRARY_STAND_AGES[-1] + AGE_HALF_WINDOW)
    con = sqlite3.connect(f"file:{fia_db}?mode=ro", uri=True)
    try:
        cond = pd.read_sql(states_sql(CONDITIONS_SQL, states), con, params=list(stdage))
        design = pd.read_sql(states_sql(PLOTS_SQL, states), con)
        seedlings = pd.read_sql(states_sql(SEEDLINGS_SQL, states), con)
    finally:
        con.close()
    cond, design, seedlings = (normalize_id_columns(f, ["PLT_CN"]) for f in (cond, design, seedlings))
    cond = cond[cond["PLT_CN"].isin(design.loc[design["DESIGNCD"] == 1, "PLT_CN"])]
    young = library.merge(cond[["PLT_CN", "CONDID", "STDAGE"]], on="PLT_CN")
    seedlings = seedlings.merge(young[["PLT_CN", "CONDID"]], on=["PLT_CN", "CONDID"])
    return YoungPlotLibrary(young[["TM_ID", "PLT_CN", "FORTYPCD", "STDAGE"]].reset_index(drop=True),
                            live_trees(tree_table, set(young["TM_ID"])),
                            seedlings[["PLT_CN", "SPCD", "TPA_UNADJ"]].reset_index(drop=True))


def live_trees(tree_table: Path, tm_ids: set[str]) -> pd.DataFrame:
    """The tree table's live rows (``STATUSCD`` 1) for these plots, IDs as exact strings."""
    chunks = pd.read_csv(tree_table, usecols=TREE_COLUMNS, chunksize=TREE_TABLE_CHUNK_ROWS,
                         dtype={"TM_ID": ID_DTYPE, "PLT_CN": ID_DTYPE})
    kept = [c[(c["STATUSCD"] == 1) & c["TM_ID"].isin(tm_ids)] for c in chunks]
    return normalize_id_columns(pd.concat(kept, ignore_index=True), ["TM_ID", "PLT_CN"])


def aoi_forest_types(raster: Path, vat: Path) -> list[int]:
    """The ``FORTYPCD`` of every TreeMap plot mapped in a raster of TreeMap values."""
    import pyogrio
    import rasterio

    with rasterio.open(raster) as src:
        values = pd.unique(src.read(1).ravel())
        values = values[values != src.nodata]
    table = pyogrio.read_dataframe(vat, columns=["Value", "FORTYPCD"], read_geometry=False)
    tm_ids = as_id_series(table["Value"], column="TM_ID")
    mapped = table[tm_ids.isin(set(as_id_series(pd.Series(values), column="TM_ID")))]
    return sorted(int(c) for c in mapped["FORTYPCD"].dropna().unique())


def main() -> None:
    from pipeline.data_access import data_paths

    raw = data_paths()["raw"]
    treemap = raw["treemap_2022"]
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aoi-raster", type=Path, required=True,
                        help="a raster of TreeMap values (TM_ID) covering the AOI")
    parser.add_argument("--vat", type=Path, default=Path(treemap["tif"] + ".vat.dbf"))
    parser.add_argument("--tree-table", type=Path, default=Path(treemap["tree_table"]))
    parser.add_argument("--db", type=Path, default=Path(raw["fia_sqlite"]["db"]))
    parser.add_argument("--out", type=Path, required=True, help="coverage CSV")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    library = read_young_plot_library(args.vat, args.db, args.tree_table)
    report = library.coverage(aoi_forest_types(args.aoi_raster, args.vat))
    report.to_csv(args.out, index=False)
    print(f"{len(library.plots)} young plots; (forest type, stand age) cells by fallback:")
    print(report.groupby(["level", "age_gap"]).size().to_string())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
