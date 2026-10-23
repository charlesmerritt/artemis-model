"""Read production data sources for the LETO initial-state pipeline."""

from collections.abc import Collection, Iterator
from dataclasses import dataclass, fields
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from pyogrio import read_dataframe

from pipeline.s1_initial_state.leto_initial_state import load_species_lookup

SPECIES_CROSSWALK_SHEET = "EasternSpeciesTranslator"
FIA_TREE_COLUMNS = (
    "CN",
    "PLT_CN",
    "STATUSCD",
    "INVYR",
    "STATECD",
    "SPCD",
    "DIA",
    "HT",
    "ACTUALHT",
    "CR",
    "TPA_UNADJ",
)
FIA_IDENTIFIER_COLUMNS = (
    "CN",
    "PLT_CN",
    "STATUSCD",
    "INVYR",
    "STATECD",
    "SPCD",
)
SQLITE_PARAMETER_LIMIT = 900


@dataclass(frozen=True)
class ProductionDataPaths:
    """Immutable paths to the LETO production data sources."""

    root: Path
    treemap: Path
    treemap_vat: Path
    fiadb: Path
    species_crosswalk: Path
    ownership: Path
    parcels: Path
    streams: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProductionDataPaths":
        return cls(
            root=root,
            treemap=root / "TreeMap-2022/Data/TreeMap2022_CONUS.tif",
            treemap_vat=root / "TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf",
            fiadb=root / "SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db",
            species_crosswalk=root / "FVS_SpeciesCrosswalk.xls",
            ownership=root / "RDS-2025-0045/Data/US_forest_ownership.tif",
            parcels=root / "FL_5_Co_Parcels.gdb",
            streams=root / "FL_5_Co_Streams.zip",
        )


def preflight_production_data(paths: ProductionDataPaths) -> None:
    """Fail before processing when a production source or workbook is invalid."""
    source_fields = fields(paths)[1:]
    missing = [
        field.name for field in source_fields if not getattr(paths, field.name).exists()
    ]
    if missing:
        names = ", ".join(missing)
        raise FileNotFoundError(
            f"Production data mount is incomplete at {paths.root}; "
            f"restore missing sources from R2: {names}"
        )
    load_species_lookup(paths.species_crosswalk, SPECIES_CROSSWALK_SHEET)


