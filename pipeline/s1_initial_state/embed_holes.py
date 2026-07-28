"""Earth Engine side of the hole-rectification workflow: sample AlphaEarth, export rasters.

Two entry points:

``sample``  Attach AlphaEarth annual embeddings to the labelled points written by
            ``sample_hole_points.py``.
``apply``   Take the linear model fitted by ``classify_holes.py`` and evaluate it
            server-side over the whole AOI, downloading a 30 m probability +
            similarity raster that ``finalize_add_back.py`` then masks to the
            TreeMap holes to produce the final add-back decision.

Leakage rule (enforced, not advisory)
-------------------------------------
The positive anchors are *defined* by LANDFIRE 2024 calling the pixel forest. An
embedding from 2023 or 2024 therefore lets the classifier read the label off the
feature and score near-perfectly while learning nothing that transfers to S3 —
which is defined as **not** tree in 2024. ``MAX_FEATURE_YEAR`` caps feature years
at 2022, the TreeMap vintage being corrected, and ``check_feature_years`` raises
if that is violated.

The same trap is documented in ``notes/clearcut-vs-agriculture-embeddings.md``,
where pre-year embeddings drove AUC to 1.000 "largely by construction".

Auth: AlphaEarth needs a live Earth Engine token. If ``ee.Initialize()`` fails
with ``invalid_grant`` the stored refresh token has expired — re-run
``earthengine authenticate``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data/interim/treemap_holes"

EMBEDDING_COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
EMBEDDING_BANDS = tuple(f"A{i:02d}" for i in range(64))
EMBEDDING_SCALE_M = 10

MAX_FEATURE_YEAR = 2022  # TreeMap vintage; see leakage rule above
DEFAULT_YEARS = (2018, 2020, 2022)
CHUNK = 500  # points per getInfo call; keeps requests under EE payload limits

# AOI bbox in EPSG:5070, matching data/interim/treemap_holes/treemap_hole_strata.tif
AOI_BOUNDS_5070 = (1210125.0, 831795.0, 1342665.0, 937605.0)
OUTPUT_SCALE_M = 30

# Fixed-point scale for the exported score bands. This must be fine enough that
# quantisation cannot flip a decision: ``finalize_add_back`` compares decoded
# values against the model's full-precision thresholds, so a coarse scale moves
# pixels across the boundary in both directions. At 1/100 a true similarity of
# 0.9046 encoded to 0.90 was wrongly rejected against a 0.90457 threshold, and a
# true probability of 0.499 encoded to 0.50 was wrongly accepted at 0.5. At
# 1/10000 the worst-case shift is 5e-5. Bands stay inside uint16: probability
# reaches 10000 and (cosine + 1) reaches 20000, against a 65535 ceiling.
SCORE_SCALE = 10_000


def check_feature_years(years) -> None:
    bad = [y for y in years if y > MAX_FEATURE_YEAR]
    if bad:
        raise ValueError(
            f"feature years {bad} exceed MAX_FEATURE_YEAR={MAX_FEATURE_YEAR}. "
            "Post-2022 embeddings leak the LF2024-derived anchor label."
        )


def init_ee():
    import ee

    try:
        ee.Initialize()
    except Exception as exc:  # noqa: BLE001 - surface the actionable cause
        raise RuntimeError(
            f"Earth Engine init failed ({exc}). If this is 'invalid_grant', the stored "
            "refresh token expired: run `earthengine authenticate`."
        ) from exc
    return ee


def aoi_region(ee):
    """The 5-county AOI bbox as an EE geometry, in the raster's own projection."""
    return ee.Geometry.Rectangle(list(AOI_BOUNDS_5070), proj="EPSG:5070", geodesic=False)


def annual_embedding(ee, year: int):
    """Mosaic of the AlphaEarth annual embedding for `year`, restricted to the AOI.

    ``filterBounds`` matters: without it the mosaic spans CONUS and every request
    pays for tiles nowhere near the AOI. Matches the convention in
    ``notebooks/clearcut_ag_common.annual_embedding``.
    """
    start = ee.Date.fromYMD(year, 1, 1)
    region = aoi_region(ee)
    return (
        ee.ImageCollection(EMBEDDING_COLLECTION)
        .filterDate(start, start.advance(1, "year"))
        .filterBounds(region)
        .mosaic()
    )


