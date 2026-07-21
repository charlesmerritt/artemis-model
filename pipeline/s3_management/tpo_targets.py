"""
Phase 1.1 — parse TPO harvest-level guidance into a clean targets config.

Turns the Timber Product Output (TPO) guidance spreadsheet
(``data/raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx``) into a tidy,
analysis-ready structure the harvest scheduler can consume as annual volume caps.
See ``notes/management-pipeline-plan.md`` Phase 1, Step 1.1.

**Real workbook layout (verified against the file on R2 `artemis-r2`):** this is a
hand-formatted sheet, not a tidy table. A title row and a URL sit at the top; the
"harvest targets per year" summary lives in a small block whose column headers are
split across two merged rows and whose two data rows are tagged by a free-text note:

    ... (title / url) ...
    <blank>                Baker    Columbia  Hamilton  Suwanee   Union    All five
    targets                Baker    Columbia  Hamilton  Suwanee   Union    counties
    per year            11755875  17798687.5 ...                            "Assuming all TPO years are averaged"
    by county           11451200  19725500   ...                            "Assuming 2013-2024 TPO years are averaged"

So we anchor on the ``"Assuming … averaged"`` note cells to find the data rows, read the
numeric cells to their left as the values, and build names from the one-or-two header rows
directly above the first data row. Two averaging periods are emitted: ``all_years`` and
``2013_2024``. Values are cubic feet per year.

The two sheets are ``ByOwnerGroup`` (Federal (NF), Other public, Private, All owners) and
``ByCounty`` (Baker, Columbia, Hamilton, Suwanee, Union, All five counties). Note the
source spells the county **"Suwanee"** (one n); downstream joins to parcels (CNTYNAME
"SUWANNEE") must account for that — left as-is here to stay faithful to the source.

Usage:
    uv run python -m pipeline.s3_management.tpo_targets \\
        --xlsx data/raw/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx \\
        --out config/tpo_targets.yaml
"""

import argparse
import logging
import re
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

OWNER_SHEET = "ByOwnerGroup"
COUNTY_SHEET = "ByCounty"
VALUE_UNITS = "cubic_feet_per_year"

_NOTE_RE = re.compile(r"assuming.*averaged", re.IGNORECASE)


def _normalize_period(note: str) -> str:
    """Map a summary note cell to a stable period key."""
    text = note.lower()
    if "2013" in text:
        return "2013_2024"
    if "all" in text:
        return "all_years"
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and pd.notna(value)


def extract_summary_block(raw: pd.DataFrame) -> dict[str, dict[str, float]]:
    """
    Extract ``{name: {period: cuft_per_year}}`` from a raw (header=None) TPO sheet.

    Anchors on the ``"Assuming … averaged"`` note cells to locate the two summary data
    rows, reads the numeric cells to the left of each note as values, and names the value
    columns from the one-or-two header rows immediately above the first data row.
    """
    grid = raw.values
    nrows, ncols = raw.shape

    # 1. Find data rows: any cell whose text matches the "Assuming … averaged" note.
    data_rows: dict[int, tuple[int, str]] = {}
    for r in range(nrows):
        for c in range(ncols):
            v = grid[r][c]
            if isinstance(v, str) and _NOTE_RE.search(v):
                data_rows[r] = (c, _normalize_period(v))
                break
    if not data_rows:
        raise ValueError("No 'Assuming … averaged' summary rows found — workbook layout may have changed.")

    first = min(data_rows)
    note_col = data_rows[first][0]

    # 2. Value columns: numeric cells to the left of the note in the first data row.
    value_cols = [c for c in range(1, note_col) if _is_number(grid[first][c])]
    if not value_cols:
        raise ValueError("Found summary note rows but no numeric value columns beside them.")

    # 3. Names: concatenate the (up to two) header rows directly above the first data row.
    header_rows = [r for r in (first - 2, first - 1) if r >= 0]
    names = {
        c: _clean_name(" ".join(str(grid[hr][c]) for hr in header_rows if pd.notna(grid[hr][c])))
        for c in value_cols
    }

    # 4. Read each data row's values under the matching period.
    out: dict[str, dict[str, float]] = {}
    for r, (_, period) in sorted(data_rows.items()):
        for c in value_cols:
            name = names[c]
            if name and _is_number(grid[r][c]):
                out.setdefault(name, {})[period] = float(grid[r][c])
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
    owner_raw = pd.read_excel(path, sheet_name=owner_sheet, header=None)
    county_raw = pd.read_excel(path, sheet_name=county_sheet, header=None)

    targets = {
        "units": VALUE_UNITS,
        "source": path.name,
        "by_owner_group": extract_summary_block(owner_raw),
        "by_county": extract_summary_block(county_raw),
    }
    periods = sorted({p for d in targets["by_county"].values() for p in d})
    logger.info(
        "Parsed TPO targets: %d owner groups, %d counties, periods=%s",
        len(targets["by_owner_group"]), len(targets["by_county"]), periods,
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
