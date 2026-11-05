"""Land-use prototype gate: reject add-back on persistent non-forest land use, from AlphaEarth.

TreeMap maps forest *cover*; FIA counts forest *land use*, which keeps a harvested stand
that will regrow. The add-back methods find disturbance, and their failures are disturbances
that never went back to forest: quarries, crop circles, solar, hay. This gate asks, per
pixel and per year, which land use the AlphaEarth embedding looks like, and rejects a pixel
that has looked like a non-forest land use for the last ``years`` years running.

1. **Labels**, automatic and per pixel (:func:`label_pixels`), from sources independent of the
   add-back methods: TreeMap, LANDFIRE EVT 2016/2022/2024, the bookend strata, the dated
   Obata/Hansen cut year, the USDA Cropland Data Layer 2019-2023 and the USGS solar
   database. Regenerating forest (cut, then tree again by 2024) is its own class, so a fresh
   cut is compared against a cut, not only against a mature stand.
2. **Prototypes**: k-means centroids of the unit-normalized embeddings of each class, taken
   only in the years its label holds (:func:`label_years`).
3. **Nearest class** per year 2017-2022 (max cosine to any prototype), and the run of
   trailing years whose nearest class is non-forest (:func:`trailing_nonforest_years`).
4. **The gate**: ``trailing >= years``, with ``years`` fitted for balanced accuracy on
   out-of-fold predictions (GroupKFold over 10 km blocks) of forest vs non-forest labels.

Embeddings stop at 2022: the labels read LANDFIRE 2024 and CDL 2023 (``embed_holes``
leakage rule). Writes ``landuse_gate.tif``: band 1 the trailing non-forest years (0-6),
band 2 the nearest :class:`LandUse` in 2022.

    uv run python -m pipeline.s1_initial_state.landuse_prototype_gate <run>/aoi_5county --inputs-root <clips> --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from pipeline.s1_initial_state.alphaearth_hole_detector import download_on_grid, grid_region, pixel_points
from pipeline.s1_initial_state.embed_holes import CHUNK, EMBEDDING_BANDS, EMBEDDING_SCALE_M, check_feature_years

EMBED_YEARS = (2017, 2018, 2019, 2020, 2021, 2022)   # AlphaEarth starts 2017; capped at the TreeMap vintage
CDL_YEARS = (2019, 2020, 2021, 2022, 2023)
CDL_MIN_YEARS = 3
# USDA NASS CDL ``cropland`` codes: annual and tree crops, double crops. Hay (36, 37) and
# grassland/pasture (176) are counted separately.
CDL_CROP = (*range(1, 36), *range(38, 62), *range(66, 78), *range(204, 255))
CDL_HAY_PASTURE = (36, 37, 176)
QUARRY = 7295
DEVELOPED = (7296, 7297, 7298, 7299)
PASTURE_HAY = 7997
VINTAGES = (2016, 2022, 2024)


class LandUseGroup(StrEnum):
    FOREST = "forest"
    NON_FOREST = "non_forest"


class LandUse(IntEnum):
    MATURE_FOREST = 1
    REGENERATING_FOREST = 2
    CROP = 3
    PASTURE_HAY = 4
    QUARRY = 5
    DEVELOPED = 6
    SOLAR = 7

    @property
    def group(self) -> LandUseGroup:
        forest = (LandUse.MATURE_FOREST, LandUse.REGENERATING_FOREST)
        return LandUseGroup.FOREST if self in forest else LandUseGroup.NON_FOREST


NON_FOREST_CODES = tuple(int(c) for c in LandUse if c.group is LandUseGroup.NON_FOREST)


# ── labels ──────────────────────────────────────────────────────────────────────────────


@dataclass
class LabelInputs:
    published: np.ndarray                 # bool: TreeMap 2022 published a plot
    hole: np.ndarray                      # bool: land TreeMap published no plot on
    tree: dict[int, np.ndarray]           # bool per LANDFIRE vintage: EVT lifeform Tree
    evt: dict[int, np.ndarray]            # EVT code per vintage
    strata: np.ndarray                    # bookend stratum S1-S5 over holes, 0 elsewhere
    cut_year: np.ndarray                  # latest dated Obata/Hansen cut, 0 undated
    cdl_crop_years: np.ndarray            # CDL 2019-2023 years in a crop class
    cdl_hay_years: np.ndarray             # CDL 2019-2023 years in hay or grassland/pasture
    solar: np.ndarray                     # bool: inside a USGS solar facility polygon


def label_pixels(x: LabelInputs) -> np.ndarray:
    """uint8 :class:`LandUse` code per pixel, 0 unlabeled. Earlier assignments are overwritten by later."""
    ever_tree = x.tree[2016] | x.tree[2022] | x.tree[2024]
    every = lambda codes: np.logical_and.reduce([np.isin(x.evt[y], codes) for y in VINTAGES])  # noqa: E731
    out = np.zeros(x.published.shape, dtype=np.uint8)
    out[x.published & x.tree[2016] & x.tree[2022] & x.tree[2024] & (x.cut_year == 0)] = LandUse.MATURE_FOREST
    out[x.hole & x.tree[2024] & ((x.strata == 1) | (x.cut_year > 0))] = LandUse.REGENERATING_FOREST
    out[every((PASTURE_HAY,)) & (x.cdl_hay_years >= CDL_MIN_YEARS)] = LandUse.PASTURE_HAY
    out[(x.cdl_crop_years >= CDL_MIN_YEARS) & ~ever_tree] = LandUse.CROP
    out[every(DEVELOPED)] = LandUse.DEVELOPED
    any_quarry = np.logical_or.reduce([x.evt[y] == QUARRY for y in VINTAGES])
    out[any_quarry & ~x.tree[2022] & ~x.tree[2024]] = LandUse.QUARRY
    out[x.solar] = LandUse.SOLAR
    return out


def label_years(land_use: LandUse, cut_year: int) -> tuple[int, ...]:
    """The embedding years a label holds in: crop from CDL's first year, regrowth from its cut."""
    if land_use is LandUse.CROP:
        return tuple(y for y in EMBED_YEARS if y >= CDL_YEARS[0])
    if land_use is LandUse.REGENERATING_FOREST and cut_year:
        return tuple(y for y in EMBED_YEARS if y >= cut_year)
    return EMBED_YEARS