def sample_year(ee, points: pd.DataFrame, year: int) -> pd.DataFrame:
    """Return a (len(points), 64) frame of embedding bands for `year`, index-aligned."""
    image = annual_embedding(ee, year)
    out = []
    for start in range(0, len(points), CHUNK):
        block = points.iloc[start:start + CHUNK]
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([lon, lat]), {"idx": int(i)})
            for i, lon, lat in zip(block.index, block.lon, block.lat)
        ])
        sampled = image.sampleRegions(
            collection=fc, scale=EMBEDDING_SCALE_M, geometries=False, tileScale=4
        )
        rows = sampled.getInfo()["features"]
        out.extend({"idx": r["properties"]["idx"],
                    **{b: r["properties"].get(b) for b in EMBEDDING_BANDS}} for r in rows)
        print(f"  {year}: {min(start + CHUNK, len(points)):,}/{len(points):,}")

    frame = pd.DataFrame(out).set_index("idx").sort_index()
    return frame.rename(columns={b: f"{b}_{year}" for b in EMBEDDING_BANDS})


def run_sample(years, points_csv: Path, out_csv: Path) -> pd.DataFrame:
    check_feature_years(years)
    ee = init_ee()
    points = pd.read_csv(points_csv)
    print(f"sampling {len(points):,} points x {len(years)} years")

    frames = [sample_year(ee, points, y) for y in years]
    table = points.join(pd.concat(frames, axis=1))

    band_cols = [c for c in table.columns if c.startswith("A")]
    missing = table[band_cols].isna().any(axis=1).sum()
    print(f"points with any missing band: {missing:,} (dropped)")
    table = table.dropna(subset=band_cols)

    table.to_csv(out_csv, index=False)
    print(f"wrote {out_csv} ({len(table):,} rows, {len(band_cols)} band columns)")
    return table


def probability_image(ee, model: dict):
    """Rebuild the fitted logistic regression as an Earth Engine image.

    AlphaEarth bands are unit-norm floats, so a linear model is exactly a band
    dot-product plus an intercept; the sigmoid is applied server-side. This
    reproduces the sklearn model bit-for-bit rather than re-fitting in EE.
    """
    year = model["feature_year"]
    image = annual_embedding(ee, year).select(list(model["bands"]))
    weights = ee.Image.constant(list(model["coef"]))
    logit = image.multiply(weights).reduce(ee.Reducer.sum()).add(ee.Image.constant(model["intercept"]))
    return logit.multiply(-1).exp().add(1).pow(-1).rename("prob")


def similarity_image(ee, model: dict):
    """Max cosine similarity to any anchor exemplar (unit-norm bands => dot product).

    Mirrors ``classify_holes.stage_a_similarity``: one dot product per exemplar,
    then a per-pixel max, so a fresh cut can match the young exemplar without
    having to resemble an established stand.
    """
    year = model["feature_year"]
    image = annual_embedding(ee, year).select(list(model["bands"]))
    bands = []
    for i, exemplar in enumerate(model["anchor_exemplars"]):
        vec = np.asarray(exemplar, dtype=float)
        vec = vec / np.linalg.norm(vec)
        bands.append(
            image.multiply(ee.Image.constant(vec.tolist()))
            .reduce(ee.Reducer.sum())
            .rename(f"sim_{i}")
        )
    return ee.Image.cat(bands).reduce(ee.Reducer.max()).rename("similarity")


def row_tiles(n_tiles: int) -> list[tuple[float, float, float, float]]:
    """Split the AOI into horizontal strips on exact 30 m pixel boundaries.

    A whole-AOI request is ~62 MB against Earth Engine's 50 MB download ceiling,
    so it must be tiled. Splitting on exact pixel-row multiples keeps every tile
    on the same grid as ``treemap_hole_strata.tif``, so the reassembled raster
    aligns without resampling.
    """
    left, bottom, right, top = AOI_BOUNDS_5070
    total_rows = round((top - bottom) / OUTPUT_SCALE_M)
    edges = [round(total_rows * i / n_tiles) for i in range(n_tiles + 1)]
    return [
        (left, top - edges[i + 1] * OUTPUT_SCALE_M, right, top - edges[i] * OUTPUT_SCALE_M)
        for i in range(n_tiles)
    ]


