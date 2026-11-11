"""A raster-repair run's counties: its manifest's FIPS and the county layer they name."""

import json

import geopandas as gpd
import pytest
from shapely.geometry import box

from pipeline.s1_initial_state.run_counties import (
    NoCountiesInManifest,
    UnknownCounty,
    manifest_counties,
    manifest_county_layer,
    read_counties,
)


def _manifest(run, **fields):
    run.mkdir(exist_ok=True)
    (run / "manifest.json").write_text(json.dumps(fields))


def test_a_manifests_counties_are_its_fips_whatever_name_follows(tmp_path):
    _manifest(tmp_path / "run", counties=["12001 12001", "12003 Baker"])
    assert manifest_counties(tmp_path / "run") == ["12001", "12003"]


def test_a_manifest_listing_no_counties_says_to_name_them(tmp_path):
    _manifest(tmp_path / "run", rule="v4")
    with pytest.raises(NoCountiesInManifest, match="--county"):
        manifest_counties(tmp_path / "run")


def test_the_county_layer_is_the_one_the_manifest_records_or_none(tmp_path):
    _manifest(tmp_path / "run", inputs={"counties": "/data/countyp010g.shp"})
    assert manifest_county_layer(tmp_path / "run").as_posix() == "/data/countyp010g.shp"
    _manifest(tmp_path / "bare", inputs={})
    assert manifest_county_layer(tmp_path / "bare") is None
    assert manifest_county_layer(tmp_path / "no_manifest") is None


@pytest.fixture
def layer(tmp_path):
    """Two counties in EPSG:4326, one of them in two parts (coast and island)."""
    path = tmp_path / "counties.shp"
    gpd.GeoDataFrame({"ADMIN_FIPS": ["12001", "12077", "12077"],
                      "NAME": ["Alachua", "Liberty", "Liberty"]},
                     geometry=[box(-83, 29, -82, 30), box(-85, 30, -84.5, 30.5),
                               box(-84.4, 30, -84.3, 30.1)], crs="EPSG:4326").to_file(path)
    return path


def test_read_counties_gives_one_row_per_fips_in_the_project_crs(layer):
    counties = read_counties(layer, ["12077", "12001"])
    assert counties["ADMIN_FIPS"].tolist() == ["12077", "12001"]   # in the order asked
    assert counties["NAME"].tolist() == ["Liberty", "Alachua"]
    assert counties.crs.to_epsg() == 5070
    assert counties.geometry.iloc[0].geom_type == "MultiPolygon"   # both parts, one row


def test_a_fips_the_layer_does_not_hold_is_an_unknown_county(layer):
    with pytest.raises(UnknownCounty, match="12999"):
        read_counties(layer, ["12001", "12999"])


@pytest.mark.parametrize("fips", ["1201", "12001' OR '1'='1", "abcde"])
def test_a_fips_that_is_not_five_digits_is_refused_before_the_layer_is_read(tmp_path, fips):
    with pytest.raises(UnknownCounty, match="5 digits"):
        read_counties(tmp_path / "missing.shp", [fips])
