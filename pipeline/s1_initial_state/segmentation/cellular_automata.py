"""Production wiring for LETO's cellular-automata management-unit segmentation.

`pipeline.leto_ca` is the algorithm itself -- a faithful NumPy/SciPy port of
`aauslander480/Leto`'s `scripts/Cellular_automata/02_segment_treemap.py`,
operating on plain arrays with no I/O of its own. This module is the S1
adapter around it: it reads the same production sources the other S1
segmentation methods read (`pipeline.s1_initial_state.data_sources`), builds
the feature/ownership/valid-mask rasters `leto_ca.segment` needs, and hands
the resulting management units to the same shared attribution contract
(`attribute_management_units`, from `segmentation.leto`) that
`voronoi_tessellation` and `boundary_overlay` use -- so all three methods
produce the identical `MU_ID`/`Acres`/`OWN_CODE`/`OWN_TYPE`/`SMZ_Pct`/
`SEGMENTATION_METHOD` contract regardless of how their geometries were built.

This is the default S1 segmentation method
(`pipeline.s1_initial_state.segmentation.DEFAULT_SEGMENTATION_METHOD`).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.errors import WindowError
from rasterio.features import geometry_window, rasterize, shapes
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window
from shapely import union_all
from shapely.geometry import shape

from pipeline import leto_ca
from pipeline.s1_initial_state.data_sources import (
    load_stand_age_by_plot,
    load_treemap_attributes,
)
from pipeline.s1_initial_state.segmentation.leto import (
    METERS_PER_FOOT,
    SQUARE_METERS_PER_ACRE,
    SegmentationError,
    assign_stable_mu_ids,
    attribute_management_units,
)

SEGMENTATION_METHOD = "cellular_automata"
DEFAULT_SMZ_BUFFER_FEET = 35.0


@dataclass(frozen=True)
class CellularAutomataSegmentationConfig:
    """Thresholds for the LETO cellular-automata segmentation.

    `overrides` is the escape hatch onto every other `pipeline.leto_ca.
    DEFAULT_CFG` knob (variable weights, shared-edge bonus, iteration caps,
    ...) for callers that need to deviate from LETO's validated defaults --
    e.g. a demo-sized AOI needing a smaller `maximum_stand_acres` than the
    300-acre value LETO's real county runs use.
    """

    smz_buffer_feet: float = DEFAULT_SMZ_BUFFER_FEET
    overrides: Mapping[str, object] = field(default_factory=dict)


def _require_crs(features: gpd.GeoDataFrame, label: str) -> None:
    if features.crs is None:
        raise ValueError(f"{label} must define a CRS")


def _read_treemap_window(treemap_path: Path, aoi_geom, aoi_crs):
    """Read the TreeMap VALUE raster over the AOI, padded by one pixel."""
    with rasterio.open(treemap_path) as source:
        if source.crs is None:
            raise ValueError("TreeMap raster must define a CRS")
        aoi_in_raster_crs = (
            gpd.GeoSeries([aoi_geom], crs=aoi_crs).to_crs(source.crs).iloc[0]
        )
        try:
            aoi_window = geometry_window(source, [aoi_in_raster_crs])
        except WindowError as error:
            raise ValueError("AOI overlaps no TreeMap cells") from error
        col_start = max(0, int(aoi_window.col_off) - 1)
        row_start = max(0, int(aoi_window.row_off) - 1)
        col_stop = min(source.width, int(aoi_window.col_off + aoi_window.width) + 1)
        row_stop = min(source.height, int(aoi_window.row_off + aoi_window.height) + 1)
        window = Window(col_start, row_start, col_stop - col_start, row_stop - row_start)
        tm_values = source.read(1, window=window)
        nodata = source.nodata
        transform = source.window_transform(window)
        crs = source.crs
    valid = np.ones(tm_values.shape, dtype=bool) if nodata is None else tm_values != nodata
    return tm_values, valid, transform, crs, aoi_in_raster_crs


def _warp_ownership_to_grid(ownership_path: Path, dst_transform, dst_shape, dst_crs):
    """Resample the ownership raster onto the TreeMap AOI grid.

    Ownership is a hard segmentation boundary evaluated per TreeMap cell, so
    it must land on that exact grid even though the source ownership raster
    (Harris et al. 2025) is not natively co-registered with TreeMap.
    Nearest-neighbour resampling keeps it categorical.
    """
    with rasterio.open(ownership_path) as source:
        if source.crs is None:
            raise ValueError("Ownership raster must define a CRS")
        destination = np.full(dst_shape, -1, dtype=np.int16)
        reproject(
            source=rasterio.band(source, 1),
            destination=destination,
            src_crs=source.crs,
            dst_crs=dst_crs,
            dst_transform=dst_transform,
            src_nodata=source.nodata,
            dst_nodata=-1,
            resampling=Resampling.nearest,
        )
    return destination


def _attribute_grids(
    tm_values: np.ndarray,
    valid: np.ndarray,
    treemap_vat_path: Path,
    fiadb_path: Path,
):
    """Build STDAGE/BALIVE/QMD/TPA feature grids and a FORTYPCD grid from the
    TreeMap VAT (BALIVE/QMD/TPA/FORTYPCD) plus a FIA COND join (STDAGE)."""
    if not valid.any():
        raise SegmentationError("AOI contains no valid TreeMap cells")

    aoi_values = np.unique(tm_values[valid])
    vat = load_treemap_attributes(treemap_vat_path)
    vat_aoi = vat[vat["VALUE"].isin(aoi_values)].copy()
    if vat_aoi.empty:
        raise SegmentationError("No TreeMap VAT rows match the AOI's TreeMap values")

    stand_age = load_stand_age_by_plot(fiadb_path, vat_aoi["PLT_CN"])
    vat_aoi = vat_aoi.merge(stand_age, on="PLT_CN", how="left")
    if vat_aoi["STDAGE"].isna().any():
        median_age = vat_aoi["STDAGE"].median()
        if pd.isna(median_age):
            raise SegmentationError(
                "No AOI plot has a FIA COND STDAGE; cannot fill the missing values"
            )
        vat_aoi["STDAGE"] = vat_aoi["STDAGE"].fillna(median_age)

    lookup_max = int(aoi_values.max())
    grids: dict[str, np.ndarray] = {}
    for column in ("STDAGE", "FORTYPCD", "BALIVE", "QMD", "TPA"):
        lut = np.full(lookup_max + 1, np.nan, dtype="float64")
        lut[vat_aoi["VALUE"].astype("int64").to_numpy()] = vat_aoi[column].to_numpy(
            dtype="float64"
        )
        grid = np.zeros(tm_values.shape, dtype="float32")
        grid[valid] = lut[tm_values[valid]].astype("float32")
        grids[column] = grid

    forest_type = grids.pop("FORTYPCD").astype("int32")
    treemap_lookup = vat_aoi[["VALUE", "PLT_CN"]].reset_index(drop=True)
    return grids, forest_type, treemap_lookup


def _riparian_mask(streams: gpd.GeoDataFrame, transform, shape_hw, crs, buffer_feet: float):
    """Rasterize a uniform-width stream buffer onto the TreeMap AOI grid."""
    if buffer_feet <= 0:
        return np.zeros(shape_hw, dtype="uint8")
    _require_crs(streams, "Streams")
    stream_geometry = streams.to_crs(crs).geometry
    stream_geometry = stream_geometry.loc[stream_geometry.notna() & ~stream_geometry.is_empty]
    if stream_geometry.empty:
        return np.zeros(shape_hw, dtype="uint8")
    buffer_distance_m = buffer_feet * METERS_PER_FOOT
    smz = union_all(stream_geometry.buffer(buffer_distance_m))
    if smz.is_empty:
        return np.zeros(shape_hw, dtype="uint8")
    return rasterize([(smz, 1)], out_shape=shape_hw, transform=transform, fill=0, dtype="uint8")


def _labels_to_units(labels: np.ndarray, transform, crs) -> gpd.GeoDataFrame:
    """Vectorize an int32 management-unit label raster (0 = nodata)."""
    mask = labels > 0
    if not mask.any():
        return gpd.GeoDataFrame(geometry=[], crs=crs)
    records = [
        {"label": int(value), "geometry": shape(geometry)}
        for geometry, value in shapes(labels.astype("int32"), mask=mask, transform=transform)
    ]
    units = gpd.GeoDataFrame(records, crs=crs)
    units = units.dissolve(by="label", as_index=False)
    return units.drop(columns=["label"])


def build_cellular_automata_management_units(
    treemap_path: Path,
    treemap_vat_path: Path,
    fiadb_path: Path,
    parcels: gpd.GeoDataFrame,
    ownership_path: Path,
    streams: gpd.GeoDataFrame,
    config: CellularAutomataSegmentationConfig | None = None,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Run LETO's cellular-automata segmentation and attribute the result.

    Mirrors `leto.build_leto_management_units`'s shape (parcels define the
    AOI; ownership/streams/treemap sources are the same production paths)
    so the two are interchangeable behind a `SEGMENTATION_METHOD` flag, but
    reads two additional sources the CA algorithm's cost function needs that
    the Voronoi method doesn't: the TreeMap VAT's BALIVE/QMD/TPA/FORTYPCD
    columns and each donor plot's FIA COND STDAGE.
    """
    _require_crs(parcels, "Parcels")
    if parcels.empty:
        raise ValueError("Parcels must contain at least one geometry")
    config = config or CellularAutomataSegmentationConfig()

    aoi_geom = parcels.geometry.union_all()
    tm_values, tm_valid, transform, crs, aoi_in_raster_crs = _read_treemap_window(
        treemap_path, aoi_geom, parcels.crs
    )
    parcel_mask = rasterize(
        [(aoi_in_raster_crs, 1)], out_shape=tm_values.shape, transform=transform,
        fill=0, dtype="uint8",
    ).astype(bool)
    valid = tm_valid & parcel_mask
    if not valid.any():
        raise SegmentationError("Parcels overlap no valid TreeMap cells")

    features, forest_type, treemap_lookup = _attribute_grids(
        tm_values, valid, treemap_vat_path, fiadb_path
    )
    ownership = _warp_ownership_to_grid(ownership_path, transform, tm_values.shape, crs)
    ownership = np.where(valid, ownership, -1).astype("int16")

    cell_acres = abs(transform.a * transform.e) / SQUARE_METERS_PER_ACRE
    cfg = {**leto_ca.DEFAULT_CFG, **dict(config.overrides)}
    parent_labels = leto_ca.segment(
        features, forest_type, ownership, valid, cell_acres, cfg=cfg, log=lambda *a, **k: None
    )

    riparian_mask = _riparian_mask(streams, transform, tm_values.shape, crs, config.smz_buffer_feet)
    mu_labels = leto_ca.split_management_units(parent_labels, riparian_mask)

    units = _labels_to_units(mu_labels, transform, crs)
    if units.empty:
        raise SegmentationError("Cellular-automata segmentation produced no management units")
    units = assign_stable_mu_ids(units, method=SEGMENTATION_METHOD)

    return attribute_management_units(
        units,
        treemap_path,
        treemap_lookup,
        ownership_path,
        streams,
        smz_buffer_feet=config.smz_buffer_feet,
    )