def run_apply(model_json: Path, out_tif: Path, n_tiles: int) -> None:
    import urllib.request

    import rasterio

    ee = init_ee()
    model = json.loads(model_json.read_text())
    check_feature_years([model["feature_year"]])

    stacked = (
        probability_image(ee, model).multiply(SCORE_SCALE).round()
        .addBands(similarity_image(ee, model).add(1).multiply(SCORE_SCALE).round())
        .toUint16()
    )

    # Earth Engine rounds each requested region outward, so tiles come back a row
    # or column larger than asked and overlap their neighbours. Concatenating them
    # would drift the grid; instead every tile is placed by its own geotransform
    # into a canvas sized from AOI_BOUNDS_5070. Overlaps rewrite identical values.
    left, bottom, right, top = AOI_BOUNDS_5070
    height = round((top - bottom) / OUTPUT_SCALE_M)
    width = round((right - left) / OUTPUT_SCALE_M)
    canvas, profile = None, None

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    for i, bounds in enumerate(row_tiles(n_tiles), start=1):
        region = ee.Geometry.Rectangle(list(bounds), proj="EPSG:5070", geodesic=False)
        url = stacked.getDownloadURL({
            "region": region,
            "scale": OUTPUT_SCALE_M,
            "crs": "EPSG:5070",
            "format": "GEO_TIFF",
        })
        tmp = out_tif.parent / f".tile_{i}.tif"
        urllib.request.urlretrieve(url, tmp)
        with rasterio.open(tmp) as src:
            data = src.read()
            tf = src.transform
            profile = profile or src.profile
        tmp.unlink()

        if canvas is None:
            canvas = np.zeros((data.shape[0], height, width), dtype=data.dtype)
        row0 = round((top - tf.f) / OUTPUT_SCALE_M)
        col0 = round((tf.c - left) / OUTPUT_SCALE_M)
        # Clip the paste to the canvas; the outward rounding can overhang an edge.
        src_r0, dst_r0 = (0, row0) if row0 >= 0 else (-row0, 0)
        src_c0, dst_c0 = (0, col0) if col0 >= 0 else (-col0, 0)
        h = min(data.shape[1] - src_r0, height - dst_r0)
        w = min(data.shape[2] - src_c0, width - dst_c0)
        canvas[:, dst_r0:dst_r0 + h, dst_c0:dst_c0 + w] = data[:, src_r0:src_r0 + h, src_c0:src_c0 + w]
        print(f"  tile {i}/{n_tiles} {data.shape} -> canvas rows {dst_r0}:{dst_r0 + h}")

    profile.update(height=height, width=width, count=canvas.shape[0], compress="lzw",
                   transform=rasterio.transform.from_origin(left, top, OUTPUT_SCALE_M, OUTPUT_SCALE_M))
    with rasterio.open(out_tif, "w", **profile) as dst:
        dst.write(canvas)
    print(f"wrote {out_tif} {canvas.shape} "
          f"(band 1 = prob*{SCORE_SCALE}, band 2 = (cosine+1)*{SCORE_SCALE})")


