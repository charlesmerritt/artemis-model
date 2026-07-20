"""
Phase 1.1 — parse TPO harvest-level guidance into a clean targets config.

Turns the Timber Product Output (TPO) guidance spreadsheet
(``data/raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx``) into a tidy,
analysis-ready structure the harvest scheduler can consume as annual volume caps.
See ``notes/management-pipeline-plan.md`` Phase 1, Step 1.1.

The workbook has two sheets, each giving average annual harvest (cubic feet per year):
  - ``ByOwnerGroup`` — Federal NF, Other public, Private, All
  - ``ByCounty``     — Baker, Columbia, Hamilton, Suwannee, Union

Each sheet carries one row per group/county and one *value column per averaging period*
(e.g. all years 1999–2024, and the recent 2013–2024 window).

**Schema assumptions (flag for review — the real workbook is on the data drive, not in
this repo, so these are inferred from the plan and must be confirmed against the file):**
  - The first non-numeric column of each sheet is the dimension label (owner group / county).
  - Every remaining numeric column is an averaging period; its header names the period.
  - Values are cubic feet per year.
The parser is deliberately tolerant of the exact period-column headers so a header rename
in the source file does not break it — only the sheet names and the "first text column is
the label" convention are assumed.

Usage:
    uv run python -m pipeline.s3_management.tpo_targets \\
        --xlsx data/raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx \\
        --out config/tpo_targets.yaml
"""

import argparse
import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

OWNER_SHEET = "ByOwnerGroup"
COUNTY_SHEET = "ByCounty"
VALUE_UNITS = "cubic_feet_per_year"


def _pick_label_column(df: pd.DataFrame) -> str:
    """The dimension label is the first column that is not purely numeric."""
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            return col
    # Fallback: the first column, if everything parsed as numeric.
    return df.columns[0]


def tidy_tpo_sheet(
    df: pd.DataFrame,
    dimension: str,
    label_col: str | None = None,
    value_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Reshape one TPO sheet into long form:
        [dimension, name, period, cuft_per_year].

    ``dimension`` is a constant tag ("owner_group" or "county"). ``label_col`` defaults to
    the first non-numeric column; ``value_cols`` defaults to every numeric column.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    if label_col is None:
        label_col = _pick_label_column(df)
    if value_cols is None:
        value_cols = [c for c in df.columns if c != label_col and pd.api.types.is_numeric_dtype(df[c])]
    if not value_cols:
        raise ValueError(f"No numeric value columns found in the {dimension} sheet (columns: {list(df.columns)})")

    df = df.dropna(subset=[label_col])
    df[label_col] = df[label_col].astype(str).str.strip()

    long = df.melt(
        id_vars=[label_col],
        value_vars=value_cols,
        var_name="period",
        value_name="cuft_per_year",
    )
    long = long.rename(columns={label_col: "name"})
    long.insert(0, "dimension", dimension)
    long["period"] = long["period"].astype(str).str.strip()
    long["cuft_per_year"] = pd.to_numeric(long["cuft_per_year"], errors="coerce")
    long = long.dropna(subset=["cuft_per_year"]).reset_index(drop=True)
    return long


def nested_from_tidy(tidy: pd.DataFrame) -> dict:
    """Convert a tidy TPO table into ``{name: {period: cuft_per_year}}``."""
    out: dict[str, dict[str, float]] = {}
    for name, sub in tidy.groupby("name"):
        out[str(name)] = {
            str(period): float(value)
            for period, value in zip(sub["period"], sub["cuft_per_year"])
        }
    return out


def parse_tpo_workbook(
    path: Path,
    owner_sheet: str = OWNER_SHEET,
    county_sheet: str = COUNTY_SHEET,
) -> dict:
    """
    Parse the TPO workbook into a targets structure:

        {
          "units": "cubic_feet_per_year",
          "source": "<filename>",
          "by_owner_group": {name: {period: value}, ...},
          "by_county":      {name: {period: value}, ...},
        }
    """
    path = Path(path)
    owner_df = pd.read_excel(path, sheet_name=owner_sheet)
    county_df = pd.read_excel(path, sheet_name=county_sheet)

    owner_tidy = tidy_tpo_sheet(owner_df, "owner_group")
    county_tidy = tidy_tpo_sheet(county_df, "county")

    targets = {
        "units": VALUE_UNITS,
        "source": path.name,
        "by_owner_group": nested_from_tidy(owner_tidy),
        "by_county": nested_from_tidy(county_tidy),
    }
    logger.info(
        "Parsed TPO targets: %d owner groups, %d counties, periods=%s",
        len(targets["by_owner_group"]),
        len(targets["by_county"]),
        sorted({p for d in targets["by_county"].values() for p in d}),
    )
    return targets


def write_tpo_targets(targets: dict, out_path: Path) -> Path:
    """Write the targets structure to YAML."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        yaml.safe_dump(targets, f, sort_keys=True, default_flow_style=False)
    logger.info("Wrote TPO targets to %s", out_path)
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Parse TPO harvest guidance into a targets config")
    parser.add_argument("--xlsx", type=Path, required=True, help="Path to the TPO guidance .xlsx")
    parser.add_argument("--out", type=Path, default=Path("config/tpo_targets.yaml"))
    parser.add_argument("--owner-sheet", type=str, default=OWNER_SHEET)
    parser.add_argument("--county-sheet", type=str, default=COUNTY_SHEET)
    args = parser.parse_args()

    targets = parse_tpo_workbook(args.xlsx, args.owner_sheet, args.county_sheet)
    write_tpo_targets(targets, args.out)


if __name__ == "__main__":
    main()
