"""Tests for the TPO harvest-target parser (pipeline/s3_management/tpo_targets.py).

The parser targets the real, hand-formatted workbook layout (title/url rows, merged
multi-row headers, a summary block whose two data rows are tagged by an
"Assuming … averaged" note). The synthetic fixture below reproduces that shape. A
second test runs against the real workbook, which the `real_xlsx` fixture resolves
from the data drive or fetches from R2, and skips only where neither is reachable.
"""

from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.tpo_targets import (
    extract_annual_average,
    extract_summary_block,
    parse_tpo_workbook,
    write_tpo_targets,
)

# Verified values from the real workbook (cubic feet per year).
COUNTY_ALL_YEARS = {
    "Baker": 11_755_875,
    "Columbia": 17_798_687.5,
    "Hamilton": 15_329_437.5,
    "Suwanee": 18_466_937.5,
    "Union": 8_703_625,
    "All five counties": 72_054_562.5,
}
OWNER_ALL_YEARS = {
    "Federal (NF)": 1_770_000,
    "Other public": 3_969_937.5,
    "Private": 66_314_312.5,
    "All owners": 72_054_250,
}

REAL_XLSX = (
    Path(__file__).resolve().parents[1]
    / "data" / "raw" / "Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx"
)


def _county_grid():
    """Reproduce the real ByCounty summary-block layout (header=None style)."""
    return pd.DataFrame([
        ["Year", "Baker", "Columbia", "Hamilton", "Suwanee", "Union", None, "Total"],
        [2011, 10, 20, 30, 40, 50, None, 150],
        [2013, 20, 30, 40, 50, 60, None, 200],
        [2015, 100, 200, 300, 400, 500, None, 1500],
        [None, None, None, None, None, None, None, None],
        ["Harvest volume targets from US Forest Service TPO reports", None, None, None, None, None, None, None],
        ["https://example/tpo", None, None, None, None, None, None, None],
        [None, None, None, None, None, None, "All five", None],
        ["targets", "Baker", "Columbia", "Hamilton", "Suwanee", "Union", "counties", None],
        ["per year", 11_755_875, 17_798_687.5, 15_329_437.5, 18_466_937.5, 8_703_625, 72_054_562.5,
         "Assuming all TPO years are averaged"],
        ["by county", 11_451_200, 19_725_500, 16_211_500, 22_474_700, 7_642_900, 77_505_800,
         "Assuming 2013-2024 TPO years are averaged"],
    ])


def _owner_grid():
    return pd.DataFrame([
        ["Year", "Federal (NF)", "Other public", "Private", "All owners", None],
        [2011, 1, 2, 3, 6, None],
        [2013, 3, 4, 5, 12, None],
        [2015, 101, 102, 103, 306, None],
        [None, None, None, None, None, None],
        ["Harvest volume targets from US Forest Service TPO reports", None, None, None, None, None],
        [None, None, None, None, None, None],
        [None, "Federal", "Other", None, "All", None],
        ["targets", "(NF)", "public", "Private", "owners", None],
        ["per year", 1_770_000, 3_969_937.5, 66_314_312.5, 72_054_250,
         "Assuming all TPO years are averaged"],
        ["by owner", 2_023_500, 4_486_900, 70_994_800, 77_505_200,
         "Assuming 2013-2024 TPO years are averaged"],
    ])


def test_extract_summary_block_reads_county_names_and_periods():
    block = extract_summary_block(_county_grid())
    assert set(block) == set(COUNTY_ALL_YEARS)
    for name, expected in COUNTY_ALL_YEARS.items():
        assert block[name]["all_years"] == pytest.approx(expected)
    # Both periods present.
    assert block["Union"]["2013_2024"] == pytest.approx(7_642_900)


def test_extract_summary_block_joins_split_owner_headers():
    block = extract_summary_block(_owner_grid())
    # "Federal" + "(NF)" across two header rows -> "Federal (NF)"; "All" + "owners" -> "All owners".
    assert set(block) == set(OWNER_ALL_YEARS)
    assert block["Federal (NF)"]["all_years"] == pytest.approx(1_770_000)
    assert block["All owners"]["2013_2024"] == pytest.approx(77_505_200)


def test_extract_summary_block_raises_without_note_anchor():
    df = pd.DataFrame([["targets", "Baker"], ["per year", 100]])
    with pytest.raises(ValueError, match="Assuming"):
        extract_summary_block(df)


def test_extract_annual_average_excludes_holdout_years_and_converts_thousands():
    county_names = list(extract_summary_block(_county_grid()))
    owner_names = list(extract_summary_block(_owner_grid()))

    counties = extract_annual_average(_county_grid(), county_names, before_year=2015)
    owners = extract_annual_average(_owner_grid(), owner_names, before_year=2015)

    assert counties["Baker"] == pytest.approx(15_000)
    assert counties["All five counties"] == pytest.approx(175_000)
    assert owners["Federal (NF)"] == pytest.approx(2_000)
    assert owners["All owners"] == pytest.approx(9_000)


def test_parse_workbook_end_to_end_synthetic(tmp_path):
    xlsx = tmp_path / "tpo.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        _owner_grid().to_excel(writer, sheet_name="ByOwnerGroup", index=False, header=False)
        _county_grid().to_excel(writer, sheet_name="ByCounty", index=False, header=False)

    targets = parse_tpo_workbook(xlsx)
    assert targets["units"] == "cubic_feet_per_year"
    assert targets["source"] == "tpo.xlsx"
    assert targets["by_county"]["Suwanee"]["all_years"] == pytest.approx(18_466_937.5)
    assert targets["by_county"]["Suwanee"]["pre_2015"] == pytest.approx(45_000)
    # County total reconciles to the owner "All owners" grand total (both ~72.05M).
    assert targets["by_county"]["All five counties"]["all_years"] == pytest.approx(72_054_562.5)
    assert targets["by_owner_group"]["All owners"]["all_years"] == pytest.approx(72_054_250)


def test_write_tpo_targets_roundtrips_yaml(tmp_path):
    import yaml
    targets = {
        "units": "cubic_feet_per_year", "source": "tpo.xlsx",
        "by_owner_group": {"All owners": {"all_years": 72_054_250.0}},
        "by_county": {"Union": {"all_years": 8_703_625.0}},
    }
    out = write_tpo_targets(targets, tmp_path / "config" / "tpo_targets.yaml")
    assert yaml.safe_load(out.read_text()) == targets


@pytest.fixture
def real_xlsx(config_dir, data_access):
    """The real workbook: already staged, on the drive, or fetched from R2 (30 KB)."""
    if REAL_XLSX.exists():
        return REAL_XLSX
    import yaml
    with open(config_dir / "data_paths.yaml") as fh:
        declared = yaml.safe_load(fh)["raw"]["tpo_guidance"]["xlsx"]
    local = data_access.ensure_local(declared, dest=REAL_XLSX)
    if local is None:
        pytest.skip(data_access.unavailable_reason(declared))
    return local


def test_parse_real_tpo_workbook_matches_known_totals(real_xlsx):
    targets = parse_tpo_workbook(real_xlsx)
    for name, expected in COUNTY_ALL_YEARS.items():
        assert targets["by_county"][name]["all_years"] == pytest.approx(expected)
    for name, expected in OWNER_ALL_YEARS.items():
        assert targets["by_owner_group"][name]["all_years"] == pytest.approx(expected)
    assert targets["by_county"]["Union"]["pre_2015"] == pytest.approx(10_268_000)
    assert targets["by_owner_group"]["All owners"]["pre_2015"] == pytest.approx(66_116_000)
