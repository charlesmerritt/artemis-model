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
