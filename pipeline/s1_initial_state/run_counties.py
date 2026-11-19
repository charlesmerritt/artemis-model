"""A raster-repair run's counties: the FIPS its ``manifest.json`` lists and the layer they name.

Every step that works a run by county reads them here: ``leto`` clips each county's cells,
and ``add_back_ids`` counts its young-plot acres by county. A run's manifest lists counties as
``"<FIPS> <name>"``, where the name is often the FIPS again (``"12001 12001"`` statewide), so
only the FIPS is read and names come from the county layer (``ADMIN_FIPS``, ``NAME``).

>>> check_fips("12077")
'12077'
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd

from pipeline.spatial_ref import to_project_crs

RUN_MANIFEST = "manifest.json"


class UnknownCounty(ValueError):
    """A FIPS that names no county in the county layer."""


class NoCountiesInManifest(ValueError):
    """No ``--county`` list, and the raster run's manifest lists no counties to default to."""


def check_fips(fips: str) -> str:
    """``fips`` if it is a 5-digit county FIPS, else :class:`UnknownCounty`; it goes into a query."""
    if not (len(fips) == 5 and fips.isdigit()):
        raise UnknownCounty(f"a county FIPS is 5 digits, not {fips!r}")
    return fips


def _manifest(run_dir: Path) -> dict:
    path = run_dir / RUN_MANIFEST
    return json.loads(path.read_text()) if path.exists() else {}


def manifest_counties(run_dir: Path) -> list[str]:
    """The FIPS the run's ``manifest.json`` lists (``"12125 Union"`` entries)."""
    listed = _manifest(run_dir).get("counties")
    if not listed:
        raise NoCountiesInManifest(f"{run_dir / RUN_MANIFEST} lists no counties: name them with "
                                   "--county")
    return [str(entry).split()[0] for entry in listed]


def manifest_county_layer(run_dir: Path) -> Path | None:
    """The county layer the run's manifest records (``inputs.counties``); ``None`` if none."""
    layer = _manifest(run_dir).get("inputs", {}).get("counties")
    return Path(layer) if layer else None


def read_counties(layer: Path, fips: list[str]) -> gpd.GeoDataFrame:
    """One row per FIPS, in the order given: ``ADMIN_FIPS``, ``NAME`` and every part's geometry,
    in the project CRS. A FIPS the layer does not hold is :class:`UnknownCounty`."""
    quoted = ", ".join(f"'{check_fips(f)}'" for f in fips)
    rows = gpd.read_file(layer, where=f"ADMIN_FIPS IN ({quoted})")
    missing = [f for f in fips if f not in set(rows["ADMIN_FIPS"].astype(str))]
    if missing:
        raise UnknownCounty(f"county {', '.join(missing)} is not in {layer}")
    rows = to_project_crs(rows).assign(ADMIN_FIPS=lambda r: r["ADMIN_FIPS"].astype(str))
    counties = rows.dissolve(by="ADMIN_FIPS", aggfunc="first").reindex(fips).reset_index()
    return counties[["ADMIN_FIPS", "NAME", "geometry"]]
