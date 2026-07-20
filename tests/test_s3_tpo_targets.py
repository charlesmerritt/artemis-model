"""Tests for the TPO harvest-target parser (pipeline/s3_management/tpo_targets.py)."""

from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.tpo_targets import (
    nested_from_tidy,
    parse_tpo_workbook,
    tidy_tpo_sheet,
    write_tpo_targets,
)

# Values taken from notes/management-pipeline-plan.md (cubic feet per year).
COUNTY_ALL = {
    "Baker": 11_760_000,
    "Columbia": 17_800_000,
    "Hamilton": 15_330_000,
    "Suwannee": 18_470_000,
    "Union": 8_700_000,
}
OWNER_ALL = {
    "Federal NF": 1_770_000,
    "Other public": 3_970_000,
    "Private": 66_300_000,
    "All": 72_050_000,
}


def _county_frame():
    return pd.DataFrame(
        {
            "County": list(COUNTY_ALL),
            "all_years": list(COUNTY_ALL.values()),
            "2013_2024": [v * 1.05 for v in COUNTY_ALL.values()],
        }
    )


def _owner_frame():
    return pd.DataFrame(
        {
            "OwnerGroup": list(OWNER_ALL),
            "all_years": list(OWNER_ALL.values()),
            "2013_2024": [v * 1.05 for v in OWNER_ALL.values()],
        }
    )


def test_tidy_tpo_sheet_reshapes_to_long_form():
    tidy = tidy_tpo_sheet(_county_frame(), "county")
    assert set(tidy.columns) == {"dimension", "name", "period", "cuft_per_year"}
    # 5 counties x 2 periods
    assert len(tidy) == 10
    assert set(tidy["period"]) == {"all_years", "2013_2024"}
    baker_all = tidy[(tidy["name"] == "Baker") & (tidy["period"] == "all_years")]
    assert baker_all["cuft_per_year"].iloc[0] == pytest.approx(11_760_000)


def test_tidy_tpo_sheet_autodetects_label_column():
    # Label column is the first non-numeric column regardless of its name.
    tidy = tidy_tpo_sheet(_owner_frame(), "owner_group")
    assert set(tidy["name"]) == set(OWNER_ALL)


def test_tidy_tpo_sheet_raises_when_no_numeric_columns():
    df = pd.DataFrame({"County": ["Baker"], "Note": ["n/a"]})
    with pytest.raises(ValueError, match="numeric value columns"):
        tidy_tpo_sheet(df, "county")


def test_nested_from_tidy_builds_name_period_mapping():
    tidy = tidy_tpo_sheet(_county_frame(), "county")
    nested = nested_from_tidy(tidy)
    assert nested["Union"]["all_years"] == pytest.approx(8_700_000)
    assert set(nested["Union"]) == {"all_years", "2013_2024"}


def test_parse_tpo_workbook_end_to_end(tmp_path):
    xlsx = tmp_path / "tpo.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        _owner_frame().to_excel(writer, sheet_name="ByOwnerGroup", index=False)
        _county_frame().to_excel(writer, sheet_name="ByCounty", index=False)

    targets = parse_tpo_workbook(xlsx)

    assert targets["units"] == "cubic_feet_per_year"
    assert targets["source"] == "tpo.xlsx"
    assert targets["by_county"]["Suwannee"]["all_years"] == pytest.approx(18_470_000)
    assert targets["by_owner_group"]["Private"]["all_years"] == pytest.approx(66_300_000)
    # "All" owner-group total reconciles to the plan's grand total.
    assert targets["by_owner_group"]["All"]["all_years"] == pytest.approx(72_050_000)


def test_write_tpo_targets_roundtrips_yaml(tmp_path):
    import yaml

    targets = {
        "units": "cubic_feet_per_year",
        "source": "tpo.xlsx",
        "by_owner_group": {"All": {"all_years": 72_050_000.0}},
        "by_county": {"Union": {"all_years": 8_700_000.0}},
    }
    out = write_tpo_targets(targets, tmp_path / "config" / "tpo_targets.yaml")
    assert out.exists()
    reloaded = yaml.safe_load(out.read_text())
    assert reloaded == targets
