"""Unit tests for the pure (network-free) helpers in notebooks/clearcut_ag_common.py."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "notebooks"))

import clearcut_ag_common as cac  # noqa: E402


def test_confused_evt_codes_are_the_expected_three():
    assert cac.CONFUSED_EVT["Eastern Warm Temperate Pasture and Hayland"] == 7997
    assert cac.CONFUSED_EVT["Southeastern Ruderal Grassland"] == 9823
    assert cac.CONFUSED_EVT[
        "East Gulf Coastal Plain Small Stream and River Floodplain Shrubland"
    ] == 9585
    assert cac.CONFUSED_EVT_VALUES == (7997, 9823, 9585)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Southern Coastal Plain Mesic Slope Evergreen Forest", True),
        ("Eastern Warm Temperate Undeveloped Ruderal Mixed Forest", True),
        ("Southeastern Great Plains Floodplain Woodland", True),
        ("Managed Tree Plantation", True),
        ("Eastern Warm Temperate Pasture and Hayland", False),
        ("Southeastern Ruderal Grassland", False),
        ("East Gulf Coastal Plain Small Stream and River Floodplain Shrubland", False),
        ("", False),
        (None, False),
        (float("nan"), False),  # off-coverage point yields NaN, must not raise
    ],
)
def test_evt_name_is_forest(name, expected):
    assert cac.evt_name_is_forest(name) is expected


def test_evt_change_clearcut_strict():
    # forest in 2016 that becomes one of the three confused classes -> flagged
    assert cac.evt_change_clearcut(True, 7997) is True
    assert cac.evt_change_clearcut(True, 9823) is True
    assert cac.evt_change_clearcut(True, 9585) is True
    # not forest before, or not a confused class after, or missing -> not flagged
    assert cac.evt_change_clearcut(False, 7997) is False
    assert cac.evt_change_clearcut(True, 7998) is False
    assert cac.evt_change_clearcut(True, None) is False


def test_evt_change_clearcut_broad_uses_lifeform_lookup():
    lookup = {
        7997: {"name": "Pasture", "lifeform": "Herb", "physiognomy": "Agricultural"},
        9585: {"name": "Floodplain Shrubland", "lifeform": "Shrub", "physiognomy": "Riparian"},
        4000: {"name": "Some Forest", "lifeform": "Tree", "physiognomy": "Hardwood"},
    }
    assert cac.evt2022_is_ag_herb_shrub(7997, lookup) is True
    assert cac.evt2022_is_ag_herb_shrub(9585, lookup) is True
    assert cac.evt2022_is_ag_herb_shrub(4000, lookup) is False  # tree lifeform
    assert cac.evt2022_is_ag_herb_shrub(9999, lookup) is False  # unknown value
    assert cac.evt2022_is_ag_herb_shrub(None, lookup) is False

    assert cac.evt_change_clearcut_broad(True, 4000, lookup) is False  # still forest
    assert cac.evt_change_clearcut_broad(True, 7997, lookup) is True
    assert cac.evt_change_clearcut_broad(False, 7997, lookup) is False


def test_derive_labels_assigns_expected_primary_labels():
    import pandas as pd

    evt2016_names = {100: "Southern Coastal Plain Evergreen Forest", 200: "Row Crop"}
    evt2022_lookup = {
        7997: {"name": "Pasture and Hayland", "lifeform": "Herb", "physiognomy": "Agricultural"},
        3994: {"name": "Eastern Warm Temperate Row Crop", "lifeform": "Agriculture", "physiognomy": "Agricultural"},
        5000: {"name": "Some Pine Forest", "lifeform": "Tree", "physiognomy": "Conifer"},
    }
    df = pd.DataFrame(
        [
            # forest in 2016, now confused pasture class + LCMS tree removal -> confused wins
            {"lc_pre": 1, "change_event": 9, "evt2016": 100, "evt2022": 7997},
            # LCMS tree removal, EVT2022 not a confused class -> clearcut
            {"lc_pre": 1, "change_event": 9, "evt2016": 100, "evt2022": 5000},
            # stable row crop, not forest in 2016, no removal -> agriculture
            {"lc_pre": 15, "change_event": 15, "evt2016": 200, "evt2022": 3994},
            # nothing special -> other
            {"lc_pre": 15, "change_event": 15, "evt2016": 100, "evt2022": 5000},
        ]
    )
    out = cac.derive_labels(df, evt2016_names, evt2022_lookup)
    assert list(out["label"]) == ["confused", "clearcut", "agriculture", "other"]
    assert list(out["is_forest_2016"]) == [True, True, False, True]
    assert out.loc[0, "confused_name"] == "pasture_hay"
    assert bool(out.loc[0, "is_clearcut"]) is True  # overlap flag preserved despite label


def test_load_evt2022_lookup(tmp_path):
    csv_path = tmp_path / "evt.csv"
    csv_path.write_text(
        "VALUE,EVT_NAME,EVT_LF,EVT_PHYS\n"
        "7997,Eastern Warm Temperate Pasture and Hayland,Herb,Agricultural\n"
        "9585,East Gulf Coastal Plain Small Stream and River Floodplain Shrubland,Shrub,Riparian\n",
        encoding="latin-1",
    )
    lookup = cac.load_evt2022_lookup(csv_path)
    assert lookup[7997]["lifeform"] == "Herb"
    assert lookup[7997]["physiognomy"] == "Agricultural"
    assert lookup[9585]["name"].startswith("East Gulf Coastal Plain")