# ── prototypes and the gate ─────────────────────────────────────────────────────────────


def _unit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, float)
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def fit_prototypes(x: np.ndarray, y: np.ndarray, k: int, seed: int) -> dict[LandUse, np.ndarray]:
    """Up to ``k`` unit-norm k-means centroids per class present in ``y``."""
    from sklearn.cluster import KMeans

    x = _unit(x)
    protos = {}
    for code in np.unique(y):
        rows = x[y == code]
        if len(rows) <= k:
            centres = rows
        else:
            centres = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(rows).cluster_centers_
        protos[LandUse(int(code))] = _unit(centres)
    return protos


def nearest_class(x: np.ndarray, prototypes: dict[LandUse, np.ndarray]) -> np.ndarray:
    """Per row, the :class:`LandUse` code of the prototype with the highest cosine."""
    x = _unit(x)
    classes = list(prototypes)
    best = np.stack([(x @ prototypes[c].T).max(axis=1) for c in classes], axis=1)
    return np.array([int(c) for c in classes])[best.argmax(axis=1)]


def trailing_nonforest_years(classes: np.ndarray) -> np.ndarray:
    """``classes`` is (pixels, years) oldest first; the run of non-forest years ending at the last."""
    nonforest = np.isin(classes, NON_FOREST_CODES)
    run = np.cumprod(nonforest[:, ::-1], axis=1)
    return run.sum(axis=1)