def run_snippet(model_json: Path, out_js: Path) -> None:
    """Emit a Code Editor script so the two surfaces can be panned over imagery.

    The local PNGs (fig10) show the surfaces statically. This puts the *same*
    fitted model on code.earthengine.google.com, where it can be dragged around
    on top of high-resolution basemap imagery — which is how you actually judge
    whether an accepted patch is a clearcut or a pasture.
    """
    model = json.loads(model_json.read_text())
    bands = json.dumps(list(model["bands"]))
    js = f"""// TreeMap hole rectification — Stage A / Stage B surfaces.
// Generated by pipeline/s1_initial_state/embed_holes.py snippet
// Paste into https://code.earthengine.google.com and press Run.
// Toggle layers in the top-right; switch the basemap to Satellite to judge patches.

var YEAR = {model["feature_year"]};
var BANDS = {bands};
var COEF = {json.dumps([round(c, 6) for c in model["coef"]])};
var INTERCEPT = {round(model["intercept"], 6)};
var EXEMPLARS = {json.dumps([[round(v, 6) for v in e] for e in model["anchor_exemplars"]])};
var SIM_THRESHOLD = {round(model["similarity_threshold"], 4)};
var DEC_THRESHOLD = {round(model["decision_threshold"], 4)};

var aoi = ee.Geometry.Rectangle({json.dumps(list(AOI_BOUNDS_5070))}, 'EPSG:5070', false);
var emb = ee.ImageCollection('{EMBEDDING_COLLECTION}')
  .filterDate(YEAR + '-01-01', (YEAR + 1) + '-01-01')
  .filterBounds(aoi).mosaic().select(BANDS);

// Stage A: max cosine similarity to any clearcut exemplar (bands are unit-norm).
var sims = EXEMPLARS.map(function (vec) {{
  return emb.multiply(ee.Image.constant(vec)).reduce(ee.Reducer.sum());
}});
var similarity = ee.ImageCollection(sims).max().rename('similarity');

// Stage B: the fitted logistic regression, scaler folded into one dot product.
var logit = emb.multiply(ee.Image.constant(COEF)).reduce(ee.Reducer.sum()).add(INTERCEPT);
var prob = logit.multiply(-1).exp().add(1).pow(-1).rename('prob');

var accepted = similarity.gte(SIM_THRESHOLD).and(prob.gte(DEC_THRESHOLD)).selfMask();

Map.centerObject(aoi, 10);
Map.setOptions('SATELLITE');
Map.addLayer(similarity.clip(aoi), {{min: 0.5, max: 1.0, palette: ['000004','b63679','fcfdbf']}},
             'Stage A: similarity', false);
Map.addLayer(prob.clip(aoi), {{min: 0, max: 1, palette: ['440154','21918c','fde725']}},
             'Stage B: probability', false);
Map.addLayer(accepted.clip(aoi), {{palette: ['ff0000']}}, 'accepted (both stages)');

// Click anywhere to read both scores at that pixel.
Map.onClick(function (coords) {{
  var pt = ee.Geometry.Point([coords.lon, coords.lat]);
  var vals = similarity.addBands(prob).reduceRegion({{
    reducer: ee.Reducer.first(), geometry: pt, scale: 10}});
  vals.evaluate(function (v) {{
    print('similarity ' + (v.similarity || 0).toFixed(3) +
          '  (threshold ' + SIM_THRESHOLD + ')',
          'probability ' + (v.prob || 0).toFixed(3) +
          '  (threshold ' + DEC_THRESHOLD + ')');
  }});
}});
"""
    out_js.parent.mkdir(parents=True, exist_ok=True)
    out_js.write_text(js)
    print(f"wrote {out_js} — paste into https://code.earthengine.google.com")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["sample", "apply", "snippet"])
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    parser.add_argument("--points-csv", type=Path, default=DATA_DIR / "hole_sample_points.csv")
    parser.add_argument("--out-csv", type=Path, default=DATA_DIR / "hole_embeddings.csv")
    parser.add_argument("--model-json", type=Path, default=DATA_DIR / "hole_model.json")
    parser.add_argument("--out-tif", type=Path, default=DATA_DIR / "hole_prob_similarity.tif")
    parser.add_argument("--out-js", type=Path,
                        default=REPO / "docs/treemap_holes/inspect_mask_gee.js")
    parser.add_argument("--tiles", type=int, default=3,
                        help="horizontal strips to split the download into (EE caps a request at 50 MB)")
    args = parser.parse_args()

    if args.stage == "sample":
        run_sample(args.years, args.points_csv, args.out_csv)
    elif args.stage == "snippet":
        run_snippet(args.model_json, args.out_js)
    else:
        run_apply(args.model_json, args.out_tif, args.tiles)


if __name__ == "__main__":
    main()
