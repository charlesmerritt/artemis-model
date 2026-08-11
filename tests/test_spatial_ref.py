"""
Tests for the project spatial reference (pipeline/spatial_ref.py).

Two jobs. First, pin the CRS itself against pyproj — the declared parameters have to be the
parameters EPSG:5070 actually has, or the config is documentation of a fiction. Second,
enforce that nothing hardcodes a CRS: a second copy of "EPSG:5070" somewhere in the tree
agrees with the config right up until the day one of them changes.
"""

import ast
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.spatial_ref import (
    assert_project_crs,
    assert_projected_metres,
    confusable_crs,
    crs_label,
    is_project_crs,
    project_crs,
    resolution_m,
    snap_origin,
    snap_transform,
    to_project_crs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CRS = pytest.importorskip("pyproj").CRS


# ---- the declaration is true --------------------------------------------------------

def test_project_crs_is_nad83_conus_albers():
    assert project_crs() == "EPSG:5070"
    assert CRS.from_user_input(project_crs()).name == "NAD83 / Conus Albers"


def _params(code: str) -> dict[str, float]:
    """Projection parameters by EPSG name, straight off the coordinate operation."""
    return {p.name: p.value for p in CRS.from_user_input(code).coordinate_operation.params}


def test_declared_parameters_match_the_real_crs(projection_config):
    """The config's parameter block must describe the CRS it names, not a nearby one."""
    declared = projection_config["spatial"]["crs_parameters"]
    actual = _params(project_crs())
    assert actual["Latitude of 1st standard parallel"] == declared["standard_parallel_1"]
    assert actual["Latitude of 2nd standard parallel"] == declared["standard_parallel_2"]
    assert actual["Latitude of false origin"] == declared["latitude_of_origin"]
    assert actual["Longitude of false origin"] == declared["central_meridian"]
    assert actual["Easting at false origin"] == declared["false_easting"]
    assert actual["Northing at false origin"] == declared["false_northing"]


def test_project_crs_is_projected_equal_area_in_metres():
    """Acres and hectares are computed straight from geometry, so this is load-bearing."""
    crs = CRS.from_user_input(project_crs())
    assert not crs.is_geographic
    assert {axis.unit_name for axis in crs.axis_info} <= {"metre", "meter"}
    assert "Albers" in crs.coordinate_operation.method_name


def test_arcgis_name_is_recorded_accurately(projection_config):
    """The ArcGIS label is how this CRS is identified in the GIS half of the project."""
    esri_wkt = CRS.from_user_input(project_crs()).to_wkt(version="WKT1_ESRI")
    assert projection_config["spatial"]["crs_esri_name"] in esri_wkt


def test_crs_label_names_both_the_code_and_the_name():
    assert "EPSG:5070" in crs_label()
    assert "NAD83 / Conus Albers" in crs_label()


# ---- the confusables are genuinely different ----------------------------------------

def test_every_listed_confusable_is_a_real_crs_that_is_not_the_project_crs():
    """A `crs_not` entry that silently equals 5070 would train people to ignore the list."""
    project = CRS.from_user_input(project_crs())
    for code, spec in confusable_crs().items():
        other = CRS.from_user_input(code)
        assert spec["name"] and spec["why_not"].strip(), f"{code} has no stated reason"
        if code == "ESRI:102039":
            # Documented as numerically equivalent; listed so people use the EPSG code.
            assert _params(code) == _params(project_crs())
            continue
        assert other != project, f"{code} is listed as wrong but equals the project CRS"


def test_north_america_albers_is_a_genuinely_different_grid():
    """ESRI:102008 is the likeliest mix-up: same datum, same family, wrong parallels."""
    other, project = _params("ESRI:102008"), _params(project_crs())
    assert other["Latitude of 1st standard parallel"] != project["Latitude of 1st standard parallel"]
    assert other["Latitude of false origin"] != project["Latitude of false origin"]


# ---- the snap grid --------------------------------------------------------------------

def test_snap_transform_is_the_treemap_affine():
    transform = snap_transform()
    assert len(transform) == 6
    assert transform[0] == resolution_m()
    assert transform[4] == -resolution_m()
    assert snap_origin() == (transform[2], transform[5])


def test_snap_origin_is_half_a_pixel_off_the_round_grid():
    """The reason exports must pass crsTransform= rather than scale=.

    TreeMap's origin is not a multiple of 30, so a raster snapped to a "round" 5070 origin
    is misaligned by 15 m — visually invisible, and enough to hand a pixel the wrong plot.
    """
    x, y = snap_origin()
    assert x % resolution_m() != 0
    assert (x % resolution_m()) == resolution_m() / 2


def test_snap_transform_is_a_copy_callers_cannot_mutate():
    snap_transform()[0] = 999
    assert snap_transform()[0] == resolution_m()


# ---- assertions ------------------------------------------------------------------------

def test_is_project_crs_compares_by_equality_not_by_string():
    assert is_project_crs(CRS.from_epsg(5070))
    assert is_project_crs("EPSG:5070")
    assert is_project_crs(CRS.from_user_input("ESRI:102039"))   # equivalent parameters
    assert not is_project_crs("EPSG:4269")


def test_assert_project_crs_passes_silently_on_the_project_crs():
    assert assert_project_crs("EPSG:5070") is None


def test_assert_project_crs_names_the_confusable_it_actually_got():
    with pytest.raises(ValueError, match="ESRI:102008"):
        assert_project_crs("ESRI:102008")


def test_assert_project_crs_explains_why_a_geographic_crs_is_wrong():
    with pytest.raises(ValueError, match="geographic"):
        assert_project_crs("EPSG:4269")


def test_assert_project_crs_includes_the_caller_context():
    with pytest.raises(ValueError, match="loading parcels"):
        assert_project_crs("EPSG:4326", context="loading parcels")


def test_a_missing_crs_is_an_error_not_a_neutral_state():
    """An undefined CRS means geometry gets treated as if it were already correct."""
    with pytest.raises(ValueError, match="no CRS"):
        assert_project_crs(None)


def test_assert_projected_metres_accepts_any_metre_crs_but_not_degrees():
    assert assert_projected_metres("EPSG:5070") is None
    assert assert_projected_metres("EPSG:26917") is None      # NAD83 / UTM 17N, the parcel CRS
    with pytest.raises(ValueError, match="geographic"):
        assert_projected_metres("EPSG:4269")


def test_to_project_crs_reprojects_and_is_a_no_op_when_already_correct():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(geometry=[Point(-82.5, 30.0)], crs="EPSG:4269")
    reprojected = to_project_crs(gdf)
    assert is_project_crs(reprojected)
    assert to_project_crs(reprojected) is reprojected


def test_to_project_crs_refuses_to_guess_a_missing_crs():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)])
    with pytest.raises(ValueError, match="no CRS"):
        to_project_crs(gdf)


