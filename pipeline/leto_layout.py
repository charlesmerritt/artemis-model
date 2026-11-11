"""The LETO layout (:attr:`pipeline.run.InventoryLayout.LETO`): LETO stages 4-5 per management unit.

Every scheduled unit is its own FVS stand, ``STAND_ID`` = ``STAND_CN`` = its ``unit_id``, in
``FVS_StandInit`` and ``FVS_TreeInit`` (:mod:`pipeline.leto_database`):

- **Trees** (:func:`pipeline.leto_inventory.build_inventory`): the live FIA trees of the unit's
  donor plots, ``TREE_COUNT`` = ``TPA_UNADJ`` x weight, species through the FVS crosswalk, HT
  falling back to ACTUALHT. A unit's donor weights are its parent stands' combined by its
  pieces' acres (:func:`pipeline.leto_proximity.unit_weights`), filtered at the unit as LETO does. A young
  plot (``Y<ID>``, ADR 0003) is a donor like any other, with its own trees from
  ``young_plots/``. A unit with no usable live tree takes the whole tree list of its nearest
  unit that has one.
- **Age**: ``AGE`` is the acre-weighted mean of the unit's parent-stand ages
  (:func:`unit_stdage_means`), rounded half to even as LETO runs.
- **DB_GROUP**: the unit's owner class, or ``Riparian``. ``stands.csv`` keeps it; the
  per-group databases are written only under ``database.mode`` grouped or both.

Per county, beside the plot layout's files, :meth:`CountyInventory.write` writes the combined database
as ``FVS_Data.db``, the groups under ``fvs_groups/`` when asked for, and LETO's stage 4 QA:
:data:`QA_TABLES`. :func:`assemble` joins the counties' into the run's.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import leto_database, leto_inventory
from pipeline.leto_database import DatabaseMode
from pipeline.leto_riparian import RIPARIAN, UPLAND
from pipeline.leto_speckle import UnitClass

GROUPS_DIR = "fvs_groups"
# LETO's stage 4 QA (`QA_Imputation_*.csv`, `QA_Missing_FVS_Species*.csv`), by our file name
IMPUTATION_DIAGNOSTICS = "imputation_diagnostics.csv"
IMPUTATION_SUMMARY = "imputation_summary.csv"
MISSING_SPECIES = "missing_species.csv"
MISSING_SPECIES_BY_STAND = "missing_species_by_stand.csv"
QA_TABLES = (IMPUTATION_DIAGNOSTICS, IMPUTATION_SUMMARY, MISSING_SPECIES, MISSING_SPECIES_BY_STAND)
TREE_FIELDS = ("CN", "PLT_CN", "STATUSCD", "SPCD", "DIA", "HT", "ACTUALHT", "CR", "TPA_UNADJ")


class UnitsWithoutTrees(ValueError):
    """Units left without a tree after nearest-unit imputation: FVS could not run them."""


class DuplicateStands(ValueError):
    """One ``STAND_CN`` in two counties' LETO-layout tables."""


def unit_stdage_means(units: pd.DataFrame, parent_age: pd.Series) -> pd.Series:
    """Each ``unit_id``'s acre-weighted mean of its rows' parent-stand (``SEG_ID``) ages.

    Upstream's ``read_stand_polygons``: rows without an age are left out of the mean; a unit
    with none has no mean. A unit whose known rows share one age has that age exactly.
    """
    age = units["SEG_ID"].map(parent_age).astype(float)
    acres = units["Acres"].astype(float)
    known = age.notna()
    keys = units.loc[known, "unit_id"]
    mean = (age * acres)[known].groupby(keys).sum() / acres[known].groupby(keys).sum()
    by_unit = age[known].groupby(keys)
    same = by_unit.min() == by_unit.max()
    mean[same] = by_unit.min()[same]
    return mean.reindex(pd.unique(units["unit_id"]))


def database_groups(units: pd.DataFrame) -> pd.Series:
    riparian = np.where(units["unit_class"] == UnitClass.RIPARIAN, RIPARIAN, UPLAND)
    return leto_database.database_groups(units["owner_class"], pd.Series(riparian, index=units.index))