def fit_gate_years(trailing: np.ndarray, nonforest: np.ndarray) -> int:
    """The run length (1..len(EMBED_YEARS)) maximizing balanced accuracy; ties take the longer run."""
    best, best_ba = None, -1.0
    for n in range(1, len(EMBED_YEARS) + 1):
        gated = trailing >= n
        ba = (gated[nonforest].mean() + (~gated[~nonforest]).mean()) / 2
        if ba >= best_ba - 1e-12:
            best, best_ba = n, ba
    return best


@dataclass
class PrototypeGate:
    prototypes: dict[LandUse, np.ndarray]
    years: int
    metrics: dict = field(default_factory=dict)

    def gated(self, trailing: np.ndarray) -> np.ndarray:
        return np.asarray(trailing) >= self.years

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "years": self.years, "embed_years": list(EMBED_YEARS), "metrics": self.metrics,
            "prototypes": {c.name: p.tolist() for c, p in self.prototypes.items()}}, indent=2))

    @classmethod
    def load(cls, path: Path) -> PrototypeGate:
        d = json.loads(Path(path).read_text())
        return cls({LandUse[n]: np.asarray(p) for n, p in d["prototypes"].items()}, d["years"],
                   d.get("metrics", {}))


def fit_gate(table: pd.DataFrame, k: int, seed: int) -> PrototypeGate:
    """Prototypes from every row's label years; ``years`` and metrics from block-held-out points."""
    from sklearn.model_selection import GroupKFold

    def rows(t: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        xs, ys = [], []
        for year in EMBED_YEARS:
            keep = t.apply(lambda r: year in label_years(LandUse(r.land_use), r.cut_year), axis=1)
            xs.append(t.loc[keep, [f"{b}_{year}" for b in EMBEDDING_BANDS]].to_numpy(float))
            ys.append(t.loc[keep, "land_use"].to_numpy())
        return np.vstack(xs), np.concatenate(ys)

    def yearly_classes(t: pd.DataFrame, protos) -> np.ndarray:
        return np.stack([nearest_class(t[[f"{b}_{y}" for b in EMBEDDING_BANDS]].to_numpy(float), protos)
                         for y in EMBED_YEARS], axis=1)

    cv = GroupKFold(n_splits=min(5, table["block"].nunique()))
    held = []
    for train, test in cv.split(table, groups=table["block"]):
        protos = fit_prototypes(*rows(table.iloc[train]), k=k, seed=seed)
        classes = yearly_classes(table.iloc[test], protos)
        held.append(table.iloc[test][["land_use"]].assign(
            class_2022=classes[:, -1], trailing=trailing_nonforest_years(classes)))
    held = pd.concat(held)
    nonforest = np.isin(held["land_use"], NON_FOREST_CODES)
    years = fit_gate_years(held["trailing"].to_numpy(), nonforest)
    gated = held["trailing"].to_numpy() >= years
    by_class = {LandUse(c).name: {"n": int((held.land_use == c).sum()),
                                  "gated": float(gated[held.land_use == c].mean()),
                                  "class_2022_correct": float((held.class_2022[held.land_use == c] == c).mean())}
                for c in sorted(held.land_use.unique())}
    metrics = {"k": k, "n_points": len(table), "n_blocks": int(table["block"].nunique()),
               "forest_passed_block_cv": float((~gated[~nonforest]).mean()),
               "non_forest_gated_block_cv": float(gated[nonforest].mean()),
               "by_class_block_cv": by_class}
    return PrototypeGate(fit_prototypes(*rows(table), k=k, seed=seed), years, metrics)


# ── Earth Engine ────────────────────────────────────────────────────────────────────────


def cdl_counts_image(ee):
    """Two bands: CDL 2019-2023 years in a crop class, and in hay or grassland/pasture."""
    def count(codes):
        years = [ee.Image(f"USDA/NASS/CDL/{y}").select("cropland").remap(list(codes), [1] * len(codes), 0)
                 for y in CDL_YEARS]
        return ee.Image.cat(years).reduce(ee.Reducer.sum())
    return ee.Image.cat([count(CDL_CROP).rename("crop_years"),
                         count(CDL_HAY_PASTURE).rename("hay_years")]).toUint8()


def embedding_stack(ee, region):
    """The AlphaEarth bands of every ``EMBED_YEARS`` year, renamed ``A00_2017`` ..."""
    from pipeline.s1_initial_state.embed_holes import annual_embedding

    check_feature_years(EMBED_YEARS)
    return ee.Image.cat([annual_embedding(ee, y, region).select(list(EMBEDDING_BANDS),
                                                                [f"{b}_{y}" for b in EMBEDDING_BANDS])
                         for y in EMBED_YEARS])


def sample_stack(ee, table: pd.DataFrame) -> pd.DataFrame:
    region = ee.Geometry.Rectangle([table.lon.min() - 0.01, table.lat.min() - 0.01,
                                    table.lon.max() + 0.01, table.lat.max() + 0.01])
    image = embedding_stack(ee, region)
    names = [f"{b}_{y}" for y in EMBED_YEARS for b in EMBEDDING_BANDS]
    rows = []
    step = CHUNK // 5      # 384 bands a point
    for start in range(0, len(table), step):
        block = table.iloc[start:start + step]
        fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lon, lat]), {"idx": int(i)})
                                   for i, lon, lat in zip(block.index, block.lon, block.lat)])
        got = image.sampleRegions(collection=fc, scale=EMBEDDING_SCALE_M, geometries=False,
                                  tileScale=8).getInfo()["features"]
        rows += [{"idx": f["properties"]["idx"], **{n: f["properties"].get(n) for n in names}} for f in got]
        print(f"  embeddings {min(start + step, len(table)):,}/{len(table):,}")
    return table.join(pd.DataFrame(rows).set_index("idx"), how="inner").dropna(subset=names)