# ---- the tripwire ----------------------------------------------------------------------

def _crs_literals(path: Path) -> list[int]:
    """Line numbers where the project CRS appears as a *value*, not as prose.

    Walks the AST and matches string literals equal to the CRS code, so a docstring or a
    ``--help`` string that mentions EPSG:5070 in a sentence is fine — stating the CRS in
    documentation is the goal — while `crs="EPSG:5070"` is not.
    """
    tree = ast.parse(path.read_text())
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip() == project_crs()
        and node not in docstrings
    ]


def test_no_module_hardcodes_the_project_crs():
    """One CRS value, in one place.

    A second copy agrees with the config right up until the day one of them changes, and
    then produces a landscape that is 15 m — or 400 km — off with no error anywhere.
    Read it from `pipeline.spatial_ref` instead.
    """
    searched = [
        p for directory in ("pipeline", "gee", "research", "scripts")
        for p in (REPO_ROOT / directory).rglob("*.py")
        if p.name != "spatial_ref.py" and "__pycache__" not in p.parts
    ]
    assert searched, "found no Python files to check — the tripwire is not actually running"

    offenders = {
        str(path.relative_to(REPO_ROOT)): lines
        for path in searched
        if (lines := _crs_literals(path))
    }
    assert not offenders, (
        f"hardcoded project CRS at {offenders}. Import it: "
        f"`from pipeline.spatial_ref import project_crs`."
    )


def test_the_tripwire_detects_a_hardcoded_crs(tmp_path):
    """Guard the guard: a tripwire that cannot fail is not protecting anything."""
    module = tmp_path / "offender.py"
    module.write_text(
        f'"""Docstring mentioning {project_crs()} in prose is fine."""\n'
        f'CRS = "{project_crs()}"\n'
    )
    assert _crs_literals(module) == [2]
