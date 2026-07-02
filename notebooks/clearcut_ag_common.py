"""Shared helpers for the clearcut-vs-agriculture discrimination notebooks.

Two notebooks build on this module:

- ``Clearcut-vs-Agriculture-Embeddings.ipynb``  (Method 1: AlphaEarth separability)
- ``Clearcut-vs-Agriculture-EVT-Change.ipynb``  (Method 2: LANDFIRE EVT change)

Both work off a single labeled point-sample table so the two methods are directly
comparable on identical locations. GEE supplies AlphaEarth embeddings, LCMS history, and
the single available LANDFIRE EVT vintage (v1.4.0, ~2016). The local LF2022 EVT tif
(EPSG:5070, 30 m) supplies the modern EVT class per point via windowed rasterio sampling.

Design doc: docs/superpowers/specs/2026-07-01-clearcut-vs-agriculture-embeddings-design.md
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import yaml

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

EMBEDDING_COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
EMBEDDING_BANDS = tuple(f"A{i:02d}" for i in range(64))
EMBEDDING_SCALE_M = 10  # AlphaEarth native resolution

LCMS_ASSET = "projects/gtac-data-publish/assets/LCMS/Product_Version/2025-11"
LCMS_STUDY_AREA = "CONUS"
LCMS_LAND_COVER_TREES = 1  # Land_Cover class
LCMS_CHANGE_TREE_REMOVAL = 9  # Change class

# GEE hosts only a single LANDFIRE EVT vintage (v1.4.0, circa 2016, pre-"Remap" codes).
# There is no annual EVT series on GEE, so Method 2 pairs this with the local LF2022 tif.
EVT_2016_ASSET = "LANDFIRE/Vegetation/EVT/v1_4_0/CONUS"
EVT_SCALE_M = 30

# LF2022 EVT codes (Remap scheme) for the three classes commonly confused with clearcut
# forest on the ground. Values verified against the LF2022_EVT CSV.
CONFUSED_EVT = {
    "Eastern Warm Temperate Pasture and Hayland": 7997,
    "Southeastern Ruderal Grassland": 9823,
    "East Gulf Coastal Plain Small Stream and River Floodplain Shrubland": 9585,
}
CONFUSED_EVT_VALUES = tuple(CONFUSED_EVT.values())

# Short tags for the three confused classes (for compact group labels / plots).
CONFUSED_EVT_SHORT = {
    7997: "pasture_hay",
    9823: "ruderal_grass",
    9585: "floodplain_shrub",
}

# EVT lifeforms (LF2022 EVT_LF field) treated as agriculture / grass / shrub — the broad
# "not forest anymore" target for the change detector.
AG_HERB_SHRUB_LIFEFORMS = ("Herb", "Agriculture", "Shrub")

# Keywords used to call a LANDFIRE EVT *name* a forest type. The 2016 GEE vintage exposes
# only class names (no lifeform field), so forest is inferred from the name. Ecological
# system names carry a physiognomy suffix (e.g. "... Evergreen Forest", "... Woodland").
FOREST_NAME_KEYWORDS = ("forest", "woodland", "plantation")

# Analysis defaults (event window sits inside AlphaEarth coverage 2017-2024)
DEFAULT_PRE_YEAR = 2020
DEFAULT_EVENT_YEAR = 2022


# --------------------------------------------------------------------------------------
# Repo / config loading
# --------------------------------------------------------------------------------------

def find_repo_root(start: Path | None = None) -> Path:
    start = start or Path.cwd()
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "config").exists():
            return candidate
    raise RuntimeError("Could not find repository root from current working directory")


def load_data_paths(repo_root: Path | None = None) -> dict:
    repo_root = repo_root or find_repo_root()
    with open(repo_root / "config" / "data_paths.yaml") as fh:
        return yaml.safe_load(fh)


def evt2022_tif_path(repo_root: Path | None = None) -> str:
    return load_data_paths(repo_root)["raw"]["landfire"]["evt_tif"]


def evt2022_csv_path(repo_root: Path | None = None) -> Path:
    """Class-attribute CSV that ships beside the LF2022 EVT tif (``.../CSV_Data/*.csv``)."""
    tif = Path(evt2022_tif_path(repo_root))
    csv_dir = tif.parent.parent / "CSV_Data"
    matches = sorted(csv_dir.glob("*_EVT.csv"))
    if not matches:
        raise FileNotFoundError(f"No *_EVT.csv found in {csv_dir}")
    return matches[0]


# --------------------------------------------------------------------------------------
# Pure helpers (no network — covered by tests/test_clearcut_ag_common.py)
# --------------------------------------------------------------------------------------

def evt_name_is_forest(name) -> bool:
    """True if a LANDFIRE EVT class name denotes a forest/woodland/plantation type.

    Tolerates non-string input (``None`` or a NaN float from an off-coverage point).
    """
    if not isinstance(name, str) or not name:
        return False
    lowered = name.lower()
    return any(keyword in lowered for keyword in FOREST_NAME_KEYWORDS)


def evt_change_clearcut(
    is_forest_2016: bool,
    evt2022_value: int | float | None,
    confused_values: tuple[int, ...] = CONFUSED_EVT_VALUES,
) -> bool:
    """Strict change flag: EVT forest in 2016 -> one of the three confused classes in 2022."""
    if not is_forest_2016 or evt2022_value is None:
        return False
    return int(evt2022_value) in confused_values


def evt2022_is_ag_herb_shrub(evt2022_value: int | float | None, lookup: dict[int, dict]) -> bool:
    """Broad "no longer forest" test: EVT_LF in {Herb, Agriculture, Shrub}."""
    if evt2022_value is None:
        return False
    record = lookup.get(int(evt2022_value))
    if record is None:
        return False
    return record["lifeform"] in AG_HERB_SHRUB_LIFEFORMS


def evt_change_clearcut_broad(
    is_forest_2016: bool, evt2022_value: int | float | None, lookup: dict[int, dict]
) -> bool:
    """Broad change flag: EVT forest in 2016 -> any ag/grass/shrub lifeform in 2022."""
    return bool(is_forest_2016) and evt2022_is_ag_herb_shrub(evt2022_value, lookup)


def load_evt2022_lookup(csv_path: str | Path) -> dict[int, dict]:
    """Parse the LF2022_EVT CSV into ``{VALUE: {name, lifeform, physiognomy}}``."""
    lookup: dict[int, dict] = {}
    with open(csv_path, newline="", encoding="latin-1") as fh:
        for row in csv.DictReader(fh):
            lookup[int(row["VALUE"])] = {
                "name": row["EVT_NAME"],
                "lifeform": row["EVT_LF"],
                "physiognomy": row["EVT_PHYS"],
            }
    return lookup


# --------------------------------------------------------------------------------------
# Earth Engine helpers (import ee lazily so the pure helpers stay import-safe offline)
# --------------------------------------------------------------------------------------

def init_ee():
    import ee

    try:
        ee.Initialize()
    except Exception:
        ee.Authenticate()
        ee.Initialize()
    return ee


def load_florida(repo_root: Path | None = None):
    """Return (ee.Geometry florida, GeoDataFrame) in EPSG:4326."""
    import ee
    import geopandas as gpd

    repo_root = repo_root or find_repo_root()
    gdf = gpd.read_file(repo_root / "config" / "extent.geojson").to_crs("EPSG:4326")
    florida = ee.Geometry(gdf.geometry.iloc[0].__geo_interface__)
    return florida, gdf


def annual_embedding(year: int, florida):
    import ee

    start = ee.Date.fromYMD(year, 1, 1)
    end = start.advance(1, "year")
    collection = ee.ImageCollection(EMBEDDING_COLLECTION)
    return collection.filterDate(start, end).filterBounds(florida).mosaic().clip(florida)


def lcms_image(year: int, band: str, florida):
    import ee

    lcms = ee.ImageCollection(LCMS_ASSET).filter(ee.Filter.eq("study_area", LCMS_STUDY_AREA))
    return ee.Image(lcms.filter(ee.Filter.eq("year", year)).first()).select(band).clip(florida)


def clearcut_mask(pre_year: int, event_year: int, florida):
    """Pixels that were LCMS Trees at ``pre_year`` and had Tree Removal at ``event_year``."""
    import ee

    pre_trees = lcms_image(pre_year, "Land_Cover", florida).eq(LCMS_LAND_COVER_TREES)
    removal = lcms_image(event_year, "Change", florida).eq(LCMS_CHANGE_TREE_REMOVAL)
    return pre_trees.And(removal).selfMask().rename("clearcut")


def load_evt2016_name_lookup():
    """``{class_value: class_name}`` for the 2016 GEE EVT vintage (one getInfo call)."""
    import ee

    image = ee.Image(EVT_2016_ASSET)
    props = image.toDictionary(["EVT_class_values", "EVT_class_names"]).getInfo()
    return dict(zip(props["EVT_class_values"], props["EVT_class_names"]))


def _feature_collection(lonlats, id_start: int = 0):
    import ee

    features = [
        ee.Feature(ee.Geometry.Point([lon, lat]), {"pid": id_start + i})
        for i, (lon, lat) in enumerate(lonlats)
    ]
    return ee.FeatureCollection(features)


def random_florida_points(n: int, florida, seed: int = 42) -> list[tuple[float, float]]:
    """``n`` uniformly random lon/lat points inside Florida."""
    import ee

    fc = ee.FeatureCollection.randomPoints(region=florida, points=n, seed=seed)
    coords = fc.geometry().coordinates().getInfo()
    return [(lon, lat) for lon, lat in coords]


def clearcut_sample_points(
    pre_year: int, event_year: int, n: int, florida, seed: int = 7
) -> list[tuple[float, float]]:
    """Random points drawn from the LCMS clearcut mask."""
    import ee

    mask = clearcut_mask(pre_year, event_year, florida)
    fc = mask.toInt().stratifiedSample(
        numPoints=n,
        classBand="clearcut",
        region=florida,
        scale=30,
        seed=seed,
        geometries=True,
    )
    coords = fc.geometry().coordinates().getInfo()
    return [(lon, lat) for lon, lat in coords]


def sample_gee_attributes(
    lonlats: list[tuple[float, float]],
    event_year: int,
    pre_year: int,
    florida,
    chunk_size: int = 400,
) -> pd.DataFrame:
    """Sample AlphaEarth (event year), LCMS (pre + event), and EVT-2016 at each point.

    Returns one row per point with columns:
    ``pid, lon, lat, A00..A63, lc_pre, lc_event, change_event, evt2016``.
    """
    import ee

    embedding = annual_embedding(event_year, florida)
    lc_pre = lcms_image(pre_year, "Land_Cover", florida).rename("lc_pre")
    lc_event = lcms_image(event_year, "Land_Cover", florida).rename("lc_event")
    change_event = lcms_image(event_year, "Change", florida).rename("change_event")
    evt2016 = ee.Image(EVT_2016_ASSET).select("EVT").rename("evt2016")
    stack = embedding.addBands([lc_pre, lc_event, change_event, evt2016])

    rows: list[dict] = []
    for start in range(0, len(lonlats), chunk_size):
        chunk = lonlats[start : start + chunk_size]
        fc = _feature_collection(chunk, id_start=start)
        sampled = stack.sampleRegions(
            collection=fc, scale=EMBEDDING_SCALE_M, geometries=True, tileScale=4
        ).getInfo()
        for feature in sampled["features"]:
            props = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"]
            props["lon"] = lon
            props["lat"] = lat
            rows.append(props)

    df = pd.DataFrame(rows)
    ordered = ["pid", "lon", "lat", *EMBEDDING_BANDS, "lc_pre", "lc_event", "change_event", "evt2016"]
    present = [c for c in ordered if c in df.columns]
    return df[present].sort_values("pid").reset_index(drop=True)


def sample_local_evt2022(lonlats: list[tuple[float, float]], tif_path: str | Path) -> list[int | None]:
    """Windowed rasterio sample of the local LF2022 EVT tif (EPSG:5070) at lon/lat points."""
    import rasterio
    from pyproj import Transformer

    with rasterio.open(tif_path) as dataset:
        transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        xy = [transformer.transform(lon, lat) for lon, lat in lonlats]
        nodata = dataset.nodata
        values: list[int | None] = []
        for value in dataset.sample(xy):
            v = value[0]
            values.append(None if (nodata is not None and v == nodata) else int(v))
    return values


# --------------------------------------------------------------------------------------
# Labeling (pure — operates on a DataFrame + lookup dicts; covered by tests)
# --------------------------------------------------------------------------------------

def derive_labels(
    df: pd.DataFrame,
    evt2016_names: dict[int, str],
    evt2022_lookup: dict[int, dict],
) -> pd.DataFrame:
    """Attach EVT attributes, per-signal boolean flags, and a primary ``label`` column.

    Expects columns ``lc_pre, lc_event, change_event, evt2016, evt2022`` (as produced by
    :func:`sample_gee_attributes` + :func:`sample_local_evt2022`). Primary label priority:
    ``confused`` (any of the three EVT classes) > ``clearcut`` (LCMS tree removal) >
    ``agriculture`` (stable row-crop/agricultural EVT, not forest in 2016) > ``other``.
    Confused points are excluded from the clean clearcut/agriculture training anchors so
    they can be adjudicated by both methods.
    """
    out = df.copy()

    out["evt2016_name"] = out["evt2016"].map(lambda v: evt2016_names.get(int(v)) if pd.notna(v) else None)
    out["is_forest_2016"] = out["evt2016_name"].map(evt_name_is_forest)

    def _field(value, key):
        if pd.isna(value):
            return None
        rec = evt2022_lookup.get(int(value))
        return rec[key] if rec else None

    out["evt2022_name"] = out["evt2022"].map(lambda v: _field(v, "name"))
    out["evt2022_lifeform"] = out["evt2022"].map(lambda v: _field(v, "lifeform"))
    out["evt2022_phys"] = out["evt2022"].map(lambda v: _field(v, "physiognomy"))

    out["is_clearcut"] = (out["lc_pre"] == LCMS_LAND_COVER_TREES) & (
        out["change_event"] == LCMS_CHANGE_TREE_REMOVAL
    )
    out["is_confused"] = out["evt2022"].isin(CONFUSED_EVT_VALUES)
    out["confused_name"] = out["evt2022"].map(lambda v: CONFUSED_EVT_SHORT.get(int(v)) if pd.notna(v) else None)
    out["is_true_ag"] = (
        (out["evt2022_phys"] == "Agricultural")
        & (~out["is_forest_2016"])
        & (~out["is_clearcut"])
        & (~out["is_confused"])
    )

    def _label(row):
        if row["is_confused"]:
            return "confused"
        if row["is_clearcut"]:
            return "clearcut"
        if row["is_true_ag"]:
            return "agriculture"
        return "other"

    out["label"] = out.apply(_label, axis=1)
    return out


def build_sample_table(
    florida,
    repo_root: Path,
    event_year: int = DEFAULT_EVENT_YEAR,
    pre_year: int = DEFAULT_PRE_YEAR,
    n_clearcut: int = 300,
    n_random: int = 1500,
    manual_anchors: list[tuple[float, float]] | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """End-to-end labeled sample table shared by both notebooks (issues GEE calls)."""
    cc_points = clearcut_sample_points(pre_year, event_year, n_clearcut, florida, seed=seed)
    rand_points = random_florida_points(n_random, florida, seed=seed)
    manual = list(manual_anchors or [])
    all_points = cc_points + rand_points + manual

    df = sample_gee_attributes(all_points, event_year, pre_year, florida)
    df["evt2022"] = sample_local_evt2022(list(zip(df["lon"], df["lat"])), evt2022_tif_path(repo_root))

    evt2016_names = load_evt2016_name_lookup()
    evt2022_lookup = load_evt2022_lookup(evt2022_csv_path(repo_root))
    df = derive_labels(df, evt2016_names, evt2022_lookup)
    df["pre_year"] = pre_year
    df["event_year"] = event_year
    return df


# ======================================================================================
# Feature engineering for the "is this grassland pixel actually forest?" model
# ======================================================================================
#
# The eventual model predicts, for a pixel LANDFIRE EVT calls grassland/ag/shrub, whether
# it is actually recently clearcut forest. LCMS tree-removal almost never fires *inside*
# those EVT classes, so labels cannot come from within the grassland universe. Instead the
# model is anchor-based / semi-supervised:
#   - positive anchors ("forest"): confident clearcuts (LCMS pre-Trees -> Tree Removal)
#   - negative anchors ("grassland"): stable genuine non-forest (never Trees in LCMS,
#     not forest in EVT-2016, not a clearcut)
#   - apply set: the three confused EVT classes, scored by the trained model.
# This module builds the labeled feature table; the notebook trains/validates.

HIST_START_YEAR = 2015  # LCMS history window start for temporal features

# Feature families and their leakage relationship to an LCMS-derived label. A model whose
# label is defined by LCMS must NOT use lcms_derived features (trivial leakage); the
# "clean" predictor set is embeddings + EVT.
FEATURE_FAMILIES = {
    "embedding_event": {"lcms_derived": False},
    "embedding_pre": {"lcms_derived": False},
    "embedding_delta": {"lcms_derived": False},
    "evt": {"lcms_derived": False},
    "lcms": {"lcms_derived": True},
}


def evt_values_by_lifeform(evt2022_lookup: dict[int, dict], lifeforms: tuple[str, ...]) -> list[int]:
    return [v for v, rec in evt2022_lookup.items() if rec["lifeform"] in lifeforms]


def evt_values_by_physiognomy(evt2022_lookup: dict[int, dict], phys: tuple[str, ...]) -> list[int]:
    return [v for v, rec in evt2022_lookup.items() if rec["physiognomy"] in phys]


def stratified_evt_points(
    strata: dict[str, list[int]],
    n_per_stratum: int | dict[str, int],
    florida_gdf_5070,
    tif_path: str | Path,
    dec: int = 8,
    seed: int = 42,
    oversample: float = 3.0,
) -> pd.DataFrame:
    """Sample points of specific LF2022 EVT classes from a *single* decimated read.

    A decimated (factor ``dec``) read of the Florida window is fast (~5 s) yet still contains
    hundreds of pixels of even rare classes (e.g. floodplain shrubland). Pass all strata at
    once so the window is read only once. ``n_per_stratum`` may be an int or per-stratum dict.
    Returns a DataFrame with ``lon, lat, stratum`` (points confirmed inside Florida).
    """
    import geopandas as gpd
    import numpy as np
    import rasterio
    from rasterio.transform import xy as transform_xy
    from rasterio.windows import Window, from_bounds
    from pyproj import Transformer

    rng = np.random.default_rng(seed)
    minx, miny, maxx, maxy = florida_gdf_5070.total_bounds

    def n_for(stratum: str) -> int:
        return n_per_stratum[stratum] if isinstance(n_per_stratum, dict) else n_per_stratum

    rows: list[dict] = []
    with rasterio.open(tif_path) as ds:
        win = from_bounds(minx, miny, maxx, maxy, ds.transform)
        col0, row0 = win.col_off, win.row_off
        full_w, full_h = int(win.width), int(win.height)
        out_w, out_h = full_w // dec, full_h // dec
        step_x, step_y = full_w / out_w, full_h / out_h
        arr = ds.read(1, window=win, out_shape=(out_h, out_w))  # decimated: locate blocks
        to_lonlat = Transformer.from_crs(ds.crs, "EPSG:4326", always_xy=True)
        fl_geom = florida_gdf_5070.geometry.union_all()

        for stratum, values in strata.items():
            blocks = np.argwhere(np.isin(arr, values))  # decimated (row, col) of blocks with the class
            if len(blocks) == 0:
                continue
            take = min(len(blocks), int(n_for(stratum) * oversample) + 20)
            picks = blocks[rng.choice(len(blocks), size=take, replace=False)]
            kept = 0
            for br, bc in picks:
                if kept >= n_for(stratum):
                    break
                # full-res block window covering this decimated cell; find the exact class pixel
                acol = int(round(col0 + bc * step_x))
                arow = int(round(row0 + br * step_y))
                bw, bh = int(np.ceil(step_x)) + 1, int(np.ceil(step_y)) + 1
                block = ds.read(1, window=Window(acol, arow, bw, bh))
                local = np.argwhere(np.isin(block, values))
                if len(local) == 0:
                    continue
                lr, lc = local[rng.integers(len(local))]
                x, y = transform_xy(ds.transform, arow + lr, acol + lc)  # pixel center in raster CRS
                if not fl_geom.contains(gpd.points_from_xy([x], [y])[0]):
                    continue
                lon, lat = to_lonlat.transform(x, y)
                rows.append({"lon": float(lon), "lat": float(lat), "stratum": stratum})
                kept += 1
    return pd.DataFrame(rows)


def sample_features(
    lonlats: list[tuple[float, float]],
    event_year: int,
    pre_year: int,
    florida,
    hist_start: int = HIST_START_YEAR,
    chunk_size: int = 300,
) -> pd.DataFrame:
    """Sample the full engineered feature stack at each point (issues GEE calls).

    Columns: ``pid, lon, lat``; event embedding ``A00..A63``; pre embedding ``P00..P63``;
    ``lc_event, lc_pre, lu_event, change_event, evt2016``; and LCMS history
    ``lcms_tree_removal_count`` (# Tree-Removal years hist_start..event) and ``lcms_ever_trees``.
    """
    import ee

    pre_bands = [f"P{i:02d}" for i in range(64)]
    event_img = annual_embedding(event_year, florida)
    pre_img = annual_embedding(pre_year, florida).rename(pre_bands)
    lc_event = lcms_image(event_year, "Land_Cover", florida).rename("lc_event")
    lc_pre = lcms_image(pre_year, "Land_Cover", florida).rename("lc_pre")
    lu_event = lcms_image(event_year, "Land_Use", florida).rename("lu_event")
    change_event = lcms_image(event_year, "Change", florida).rename("change_event")
    evt2016 = ee.Image(EVT_2016_ASSET).select("EVT").rename("evt2016")

    years = list(range(hist_start, event_year + 1))
    removal = ee.ImageCollection(
        [lcms_image(y, "Change", florida).eq(LCMS_CHANGE_TREE_REMOVAL) for y in years]
    ).sum().rename("lcms_tree_removal_count")
    ever_trees = ee.ImageCollection(
        [lcms_image(y, "Land_Cover", florida).eq(LCMS_LAND_COVER_TREES) for y in years]
    ).max().rename("lcms_ever_trees")

    stack = event_img.addBands(
        [pre_img, lc_event, lc_pre, lu_event, change_event, evt2016, removal, ever_trees]
    )

    rows: list[dict] = []
    for start in range(0, len(lonlats), chunk_size):
        chunk = lonlats[start : start + chunk_size]
        fc = _feature_collection(chunk, id_start=start)
        sampled = stack.sampleRegions(
            collection=fc, scale=EMBEDDING_SCALE_M, geometries=True, tileScale=4
        ).getInfo()
        for feature in sampled["features"]:
            props = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"]
            props["lon"], props["lat"] = lon, lat
            rows.append(props)
    return pd.DataFrame(rows).sort_values("pid").reset_index(drop=True)


def derive_feature_label(
    df: pd.DataFrame,
    evt2016_names: dict[int, str],
    evt2022_lookup: dict[int, dict],
) -> pd.DataFrame:
    """Attach EVT attributes, the embedding delta, role, and the anchor label ``y``.

    Roles: ``positive_forest`` (LCMS clearcut), ``negative_grassland`` (stable non-forest),
    ``apply_confused`` (one of the three confused EVT classes; the inference target), or
    ``other``. ``y`` = 1 for positive_forest, 0 for negative_grassland, NaN otherwise.
    """
    import numpy as np

    out = df.copy()
    event_bands = list(EMBEDDING_BANDS)
    pre_bands = [f"P{i:02d}" for i in range(64)]
    out["emb_delta_l2"] = np.linalg.norm(
        out[event_bands].to_numpy() - out[pre_bands].to_numpy(), axis=1
    )

    out["evt2016_name"] = out["evt2016"].map(lambda v: evt2016_names.get(int(v)) if pd.notna(v) else None)
    out["is_forest_2016"] = out["evt2016_name"].map(evt_name_is_forest)

    def _field(value, key):
        if pd.isna(value):
            return None
        rec = evt2022_lookup.get(int(value))
        return rec[key] if rec else None

    out["evt2022_name"] = out["evt2022"].map(lambda v: _field(v, "name"))
    out["evt2022_lifeform"] = out["evt2022"].map(lambda v: _field(v, "lifeform"))
    out["evt2022_phys"] = out["evt2022"].map(lambda v: _field(v, "physiognomy"))

    out["is_clearcut"] = (out["lc_pre"] == LCMS_LAND_COVER_TREES) & (
        out["change_event"] == LCMS_CHANGE_TREE_REMOVAL
    )
    out["is_confused"] = out["evt2022"].isin(CONFUSED_EVT_VALUES)
    out["confused_name"] = out["evt2022"].map(lambda v: CONFUSED_EVT_SHORT.get(int(v)) if pd.notna(v) else None)
    out["evt_nonforest_universe"] = out["evt2022_lifeform"].isin(AG_HERB_SHRUB_LIFEFORMS)
    out["evt_change_strict"] = out.apply(
        lambda r: evt_change_clearcut(bool(r["is_forest_2016"]), r["evt2022"]), axis=1
    )
    out["evt_change_broad"] = out.apply(
        lambda r: evt_change_clearcut_broad(bool(r["is_forest_2016"]), r["evt2022"], evt2022_lookup),
        axis=1,
    )

    stable_nonforest = (
        out["evt_nonforest_universe"]
        & (~out["is_clearcut"])
        & (~out["is_forest_2016"])
        & (out["lcms_ever_trees"] == 0)
    )

    def _role(row):
        if row["is_clearcut"]:
            return "positive_forest"
        if row["is_confused"]:
            return "apply_confused"
        if stable_nonforest.loc[row.name]:
            return "negative_grassland"
        return "other"

    out["role"] = out.apply(_role, axis=1)
    out["y"] = np.where(
        out["role"] == "positive_forest", 1.0,
        np.where(out["role"] == "negative_grassland", 0.0, np.nan),
    )
    return out


def feature_dictionary() -> pd.DataFrame:
    """Column -> {family, lcms_derived, dtype, note} for leakage-aware model building."""
    rows = []
    for b in EMBEDDING_BANDS:
        rows.append({"column": b, "family": "embedding_event", "lcms_derived": False,
                     "dtype": "float", "note": "AlphaEarth event-year band"})
    for i in range(64):
        rows.append({"column": f"P{i:02d}", "family": "embedding_pre", "lcms_derived": False,
                     "dtype": "float", "note": "AlphaEarth pre-year band"})
    rows += [
        {"column": "emb_delta_l2", "family": "embedding_delta", "lcms_derived": False,
         "dtype": "float", "note": "L2 distance event vs pre embedding (disturbance magnitude)"},
        {"column": "is_forest_2016", "family": "evt", "lcms_derived": False,
         "dtype": "bool", "note": "EVT 2016 name denotes forest/woodland"},
        {"column": "evt_change_strict", "family": "evt", "lcms_derived": False,
         "dtype": "bool", "note": "forest 2016 -> one of the three confused classes"},
        {"column": "evt_change_broad", "family": "evt", "lcms_derived": False,
         "dtype": "bool", "note": "forest 2016 -> any ag/grass/shrub lifeform 2022"},
        {"column": "lc_event", "family": "lcms", "lcms_derived": True,
         "dtype": "int", "note": "LCMS Land_Cover at event year"},
        {"column": "lc_pre", "family": "lcms", "lcms_derived": True,
         "dtype": "int", "note": "LCMS Land_Cover at pre year"},
        {"column": "lu_event", "family": "lcms", "lcms_derived": True,
         "dtype": "int", "note": "LCMS Land_Use at event year"},
        {"column": "change_event", "family": "lcms", "lcms_derived": True,
         "dtype": "int", "note": "LCMS Change at event year"},
        {"column": "lcms_tree_removal_count", "family": "lcms", "lcms_derived": True,
         "dtype": "int", "note": "# Tree-Removal years in history window"},
        {"column": "lcms_ever_trees", "family": "lcms", "lcms_derived": True,
         "dtype": "int", "note": "ever LCMS Trees in history window"},
    ]
    return pd.DataFrame(rows)


def clean_feature_columns() -> list[str]:
    """Predictor columns with no leakage against an LCMS-derived label (embeddings + EVT)."""
    fd = feature_dictionary()
    return fd.loc[~fd["lcms_derived"], "column"].tolist()


def build_feature_table(
    florida,
    florida_gdf_5070,
    repo_root: Path,
    event_year: int = DEFAULT_EVENT_YEAR,
    pre_year: int = DEFAULT_PRE_YEAR,
    n_per_confused: int = 300,
    n_agriculture: int = 300,
    n_grass_shrub_other: int = 300,
    n_clearcut: int = 500,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the labeled feature table + its data dictionary (issues GEE + raster calls)."""
    evt2022_lookup = load_evt2022_lookup(evt2022_csv_path(repo_root))
    ag_values = [v for v in evt_values_by_physiognomy(evt2022_lookup, ("Agricultural",))
                 if v not in CONFUSED_EVT_VALUES]
    grass_shrub_other = [v for v in evt_values_by_lifeform(evt2022_lookup, ("Herb", "Shrub"))
                         if v not in CONFUSED_EVT_VALUES]
    strata = {
        "pasture_hay": [7997],
        "ruderal_grass": [9823],
        "floodplain_shrub": [9585],
        "agriculture_rowcrop": ag_values,
        "grass_shrub_other": grass_shrub_other,
    }
    n_map = {"pasture_hay": n_per_confused, "ruderal_grass": n_per_confused,
             "floodplain_shrub": n_per_confused, "agriculture_rowcrop": n_agriculture,
             "grass_shrub_other": n_grass_shrub_other}

    evt_pts = stratified_evt_points(strata, n_map, florida_gdf_5070,
                                    evt2022_tif_path(repo_root), seed=seed)
    cc_pts = clearcut_sample_points(pre_year, event_year, n_clearcut, florida, seed=seed)
    cc_df = pd.DataFrame({"lon": [p[0] for p in cc_pts], "lat": [p[1] for p in cc_pts],
                          "stratum": "lcms_clearcut"})
    pool = pd.concat([evt_pts, cc_df], ignore_index=True)

    feats = sample_features(list(zip(pool["lon"], pool["lat"])), event_year, pre_year, florida)
    # attach sampling stratum by nearest pid order (sample_features preserves point order)
    feats = feats.merge(pool.reset_index().rename(columns={"index": "pid"})[["pid", "stratum"]],
                        on="pid", how="left")
    feats["evt2022"] = sample_local_evt2022(list(zip(feats["lon"], feats["lat"])),
                                            evt2022_tif_path(repo_root))
    evt2016_names = load_evt2016_name_lookup()
    feats = derive_feature_label(feats, evt2016_names, evt2022_lookup)
    feats["pre_year"], feats["event_year"] = pre_year, event_year
    return feats, feature_dictionary()


# ======================================================================================
# Embedding-similarity AOI finder: pick reference clearcuts, find similar land in an AOI
# ======================================================================================

SIMILARITY_AGG = ("max", "mean")


def normalize_reference_points(points) -> list[tuple[float, float]]:
    """Coerce assorted (lon, lat) inputs into a clean list of float tuples.

    Accepts lists/tuples of pairs or dicts with lon/lat (or lng/latitude) keys — handy for
    points pulled off an ipyleaflet/geemap draw control. Raises on malformed input.
    """
    out: list[tuple[float, float]] = []
    for p in points:
        if isinstance(p, dict):
            lon = p.get("lon", p.get("lng", p.get("longitude")))
            lat = p.get("lat", p.get("latitude"))
        else:
            lon, lat = p[0], p[1]
        if lon is None or lat is None:
            raise ValueError(f"Could not read lon/lat from reference point: {p!r}")
        out.append((float(lon), float(lat)))
    return out


def vector_scale_for_area_km2(area_km2: float) -> int:
    """Adaptive vectorization scale (m): finer for small AOIs, coarser for large ones."""
    if area_km2 <= 8000:      # ~ a Florida county or two
        return 20
    if area_km2 <= 40000:
        return 40
    return 90                 # ~ all of Florida (~170,000 km2)


def constant_vector_image(vector, bands: tuple[str, ...] = EMBEDDING_BANDS):
    import ee

    return ee.Image.constant(ee.Dictionary(vector).values(list(bands))).rename(list(bands))


def point_embedding_vector(image, lon: float, lat: float, scale: int = EMBEDDING_SCALE_M):
    import ee

    sample = image.sample(region=ee.Geometry.Point([lon, lat]), scale=scale, numPixels=1).first()
    return sample.toDictionary(list(EMBEDDING_BANDS))


def cosine_similarity_image(image, vector, name: str = "similarity"):
    import ee

    v = constant_vector_image(vector)
    numerator = image.multiply(v).reduce(ee.Reducer.sum())
    image_norm = image.pow(2).reduce(ee.Reducer.sum()).sqrt()
    vector_norm = v.pow(2).reduce(ee.Reducer.sum()).sqrt()
    return numerator.divide(image_norm.multiply(vector_norm)).rename(name)


def reference_vectors(year: int, points: list[tuple[float, float]], region):
    """Sample each reference point's embedding vector at ``year`` (over ``region``)."""
    image = annual_embedding(year, region)
    return [point_embedding_vector(image, lon, lat) for lon, lat in points]


def similarity_image(year: int, aoi, ref_vectors: list, agg: str = "max"):
    """Per-pixel similarity to the reference set within the AOI.

    ``max`` = similarity to the closest reference (find land like ANY exemplar);
    ``mean`` = average similarity to all references.
    """
    import ee

    if agg not in SIMILARITY_AGG:
        raise ValueError(f"agg must be one of {SIMILARITY_AGG}, got {agg!r}")
    image = annual_embedding(year, aoi)
    sims = ee.ImageCollection([cosine_similarity_image(image, v) for v in ref_vectors])
    combined = sims.max() if agg == "max" else sims.mean()
    return combined.rename("similarity").clip(aoi)


def counties_aoi(names: list[str] | None, statefp: str = "12"):
    """Dissolved geometry for the named counties (GEE TIGER/2018), default all of state."""
    import ee

    fc = ee.FeatureCollection("TIGER/2018/Counties").filter(ee.Filter.eq("STATEFP", statefp))
    if names:
        fc = fc.filter(ee.Filter.inList("NAME", ee.List(list(names))))
    return fc.geometry().dissolve(maxError=100)


def vectorize_similarity(sim_image, aoi, threshold: float, scale: int, min_area_ha: float = 1.0):
    """Polygonize the ``similarity >= threshold`` mask within the AOI.

    Each polygon gets an ``area_ha`` property; polygons smaller than ``min_area_ha`` are
    dropped server-side to cut salt-and-pepper speckle (a 20 m pixel is only 0.04 ha, so a
    similarity mask vectorizes into tens of thousands of specks without this filter).
    """
    import ee

    mask = sim_image.gte(threshold).selfMask().rename("similar")
    vectors = mask.reduceToVectors(
        geometry=aoi,
        scale=scale,
        geometryType="polygon",
        eightConnected=False,
        labelProperty="similar",
        maxPixels=1e10,
        bestEffort=True,
    )
    vectors = vectors.map(
        lambda f: f.set("area_ha", f.geometry().area(maxError=scale / 2.0).divide(1e4))
    )
    if min_area_ha and min_area_ha > 0:
        vectors = vectors.filter(ee.Filter.gte("area_ha", min_area_ha))
    return vectors


def fc_to_gdf(fc, max_features: int = 5000):
    """Pull an ee.FeatureCollection to a GeoDataFrame (EPSG:4326), capped with a warning."""
    import ee
    import geopandas as gpd

    n = fc.size().getInfo()
    if n > max_features:
        print(f"WARNING: {n} features exceeds max_features={max_features}; pulling first "
              f"{max_features}. Raise the threshold/scale or export to Drive for the full result.")
        fc = ee.FeatureCollection(fc.toList(max_features))
    data = fc.getInfo()
    if not data["features"]:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")


def reference_points_gdf(points: list[tuple[float, float]]):
    import geopandas as gpd

    pts = normalize_reference_points(points)
    return gpd.GeoDataFrame(
        {"ref_id": list(range(len(pts)))},
        geometry=gpd.points_from_xy([p[0] for p in pts], [p[1] for p in pts]),
        crs="EPSG:4326",
    )