def gate_image(ee, gate: PrototypeGate, region):
    """Band 1 trailing non-forest years, band 2 the nearest class in 2022 (uint8)."""
    stack = embedding_stack(ee, region)
    classes = list(gate.prototypes)
    codes = [int(c) for c in classes]
    flags, nearest = [], None
    for year in EMBED_YEARS:
        img = stack.select([f"{b}_{year}" for b in EMBEDDING_BANDS])
        unit = img.divide(img.pow(2).reduce(ee.Reducer.sum()).sqrt())
        scores = []
        for c in classes:
            sims = [unit.multiply(ee.Image.constant(p.tolist())).reduce(ee.Reducer.sum())
                    for p in gate.prototypes[c]]
            scores.append(ee.Image.cat(sims).reduce(ee.Reducer.max()))
        index = ee.Image.cat(scores).toArray().arrayArgmax().arrayGet([0])
        nearest = index.remap(list(range(len(codes))), codes)
        flags.append(nearest.remap(list(NON_FOREST_CODES), [1] * len(NON_FOREST_CODES), 0))
    run, trailing = ee.Image.constant(1), ee.Image.constant(0)
    for flag in reversed(flags):
        run = run.multiply(flag)
        trailing = trailing.add(run)
    return ee.Image.cat([trailing.rename("trailing_nonforest_years"), nearest.rename("class_2022")]).toUint8()


# ── the run ─────────────────────────────────────────────────────────────────────────────