def young_donor_trees(trees: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """The young plots' live trees as FIA TREE rows under their own ``Y<ID>`` plot."""
    if trees.empty:
        return pd.DataFrame(columns=list(TREE_FIELDS))
    trees = trees.sort_values(["Value", "SPCD", "DIA"], kind="stable")
    plot = prefix + trees["Value"].astype(str)
    out = trees.reindex(columns=list(TREE_FIELDS)).assign(
        PLT_CN=plot, CN=plot + "-" + (trees.groupby("Value").cumcount() + 1).astype(str))
    return out.reset_index(drop=True)


def build(units, unit_weights: pd.DataFrame, unit_age: pd.Series, donor_trees: pd.DataFrame,
          species_lookup: Mapping[str, str], minimum_weight: float) -> leto_inventory.Inventory:
    """LETO stage 4 for the scheduled ``units`` (a projected GeoDataFrame, one row per
    ``unit_id`` ``<FIPS>-<n>``).

    ``unit_weights``: unfiltered ``unit_id`` x ``PLT_CN`` donor weights; ``unit_age``: each
    unit's mean stand age (:func:`unit_stdage_means`), by ``unit_id``. ``donor_trees``: the
    donors' TREE rows (:data:`TREE_FIELDS`). Fails closed (:class:`UnitsWithoutTrees`) on a
    unit no imputation could reach.
    """
    frame = units.assign(
        MU_ID=units["unit_id"].str.rsplit("-", n=1).str[1].astype("int64"),
        STAND_ID=units["unit_id"], ParentSEG_ID=units["SEG_ID"],
        STDAGE_MEAN=units["unit_id"].map(unit_age), DB_GROUP=database_groups(units))
    weights = unit_weights[unit_weights["unit_id"].isin(set(units["unit_id"]))].drop(
        columns="unit_id")
    missing = sorted(set(units["unit_id"]) - set(unit_weights["unit_id"]))
    if missing:
        raise ValueError(f"Management units without donor weights: {missing[:20]}")
    inventory = leto_inventory.build_inventory(frame.drop(columns="SEG_ID"), weights, donor_trees,
                                               species_lookup, minimum_weight=minimum_weight)
    if not inventory.still_missing.empty:
        raise UnitsWithoutTrees(f"units {inventory.still_missing['STAND_ID'].tolist()[:10]} have "
                                "no tree after nearest-unit imputation")
    return inventory


def stand_ages(inventory: leto_inventory.Inventory) -> pd.Series:
    """Each unit's FVS start ``AGE``, by ``unit_id``."""
    final = inventory.final_stands
    return pd.Series(final["AGE"].astype("Float64").to_numpy(), index=final["STAND_ID"])


def _with_county(table: pd.DataFrame, fips: str) -> pd.DataFrame:
    return table.copy().reset_index(drop=True).assign(county_fips=fips)[
        ["county_fips", *table.columns]]


@dataclass(frozen=True)
class CountyInventory:
    """One county's LETO-layout inventory, written as :class:`pipeline.leto_stage.PlotInventory` is."""

    inventory: leto_inventory.Inventory
    fips: str
    mode: DatabaseMode
    state_code: int

    def write(self, fvs_data: Path) -> None:
        """The combined database at ``fvs_data``; beside it, the groups when asked for, and the QA."""
        out = fvs_data.parent
        # The run reads the combined database, so it is always written: upstream's `grouped`
        # (groups only) becomes `both` here, and `combined` writes no groups.
        leto_database.build_databases(
            self.inventory.final_stands, self.inventory.trees, out / GROUPS_DIR,
            mode=DatabaseMode.COMBINED if self.mode is DatabaseMode.COMBINED else DatabaseMode.BOTH,
            combined_path=fvs_data, state_code=self.state_code)
        tree_list = self.inventory.tree_list
        tables = (self.inventory.imputation_diagnostics, self.inventory.imputation_summary,
                  tree_list.missing_species, tree_list.missing_species_by_stand)
        for name, table in zip(QA_TABLES, tables):
            _with_county(table, self.fips).to_csv(out / name, index=False)


# ── the run ────────────────────────────────────────────────────────────────────────────


def merge_databases(paths: list[Path], out: Path) -> None:
    """The tables of ``paths`` (one schema) as one database; a ``STAND_CN`` twice is :class:`DuplicateStands`."""
    out.unlink(missing_ok=True)
    shutil.copyfile(paths[0], out)
    with sqlite3.connect(out) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for i, path in enumerate(paths[1:]):
            con.execute(f"ATTACH DATABASE ? AS county{i}", (str(path),))
            for table in tables:
                con.execute(f'INSERT INTO main."{table}" SELECT * FROM county{i}."{table}"')
            con.commit()
            con.execute(f"DETACH DATABASE county{i}")
        repeated = [r[0] for r in con.execute(
            f'SELECT STAND_CN FROM "{leto_database.STAND_TABLE}" GROUP BY STAND_CN HAVING COUNT(*) > 1')]
    con.close()
    if repeated:
        raise DuplicateStands(f"stands {repeated[:10]} are in more than one county's tables")


def _concat_tables(paths: list[Path], out: Path) -> None:
    """The counties' QA tables as one, read as text so control numbers stay exact."""
    frames = [pd.read_csv(p, dtype=str, keep_default_na=False) for p in paths]
    pd.concat(frames, ignore_index=True).to_csv(out, index=False)


def assemble(dirs: list[Path], run_dir: Path, fvs_data: Path, qa_dir: Path) -> None:
    """The run's combined database, per-group databases and QA tables from the counties' own."""
    merge_databases([d / fvs_data.name for d in dirs], fvs_data)
    names = sorted({p.name for d in dirs for p in (d / GROUPS_DIR).glob("*.db")})
    if names:
        (run_dir / GROUPS_DIR).mkdir(parents=True, exist_ok=True)
    for name in names:
        merge_databases([d / GROUPS_DIR / name for d in dirs if (d / GROUPS_DIR / name).exists()],
                        run_dir / GROUPS_DIR / name)
    for name in QA_TABLES:
        _concat_tables([d / name for d in dirs], qa_dir / name)


def remove_run_outputs(run_dir: Path, qa_dir: Path) -> None:
    shutil.rmtree(run_dir / GROUPS_DIR, ignore_errors=True)
    for name in QA_TABLES:
        (qa_dir / name).unlink(missing_ok=True)


def species_sha256(species_lookup: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(sorted(species_lookup.items()))).encode()).hexdigest()