def _normalize_plot_ids(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.notna() & np.isfinite(numeric) & numeric.mod(1).eq(0)
    if not valid.all():
        raise ValueError("TreeMap PLT_CN values must be finite whole numbers")
    try:
        return numeric.astype("Int64").astype("string")
    except (OverflowError, TypeError) as error:
        raise ValueError(
            "TreeMap PLT_CN values must be finite whole numbers within Int64 range"
        ) from error


def load_treemap_lookup(path: Path) -> pd.DataFrame:
    """Read a TreeMap raster attribute table and preserve FIA plot identifiers."""
    lookup = read_dataframe(path, read_geometry=False)
    lookup = lookup.rename(
        columns={column: column.upper() for column in lookup.columns}
    )
    required = {"VALUE", "PLT_CN"}
    missing = required.difference(lookup.columns)
    if missing:
        raise ValueError(f"TreeMap lookup missing columns: {sorted(missing)}")
    result = lookup[["VALUE", "PLT_CN"]].copy()
    result["PLT_CN"] = _normalize_plot_ids(result["PLT_CN"])
    return result


TREEMAP_ATTRIBUTE_COLUMNS = {
    "FORTYPCD": "FORTYPCD",
    "BALIVE": "BALIVE",
    "QMD": "QMD",
    "TPA": "TPA_LIVE",
}


def load_treemap_attributes(path: Path) -> pd.DataFrame:
    """Read the TreeMap VAT's per-plot stand attributes for the CA segmentation.

    TreeMap 2022 ships FORTYPCD, BALIVE, QMD and TPA_LIVE directly in its
    raster attribute table -- already the plot-level values LETO's
    `01_build_segmentation_rasters.py` computes for its segmentation
    features. LETO uses them only as a fallback behind FIA
    (`pipeline.leto_attributes`); see `load_leto_attribute_sources`.
    """
    lookup = read_dataframe(path, read_geometry=False)
    lookup = lookup.rename(
        columns={column: column.upper() for column in lookup.columns}
    )
    required = {"VALUE", "PLT_CN", *TREEMAP_ATTRIBUTE_COLUMNS.values()}
    missing = required.difference(lookup.columns)
    if missing:
        raise ValueError(f"TreeMap VAT missing columns: {sorted(missing)}")
    result = lookup[["VALUE", "PLT_CN", *TREEMAP_ATTRIBUTE_COLUMNS.values()]].copy()
    result = result.rename(
        columns={vat_name: feature_name for feature_name, vat_name in TREEMAP_ATTRIBUTE_COLUMNS.items()}
    )
    result["PLT_CN"] = _normalize_plot_ids(result["PLT_CN"])
    for column in ("FORTYPCD", "BALIVE", "QMD", "TPA"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


LETO_COND_COLUMNS = ("PLT_CN", "CONDID", "COND_STATUS_CD", "CONDPROP_UNADJ", "STDAGE",
                     "FORTYPCD", "BALIVE")
LETO_TREE_COLUMNS = ("PLT_CN", "CONDID", "STATUSCD", "DIA", "TPA_UNADJ")


def load_leto_attribute_sources(
    path: Path, plot_ids: Collection[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """COND and TREE rows `pipeline.leto_attributes.plot_lookup` needs for these plots."""
    plots = sorted({str(plot_id) for plot_id in plot_ids})
    tables = {"COND": LETO_COND_COLUMNS, "TREE": LETO_TREE_COLUMNS}
    frames: dict[str, list[pd.DataFrame]] = {name: [] for name in tables}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        for plot_chunk in _chunks(plots, SQLITE_PARAMETER_LIMIT):
            plot_parameters = ", ".join("?" for _ in plot_chunk)
            for name, columns in tables.items():
                select = ", ".join(
                    "CAST(PLT_CN AS TEXT) AS PLT_CN" if c == "PLT_CN" else c for c in columns)
                frames[name].append(pd.read_sql_query(
                    f"SELECT {select} FROM {name} WHERE PLT_CN IN ({plot_parameters})",
                    connection, params=plot_chunk))
    out = []
    for name, columns in tables.items():
        frame = (pd.concat(frames[name], ignore_index=True) if frames[name]
                 else pd.DataFrame(columns=list(columns)))
        frame["PLT_CN"] = frame["PLT_CN"].astype("string")
        for column in columns[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        out.append(frame)
    return out[0], out[1]


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _empty_fia_trees() -> pd.DataFrame:
    result = pd.DataFrame(columns=FIA_TREE_COLUMNS)
    for column in FIA_IDENTIFIER_COLUMNS:
        result[column] = result[column].astype("string")
    return result


def load_fia_trees_sqlite(
    path: Path,
    plot_ids: Collection[str],
    state_codes: Collection[int] = (1, 12, 13, 45),
) -> pd.DataFrame:
    """Read the required FIA tree rows from a SQLite database in read-only mode."""
    plots = sorted({str(plot_id) for plot_id in plot_ids})
    states = sorted(set(state_codes))
    if not plots or not states:
        return _empty_fia_trees()

    chunk_size = SQLITE_PARAMETER_LIMIT - len(states)
    if chunk_size < 1:
        raise ValueError("Too many state codes for a parameterized SQLite query")

    select_columns = ", ".join(FIA_TREE_COLUMNS)
    state_parameters = ", ".join("?" for _ in states)
    frames = []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        for plot_chunk in _chunks(plots, chunk_size):
            plot_parameters = ", ".join("?" for _ in plot_chunk)
            query = (
                f"SELECT {select_columns} FROM TREE "
                f"WHERE PLT_CN IN ({plot_parameters}) "
                f"AND STATECD IN ({state_parameters})"
            )
            frames.append(
                pd.read_sql_query(query, connection, params=[*plot_chunk, *states])
            )

    result = pd.concat(frames, ignore_index=True)
    for column in FIA_IDENTIFIER_COLUMNS:
        result[column] = result[column].astype("string")
    return result