def build_labels(run_dir: Path, inputs_root: Path, cdl_tif: Path,
                 solar_shp: Path | None) -> tuple[np.ndarray, np.ndarray, object]:
    """The :class:`LandUse` label raster, the dated cut year and the transform, on the run's grid."""
    import geopandas as gpd
    from rasterio import features

    from pipeline.s1_initial_state.add_back_stand_age import cut_year
    from pipeline.s1_initial_state.county_improvement import Grid, Inputs, TreeMapProvenance as TP, read_on_grid

    with rasterio.open(run_dir / "treemap2022_provenance.tif") as src:
        prov, transform, crs = src.read(1), src.transform, src.crs
    grid = Grid(transform, prov.shape)
    inputs = Inputs(landfire=inputs_root / "landfire")
    evt, tree = {}, {}
    for y in VINTAGES:
        tif, csv = inputs.evt(y)
        legend = pd.read_csv(csv)
        evt[y] = read_on_grid(tif, grid, fill=-9999).astype(np.int32)
        tree[y] = np.isin(evt[y], legend.loc[legend.EVT_LF == "Tree", "VALUE"].to_numpy())
    with rasterio.open(run_dir / "bookend_strata.tif") as src:
        strata = src.read(1)
    obata = next((inputs_root / "obata").glob("lastDist-*.tif"))
    years, _ = cut_year(read_on_grid(obata, grid, fill=0), read_on_grid(inputs_root / "hansen/hansen_aoi.tif", grid, fill=0))
    cdl = read_on_grid(cdl_tif, grid, fill=0, indexes=[1, 2])
    solar = np.zeros(prov.shape, dtype=bool)
    if solar_shp is not None:
        sites = gpd.read_file(solar_shp).to_crs(crs)
        if len(sites):
            solar = features.rasterize(((g, 1) for g in sites.geometry), out_shape=prov.shape,
                                       transform=transform, fill=0, dtype="uint8").astype(bool)
    hole = np.isin(prov, (TP.UNMAPPED_LAND, TP.ADDED_BACK, TP.ADDED_BACK_YOUNG))
    x = LabelInputs(published=prov == TP.PUBLISHED, hole=hole, tree=tree, evt=evt,
                    strata=np.where(strata == 255, 0, strata), cut_year=years,
                    cdl_crop_years=cdl[0], cdl_hay_years=cdl[1], solar=solar)
    return label_pixels(x), years, transform


def sample_labels(labels: np.ndarray, cut_years: np.ndarray, transform, n_per_class: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    for c in LandUse:
        flat = np.flatnonzero(labels == c)
        if flat.size == 0:
            continue
        pick = np.sort(rng.choice(flat, min(n_per_class, flat.size), replace=False))
        rows, cols = np.unravel_index(pick, labels.shape)
        frames.append(pd.DataFrame({"land_use": int(c), "row": rows, "col": cols,
                                    "cut_year": cut_years[rows, cols].astype(int)}))
    table = pd.concat(frames, ignore_index=True)
    return pd.concat([table, pixel_points(table["row"], table["col"], transform)], axis=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--inputs-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--solar-shp", type=Path, default=None)
    parser.add_argument("--n-per-class", type=int, default=500)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261005)
    args = parser.parse_args()

    from pipeline.s1_initial_state.embed_holes import init_ee

    ee = init_ee()
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    like = args.run_dir / "treemap2022_provenance.tif"
    cdl_tif = out / "cdl_2019_2023_counts.tif"
    if not cdl_tif.exists():
        download_on_grid(ee, cdl_counts_image(ee), like, cdl_tif, "uint8",
                         "band 1 CDL 2019-2023 crop years, band 2 hay/pasture years")
    labels, cut_years, transform = build_labels(args.run_dir, args.inputs_root, cdl_tif, args.solar_shp)
    print({LandUse(c).name: round(int((labels == c).sum()) * 0.2224) for c in LandUse})
    sample_csv = out / "landuse_sample.csv"
    if sample_csv.exists():
        table = pd.read_csv(sample_csv)
    else:
        table = sample_stack(ee, sample_labels(labels, cut_years, transform, args.n_per_class, args.seed))
        table.to_csv(sample_csv, index=False)
    gate = fit_gate(table, args.k, args.seed)
    gate.save(out / "landuse_gate.json")
    print(json.dumps({"years": gate.years, **gate.metrics}, indent=2))
    download_on_grid(ee, gate_image(ee, gate, grid_region(ee, like)), like, out / "landuse_gate.tif", "uint8",
                     "band 1 trailing non-forest years 2017-2022, band 2 nearest LandUse 2022",
                     max_tile_pixels=400_000)


if __name__ == "__main__":
    main()
