"""
The project spatial reference — one source of truth for every CRS decision in ARTEMIS.

The project CRS is **EPSG:5070, NAD83 / Conus Albers** (ArcGIS labels it
``NAD_1983_Contiguous_USA_Albers``). It is declared once in `config/projection.yaml` under
``spatial``, and everything reads it from here. No module should contain the string
``EPSG:5070`` — `tests/test_spatial_ref.py` enforces that, because a hardcoded copy is how
a project ends up with two CRSs that agree until the day one of them is changed.

Why it matters more than a normal config value: TreeMap 2022, LANDFIRE EVT, and the Harris
ownership raster are all natively 5070, 30 m, and pixel-co-registered, and all three carry
*categorical* values — plot IDs, vegetation types, ownership classes. Reprojecting them
means resampling them, and nearest-neighbour resampling of a plot-ID raster changes which
FIA plot a pixel inherits. Staying on 5070 keeps the raster work reproject-and-snap only.

The failure mode this module exists to catch is not a crash. A wrong Albers looks like a
correct map: `ESRI:102008` is off by kilometres and still renders as Florida, `EPSG:6350`
is off by less than a metre and still breaks a 30 m snap grid, and `EPSG:4269` produces
acre figures that are simply wrong. So the assertions here are strict by default and the
error messages name the CRS you actually passed.

Usage:
    from pipeline.spatial_ref import assert_project_crs, project_crs, to_project_crs

    gdf = to_project_crs(gpd.read_file(path))     # reproject if needed, no-op if already
    assert_project_crs(raster)                     # raises with a specific message
    gdf.to_file(out, crs=project_crs())            # when you need the bare string

    uv run python -m pipeline.spatial_ref          # print the declaration
"""

from __future__ import annotations

import argparse
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "projection.yaml"


@lru_cache(maxsize=None)
def _spatial(path: str | None = None) -> dict:
    """The `spatial` block of `config/projection.yaml`."""
    with open(Path(path) if path else CONFIG_PATH) as f:
        return yaml.safe_load(f)["spatial"]


def project_crs() -> str:
    """The project CRS as an authority string, e.g. ``"EPSG:5070"``."""
    return _spatial()["crs"]


def crs_label() -> str:
    """Human-readable label: ``"EPSG:5070 (NAD83 / Conus Albers)"``."""
    spatial = _spatial()
    return f"{spatial['crs']} ({spatial['crs_name']})"


def resolution_m() -> int:
    """Working pixel size in metres."""
    return _spatial()["resolution_m"]


def snap_transform() -> list[float]:
    """
    The TreeMap 2022 affine in GDAL order, ``[xScale, xShear, xOrigin, yShear, yScale, yOrigin]``.

    Pass this to GEE exports as ``crsTransform=``. Never use ``scale=``: it lets GEE choose
    its own origin, and TreeMap's origin is half a pixel off the round 5070 grid, so the
    result is a silent 15 m misalignment.
    """
    return list(_spatial()["snap_transform"])


def snap_origin() -> tuple[float, float]:
    """The ``(x, y)`` upper-left corner of the TreeMap snap grid."""
    transform = snap_transform()
    return transform[2], transform[5]


def confusable_crs() -> dict:
    """The CRSs that are wrong for ARTEMIS and why, keyed by authority string."""
    return dict(_spatial()["crs_not"])


# ---- assertions -----------------------------------------------------------------------

def _crs_of(obj):
    """Pull a CRS off a GeoDataFrame/GeoSeries, a rasterio dataset, or a CRS itself."""
    crs = getattr(obj, "crs", obj)
    if crs is None:
        raise ValueError(
            "object has no CRS. An undefined CRS is not a neutral state — it means "
            f"geometry will be treated as if it were already {crs_label()} when it is not. "
            "Set it explicitly before continuing."
        )
    return crs


def is_project_crs(obj) -> bool:
    """True when ``obj`` is in the project CRS. Compares by CRS equality, not by string."""
    from pyproj import CRS

    try:
        return CRS.from_user_input(_crs_of(obj)) == CRS.from_user_input(project_crs())
    except ValueError:
        return False


def assert_project_crs(obj, context: str = "") -> None:
    """
    Raise unless ``obj`` is in the project CRS.

    Names the CRS that was actually passed, and calls out the confusable ones by name when
    that is what turned up — the whole point is that these do not announce themselves.
    """
    from pyproj import CRS

    crs = _crs_of(obj)
    if is_project_crs(crs):
        return

    where = f" ({context})" if context else ""
    actual = CRS.from_user_input(crs)
    detail = ""
    for code, spec in confusable_crs().items():
        if actual == CRS.from_user_input(code):
            detail = f"\nThat is {code} — {spec['name']}. {spec['why_not'].strip()}"
            break
    if not detail and actual.is_geographic:
        detail = (
            "\nThat CRS is geographic (degrees). Area and length computed in it are "
            "meaningless, so every acre figure downstream would be wrong."
        )

    raise ValueError(
        f"expected the project CRS {crs_label()}{where}, got {actual.name!r}.{detail}\n"
        f"Reproject with pipeline.spatial_ref.to_project_crs(...) before continuing."
    )


def assert_projected_metres(obj, context: str = "") -> None:
    """
    Raise unless ``obj`` is in *some* projected CRS measured in metres.

    The weaker check, for geometry helpers that only need metres rather than the exact
    project grid (`sliver_merge` computes acres from area, and works in any metre CRS).
    """
    from pyproj import CRS

    crs = CRS.from_user_input(_crs_of(obj))
    where = f" ({context})" if context else ""
    if crs.is_geographic:
        raise ValueError(
            f"expected a projected CRS in metres{where}, got the geographic CRS "
            f"{crs.name!r}. Area and length in degrees are meaningless here — reproject "
            f"to {crs_label()} first."
        )
    units = {axis.unit_name for axis in crs.axis_info}
    if not units <= {"metre", "meter"}:
        raise ValueError(
            f"expected a projected CRS in metres{where}, got {crs.name!r} with units "
            f"{sorted(units)}. Reproject to {crs_label()} first."
        )


def to_project_crs(gdf):
    """Reproject a GeoDataFrame/GeoSeries to the project CRS. A no-op when it already is."""
    if getattr(gdf, "crs", None) is None:
        raise ValueError(
            "cannot reproject an object with no CRS — set it explicitly first, since "
            "guessing would silently place the geometry in the wrong place."
        )
    if is_project_crs(gdf):
        return gdf
    return gdf.to_crs(project_crs())


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the ARTEMIS project spatial reference")
    parser.parse_args()

    spatial = _spatial()
    print(f"Project CRS   {crs_label()}")
    print(f"  ArcGIS name {spatial['crs_esri_name']}")
    print(f"  datum       {spatial['crs_datum']}")
    print(f"  projection  {spatial['crs_projection']} ({spatial['crs_units']})")
    for key, value in spatial["crs_parameters"].items():
        print(f"    {key:<22} {value}")
    print(f"\nResolution    {resolution_m()} m, snapped to {spatial['snap_reference']}")
    print(f"  transform   {snap_transform()}")
    print(f"  origin      {snap_origin()}")
    print("\nNot these:")
    for code, spec in confusable_crs().items():
        print(f"  {code:<14} {spec['name']}")


if __name__ == "__main__":
    main()
