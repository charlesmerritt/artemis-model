"""
Generate Google Earth Engine embeddings over an imagery extent and compare how
they cluster inside versus outside the area of interest.

The question this answers: an imagery extent necessarily includes ground that is
not the thing being studied. Does that outside ground look different in embedding
space, or does the AOI boundary cut through a single continuous land-cover
population? A clustering that splits cleanly along the boundary says the AOI is a
distinct thing; one that does not says the boundary is arbitrary with respect to
what the satellite sees.

Method:

  1. Take the AlphaEarth annual embedding (64-dimensional, 10 m) across the whole
     imagery extent — not just the AOI, because the outside is half the comparison.
  2. Draw a stratified sample, balanced between inside-AOI and outside-AOI, so the
     clustering is not dominated by whichever side happens to be larger.
  3. Fit k-means on the sample in Earth Engine, using embedding bands only. The
     inside/outside flag is deliberately withheld from training: the clusterer must
     not be told the answer it is being tested on.
  4. Apply the clusterer back to the sample and to the full extent, then compare
     cluster composition inside versus outside.

Outputs a clusters.json chart payload for the viewer side panel, a samples CSV,
and (optionally) an exported cluster raster.

Usage
-----
    uv run python -m pipeline.s5_imagery.embeddings \
        --extent config/study_extent.geojson --aoi config/stands.geojson \
        --year 2024 --k 6

    uv run python -m pipeline.s5_imagery.embeddings \
        --aoi config/stands.geojson --derive-extent buffer --buffer-m 750 \
        --year 2023 --k 8 --n-samples 3000 --export-clusters drive
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry.base import BaseGeometry

from pipeline.s5_imagery import vectors

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# AlphaEarth annual embeddings — 64 unit-norm bands at 10 m. Same asset the
# clearcut/agriculture work uses (notebooks/clearcut_ag_common.py), kept in sync
# deliberately so embedding-space results are comparable across the project.
EMBEDDING_COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
EMBEDDING_BANDS = [f"A{i:02d}" for i in range(64)]
EMBEDDING_SCALE_M = 10

# AlphaEarth coverage. Requests outside this yield an empty collection.
EMBEDDING_FIRST_YEAR = 2017

DEFAULT_K = 6
DEFAULT_SAMPLES_PER_CLASS = 1500
DEFAULT_SEED = 42

# The outside-AOI side needs enough of the extent to be a population rather than a
# boundary sliver. Below this the comparison is not worth running.
MIN_OUTSIDE_FRACTION = 0.05

# Earth Engine getInfo chokes on large feature collections; page through instead.
FETCH_CHUNK = 500

# Above this the charts stop being readable and the palette starts cycling. Cobweb
# in particular can emit dozens of clusters on a large sample.
CLUSTER_COUNT_WARN = 20

CLUSTERS_SCHEMA = "artemis.embeddings.clusters/2"

# Categorical palette for cluster identity. Okabe-Ito extended — chosen because
# cluster IDs are nominal and must stay distinguishable for colorblind viewers.
CLUSTER_PALETTE = [
    "#e69f00",
    "#56b4e9",
    "#009e73",
    "#f0e442",
    "#0072b2",
    "#d55e00",
    "#cc79a7",
    "#999999",
    "#8c564b",
    "#17becf",
    "#bcbd22",
    "#7f7f7f",
]


def cluster_palette(k: int) -> list[str]:
    """Colors for k clusters, cycling if k exceeds the palette."""
    if k < 1:
        raise ValueError("k must be >= 1")
    return [CLUSTER_PALETTE[i % len(CLUSTER_PALETTE)] for i in range(k)]


# ──────────────────────────────────────────────────────────────────────────────
# Clustering methods
# ──────────────────────────────────────────────────────────────────────────────
#
# All of these are Earth Engine clusterers, which is a deliberate constraint: a
# server-side clusterer can be applied back to the full extent image to produce a
# cluster raster, so every method in the dropdown gets a map layer as well as
# charts. A locally-fit model (scikit-learn) would label the sampled points fine
# but could not paint the extent without a round trip, leaving half a feature.
#
# The set spans genuinely different assumptions rather than variations on one:
# fixed-k centroids, two different automatic-k criteria, competitive learning, and
# incremental hierarchical clustering.

CLUSTER_METHODS: dict[str, dict[str, Any]] = {
    "kmeans": {
        "label": "k-means (Euclidean)",
        "description": "Fixed k, spherical clusters, squared-Euclidean distance.",
        "auto_k": False,
        "uses_k": True,
    },
    "kmeans_manhattan": {
        "label": "k-means (Manhattan)",
        "description": (
            "Fixed k with L1 distance. Medians rather than means, so it is less "
            "swayed by a few extreme bands — worth comparing at 64 dimensions, where "
            "Euclidean distance concentrates."
        ),
        "auto_k": False,
        "uses_k": True,
    },
    "xmeans": {
        "label": "X-means (auto k, BIC)",
        "description": (
            "Searches k between --k-min and --k-max, splitting clusters while the "
            "Bayesian information criterion improves. Use when k is not known."
        ),
        "auto_k": True,
        "uses_k": False,
    },
    "cascade_kmeans": {
        "label": "Cascade k-means (auto k, Calinski-Harabasz)",
        "description": (
            "Runs k-means across --k-min to --k-max and keeps the k with the best "
            "Calinski-Harabasz score. A second opinion on k that does not share "
            "X-means' BIC assumptions."
        ),
        "auto_k": True,
        "uses_k": False,
    },
    "lvq": {
        "label": "Learning vector quantization",
        "description": (
            "Competitive learning: prototypes move toward the samples that win them. "
            "Order-sensitive and non-deterministic, but can follow elongated "
            "structure that centroid methods cut through."
        ),
        "auto_k": False,
        "uses_k": True,
    },
    "cobweb": {
        "label": "Cobweb (hierarchical, emergent k)",
        "description": (
            "Incremental hierarchical clustering; k emerges from --cobweb-cutoff "
            "rather than being set. Can produce many small clusters — raise the "
            "cutoff to merge more aggressively."
        ),
        "auto_k": True,
        "uses_k": False,
    },
}

DEFAULT_METHOD = "kmeans"


def resolve_methods(methods: str | None) -> list[str]:
    """
    Parse the --methods list, preserving order and dropping duplicates.

    Unknown names fail loudly with the valid set rather than being skipped, since a
    typo would otherwise silently produce a run without the method asked for.
    """
    if not methods:
        return [DEFAULT_METHOD]

    resolved: list[str] = []
    for token in str(methods).replace(" ", "").split(","):
        if not token:
            continue
        if token not in CLUSTER_METHODS:
            raise ValueError(
                f"Unknown clustering method {token!r}. Available: "
                f"{', '.join(sorted(CLUSTER_METHODS))}"
            )
        if token not in resolved:
            resolved.append(token)

    if not resolved:
        raise ValueError("--methods resolved to nothing")
    return resolved


def build_clusterer(ee, method: str, k: int, k_min: int, k_max: int, seed: int,
                    cobweb_acuity: float, cobweb_cutoff: float):
    """
    Construct an untrained Earth Engine clusterer for one method.

    Kept separate from training so the parameter mapping is inspectable in one
    place — these Weka wrappers take different argument names for the same idea
    (nClusters vs numClusters vs minClusters/maxClusters).
    """
    if method == "kmeans":
        return ee.Clusterer.wekaKMeans(nClusters=k, distanceFunction="Euclidean", seed=seed)
    if method == "kmeans_manhattan":
        return ee.Clusterer.wekaKMeans(nClusters=k, distanceFunction="Manhattan", seed=seed)
    if method == "xmeans":
        return ee.Clusterer.wekaXMeans(minClusters=k_min, maxClusters=k_max, seed=seed)
    if method == "cascade_kmeans":
        return ee.Clusterer.wekaCascadeKMeans(minClusters=k_min, maxClusters=k_max)
    if method == "lvq":
        return ee.Clusterer.wekaLVQ(numClusters=k)
    if method == "cobweb":
        return ee.Clusterer.wekaCobweb(acuity=cobweb_acuity, cutoff=cobweb_cutoff, seed=seed)
    raise ValueError(f"Unknown clustering method {method!r}")


def observed_cluster_count(cluster_ids: list[int]) -> int:
    """
    Number of clusters actually produced.

    Auto-k methods do not report their k up front, and even fixed-k methods can
    leave a cluster empty, so the count comes from the labels rather than from the
    parameter that was requested.
    """
    if not cluster_ids:
        raise ValueError("No cluster labels to count")
    if min(cluster_ids) < 0:
        # Checked on the minimum, not the maximum: a single negative label among
        # valid ones is still a broken labelling, and cluster_distribution would
        # index a list with it.
        raise ValueError("Cluster labels must be non-negative")
    return max(cluster_ids) + 1


# ──────────────────────────────────────────────────────────────────────────────
# Statistics (pure)
# ──────────────────────────────────────────────────────────────────────────────


def cluster_distribution(records: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """
    Per-cluster inside/outside counts and shares.

    ``inside_share`` is the fraction of *inside* samples landing in this cluster;
    ``outside_share`` the same for outside samples. They are normalized within each
    side, so they stay comparable even when the two sides have different sample
    counts.

    ``inside_fraction`` is the other direction — of everything in this cluster, how
    much is inside the AOI. A cluster at 0.95 is essentially an AOI signature; one
    at 0.5 is shared with the surroundings.
    """
    if k < 1:
        raise ValueError("k must be >= 1")

    inside_counts = [0] * k
    outside_counts = [0] * k
    for record in records:
        cluster = int(record["cluster"])
        if not 0 <= cluster < k:
            raise ValueError(f"Cluster id {cluster} outside range 0..{k - 1}")
        if record["inside"]:
            inside_counts[cluster] += 1
        else:
            outside_counts[cluster] += 1

    total_inside = sum(inside_counts)
    total_outside = sum(outside_counts)
    palette = cluster_palette(k)

    distribution = []
    for cluster in range(k):
        inside = inside_counts[cluster]
        outside = outside_counts[cluster]
        total = inside + outside
        inside_share = inside / total_inside if total_inside else 0.0
        outside_share = outside / total_outside if total_outside else 0.0
        distribution.append(
            {
                "cluster": cluster,
                "color": palette[cluster],
                "inside_count": inside,
                "outside_count": outside,
                "total_count": total,
                "inside_share": round(inside_share, 6),
                "outside_share": round(outside_share, 6),
                "share_delta": round(inside_share - outside_share, 6),
                "inside_fraction": round(inside / total, 6) if total else None,
            }
        )
    return distribution


def jensen_shannon_divergence(p: list[float], q: list[float]) -> float:
    """
    Jensen-Shannon divergence between two discrete distributions, base 2.

    Bounded in [0, 1]: 0 means inside and outside distribute over clusters
    identically, 1 means they share no cluster at all. Symmetric and finite even
    when one side has zero mass in a cluster, which is why it is used here instead
    of KL divergence.
    """
    if len(p) != len(q):
        raise ValueError("Distributions must have equal length")
    if not p:
        raise ValueError("Distributions must be non-empty")

    p_sum, q_sum = sum(p), sum(q)
    if p_sum <= 0 or q_sum <= 0:
        return 0.0
    p_norm = [value / p_sum for value in p]
    q_norm = [value / q_sum for value in q]

    def _kl(a: list[float], b: list[float]) -> float:
        return sum(
            ai * math.log2(ai / bi) for ai, bi in zip(a, b, strict=True) if ai > 0 and bi > 0
        )

    m = [(ai + bi) / 2 for ai, bi in zip(p_norm, q_norm, strict=True)]
    divergence = 0.5 * _kl(p_norm, m) + 0.5 * _kl(q_norm, m)
    return float(min(1.0, max(0.0, divergence)))


def interpret_divergence(value: float) -> str:
    """Plain-language reading of the JS divergence, for the panel."""
    if value < 0.05:
        return "Inside and outside are effectively the same population in embedding space."
    if value < 0.15:
        return "Weak separation — the AOI boundary barely registers in embedding space."
    if value < 0.35:
        return "Moderate separation — inside and outside favour different clusters."
    if value < 0.6:
        return "Strong separation — most clusters are clearly inside- or outside-dominated."
    return "Near-complete separation — inside and outside occupy distinct clusters."


def pca_2d(matrix: np.ndarray) -> tuple[np.ndarray, list[float]]:
    """
    Project rows onto their first two principal components.

    Plain SVD on the centered matrix — no scikit-learn dependency for what is 3
    lines of numpy. Returns the 2-D coordinates and the explained-variance ratio of
    each component, so the panel can label how much structure the scatter shows.
    """
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("Expected a 2-D matrix")
    if matrix.shape[0] < 2 or matrix.shape[1] < 2:
        raise ValueError("Need at least 2 samples and 2 features for PCA")

    centered = matrix - matrix.mean(axis=0)
    _, singular_values, components = np.linalg.svd(centered, full_matrices=False)

    coords = centered @ components[:2].T
    variances = singular_values**2
    total = float(variances.sum())
    ratios = [float(value / total) for value in variances[:2]] if total > 0 else [0.0, 0.0]
    return coords, ratios


def build_run(
    method: str,
    inside_flags: list[bool],
    cluster_ids: list[int],
    layer_info: dict[str, Any],
    k_requested: int | None = None,
) -> dict[str, Any]:
    """
    Assemble one clustering run: its distribution, separability, and per-point labels.

    ``cluster_ids`` is parallel to ``inside_flags`` and to the shared scatter points,
    which is what lets the panel switch methods without re-plotting geometry — only
    the colors change.
    """
    if len(inside_flags) != len(cluster_ids):
        raise ValueError("inside flags and cluster ids must be the same length")

    spec = CLUSTER_METHODS.get(method)
    if spec is None:
        raise ValueError(f"Unknown clustering method {method!r}")

    k_observed = observed_cluster_count(cluster_ids)
    records = [
        {"cluster": cluster, "inside": inside}
        for cluster, inside in zip(cluster_ids, inside_flags, strict=True)
    ]
    distribution = cluster_distribution(records, k_observed)

    inside_counts = [float(entry["inside_count"]) for entry in distribution]
    outside_counts = [float(entry["outside_count"]) for entry in distribution]
    divergence = jensen_shannon_divergence(inside_counts, outside_counts)

    return {
        "method": method,
        "label": spec["label"],
        "description": spec["description"],
        "auto_k": spec["auto_k"],
        "k_requested": k_requested if spec["uses_k"] else None,
        "k_observed": k_observed,
        "palette": cluster_palette(k_observed),
        "clusters": distribution,
        "separability": {
            "jensen_shannon_divergence": round(divergence, 6),
            "interpretation": interpret_divergence(divergence),
        },
        "cluster_by_point": [int(value) for value in cluster_ids],
        "layers": layer_info,
    }


def build_clusters_payload(
    name: str,
    slug: str,
    year: int,
    scale_m: float,
    seed: int,
    inside_flags: list[bool],
    coords: np.ndarray | None,
    variance_ratios: list[float],
    extent_geom: BaseGeometry,
    aoi_geom: BaseGeometry,
    runs: list[dict[str, Any]],
    default_method: str | None = None,
) -> dict[str, Any]:
    """
    Assemble the payload the viewer side panel renders. Pure.

    Scatter geometry is stored once, not per run. Every method clusters the same
    sampled embeddings, so the PCA coordinates are identical across methods and only
    the cluster assignment differs — duplicating 1500 points per method would bloat
    the catalog for no information.
    """
    if not runs:
        raise ValueError("At least one clustering run is required")

    points = []
    if coords is not None:
        for inside, (x, y) in zip(inside_flags, coords, strict=True):
            points.append(
                {"x": round(float(x), 4), "y": round(float(y), 4), "inside": bool(inside)}
            )

    inside_total = sum(1 for flag in inside_flags if flag)
    chosen = default_method or runs[0]["method"]
    if all(run["method"] != chosen for run in runs):
        raise ValueError(f"default_method {chosen!r} is not among the runs")

    return {
        "schema": CLUSTERS_SCHEMA,
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "name": name,
        "slug": slug,
        "collection": EMBEDDING_COLLECTION,
        "year": year,
        "scale_m": scale_m,
        "seed": seed,
        "sample": {
            "inside": inside_total,
            "outside": len(inside_flags) - inside_total,
            "total": len(inside_flags),
        },
        "extent": {
            "bounds": vectors.bounds_list(extent_geom),
            "area_ha": round(vectors.area_ha(extent_geom), 2),
        },
        "aoi": {
            "bounds": vectors.bounds_list(aoi_geom),
            "area_ha": round(vectors.area_ha(aoi_geom), 2),
        },
        "default_method": chosen,
        "runs": runs,
        "scatter": {
            "explained_variance_ratio": [round(value, 6) for value in variance_ratios],
            "points": points,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Earth Engine
# ──────────────────────────────────────────────────────────────────────────────


def embedding_image(ee, year: int, extent_ee):
    """AlphaEarth embedding mosaic for one year over the extent."""
    collection = (
        ee.ImageCollection(EMBEDDING_COLLECTION)
        .filterBounds(extent_ee)
        .filterDate(f"{year}-01-01", f"{year}-12-31")
    )
    if int(collection.size().getInfo()) == 0:
        raise SystemExit(
            f"No AlphaEarth embeddings for {year} over this extent. "
            f"Coverage starts in {EMBEDDING_FIRST_YEAR}; try an earlier year."
        )
    return collection.mosaic().select(EMBEDDING_BANDS)


def inside_band(ee, aoi_ee):
    """
    A 0/1 band marking the AOI.

    Painted rather than sampled from a vector so stratifiedSample can use it as a
    class band, which is what makes the inside/outside sample balanced by
    construction instead of by luck of the draw.
    """
    return ee.Image(0).byte().paint(ee.FeatureCollection([ee.Feature(aoi_ee)]), 1).rename("inside")


def fetch_features(ee, collection, total: int, chunk: int = FETCH_CHUNK) -> list[dict[str, Any]]:
    """Page a FeatureCollection to the client; getInfo fails outright on large ones."""
    features: list[dict[str, Any]] = []
    for offset in range(0, total, chunk):
        page = collection.toList(chunk, offset).getInfo()
        if not page:
            break
        features.extend(page)
        logger.info("  fetched %d/%d samples", len(features), total)
    return features


def submit_cluster_export(
    ee,
    image,
    extent_ee,
    description: str,
    target: str,
    scale_m: float,
    crs: str,
    folder: str,
    bucket: str | None,
    prefix: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Submit the cluster raster export and describe it for the payload."""
    record: dict[str, Any] = {
        "target": target,
        "description": description,
        "filename": f"{description}.tif",
        "scale_m": scale_m,
        "crs": crs,
    }
    common = {
        "image": image.clip(extent_ee).toByte(),
        "description": description,
        "region": extent_ee,
        "scale": scale_m,
        "crs": crs,
        "maxPixels": 1e13,
        "fileFormat": "GeoTIFF",
        "formatOptions": {"cloudOptimized": True},
    }

    if target == "drive":
        record["folder"] = folder
        task = ee.batch.Export.image.toDrive(folder=folder, fileNamePrefix=description, **common)
    else:
        object_path = f"{prefix.rstrip('/')}/{description}" if prefix else description
        record["bucket"] = bucket
        record["object"] = f"{object_path}.tif"
        task = ee.batch.Export.image.toCloudStorage(
            bucket=bucket, fileNamePrefix=object_path, **common
        )

    if dry_run:
        record["task_id"] = None
        record["status"] = "not submitted (--dry-run)"
        return record

    task.start()
    record["task_id"] = task.id
    record["status"] = "submitted"
    logger.info("Cluster raster export submitted: %s (task %s)", description, task.id)
    return record


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cluster Earth Engine embeddings inside vs outside an area of interest",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    layers = parser.add_argument_group("vector layers")
    layers.add_argument("--aoi", required=True, help="Area-of-interest vector layer (required)")
    layers.add_argument("--aoi-layer", help="Layer name within a multi-layer AOI source")
    layers.add_argument("--extent", help="Imagery-extent vector layer (defaults to derived)")
    layers.add_argument("--extent-layer", help="Layer name within a multi-layer extent source")
    layers.add_argument("--derive-extent", choices=vectors.EXTENT_MODES, default="bbox")
    layers.add_argument("--buffer-m", type=float, default=0.0)

    clustering = parser.add_argument_group("clustering")
    clustering.add_argument("--year", type=int, required=True, help="Embedding year")
    clustering.add_argument(
        "--methods",
        default=DEFAULT_METHOD,
        help=(
            "Comma-separated clustering methods to run; each becomes an option in the "
            "viewer's method dropdown. Available: " + ", ".join(sorted(CLUSTER_METHODS))
        ),
    )
    clustering.add_argument(
        "--list-methods",
        action="store_true",
        help="Print the available clustering methods and exit",
    )
    clustering.add_argument(
        "--default-method",
        help="Method the viewer selects on load (defaults to the first in --methods)",
    )
    clustering.add_argument("--k", type=int, default=DEFAULT_K, help="Number of clusters")
    clustering.add_argument(
        "--k-min", type=int, default=2, help="Lower bound for automatic-k methods"
    )
    clustering.add_argument(
        "--k-max", type=int, default=12, help="Upper bound for automatic-k methods"
    )
    clustering.add_argument(
        "--cobweb-acuity", type=float, default=1.0, help="Cobweb minimum cluster variance"
    )
    clustering.add_argument(
        "--cobweb-cutoff",
        type=float,
        default=0.002,
        help="Cobweb merge threshold; raise it to produce fewer, larger clusters",
    )
    clustering.add_argument(
        "--n-samples",
        type=int,
        default=DEFAULT_SAMPLES_PER_CLASS,
        help="Samples per side (inside and outside each get this many)",
    )
    clustering.add_argument("--scale", type=float, default=EMBEDDING_SCALE_M)
    clustering.add_argument("--seed", type=int, default=DEFAULT_SEED)

    export = parser.add_argument_group("export")
    export.add_argument("--export-clusters", choices=("none", "drive", "gcs"), default="none")
    export.add_argument("--folder", default="artemis_embeddings")
    export.add_argument("--gcs-bucket")
    export.add_argument("--gcs-prefix", default="embeddings")
    export.add_argument("--crs", default="EPSG:4326")

    parser.add_argument("--name", help="Display name (defaults to the AOI file stem)")
    parser.add_argument("--out-dir", help="Output directory (default data/interim/embeddings/<slug>)")
    parser.add_argument("--project", help="Earth Engine project ID")
    parser.add_argument("--dry-run", action="store_true", help="Sample and analyze, export nothing")
    return parser


def _print_methods() -> int:
    print("Available clustering methods (--methods):\n")
    for name, spec in CLUSTER_METHODS.items():
        marker = "auto k" if spec["auto_k"] else "uses --k"
        print(f"  {name:<16} {spec['label']}  [{marker}]")
        print(f"                   {spec['description']}\n")
    print(f"Default: {DEFAULT_METHOD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # --list-methods is informational, so it must not trip the required arguments.
    if argv is not None and "--list-methods" in argv:
        return _print_methods()
    if argv is None and "--list-methods" in sys.argv[1:]:
        return _print_methods()

    args = parser.parse_args(argv)

    try:
        methods = resolve_methods(args.methods)
    except ValueError as err:
        raise SystemExit(str(err)) from err

    default_method = args.default_method or methods[0]
    if default_method not in methods:
        raise SystemExit(
            f"--default-method {default_method!r} is not in --methods ({', '.join(methods)})"
        )

    if args.k < 2:
        raise SystemExit("--k must be at least 2; one cluster cannot separate anything")
    if args.k_min < 2:
        raise SystemExit("--k-min must be at least 2")
    if args.k_max < args.k_min:
        raise SystemExit("--k-max must be >= --k-min")
    if args.year < EMBEDDING_FIRST_YEAR:
        raise SystemExit(f"AlphaEarth embeddings start in {EMBEDDING_FIRST_YEAR}")
    if args.export_clusters == "gcs" and not args.gcs_bucket:
        raise SystemExit("--export-clusters gcs requires --gcs-bucket")

    aoi_geom = vectors.layer_geometry(vectors.load_layer(args.aoi, args.aoi_layer))
    logger.info("AOI: %s (%.1f ha)", args.aoi, vectors.area_ha(aoi_geom))

    if args.extent:
        extent_geom = vectors.layer_geometry(vectors.load_layer(args.extent, args.extent_layer))
        extent_source = str(args.extent)
    else:
        extent_geom = vectors.derive_extent(aoi_geom, args.derive_extent, args.buffer_m)
        extent_source = f"derived from AOI (mode={args.derive_extent}, buffer={args.buffer_m} m)"
    logger.info("Extent: %s (%.1f ha)", extent_source, vectors.area_ha(extent_geom))

    extent_ha = vectors.area_ha(extent_geom)
    outside_ha = extent_ha - vectors.area_ha(aoi_geom)
    outside_fraction = outside_ha / extent_ha if extent_ha > 0 else 0.0
    if outside_fraction < MIN_OUTSIDE_FRACTION:
        # A rectangular AOI with --derive-extent bbox lands here: the projected
        # envelope is a hair larger than the AOI, so "outside" exists numerically
        # but is a sliver along the boundary, not a comparison population.
        raise SystemExit(
            f"Only {outside_fraction * 100:.2f}% of the extent lies outside the AOI "
            f"({outside_ha:.1f} of {extent_ha:.1f} ha), too little to characterize the "
            "outside population. Widen the extent with --derive-extent buffer and a "
            "larger --buffer-m, or supply a separate --extent layer."
        )
    logger.info(
        "Outside-AOI area available for comparison: %.1f ha (%.1f%% of extent)",
        outside_ha,
        outside_fraction * 100,
    )

    name = args.name or Path(args.aoi).stem
    slug = vectors.slugify(name)
    out_dir = Path(args.out_dir) if args.out_dir else Path("data/interim/embeddings") / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    import ee

    if args.project:
        ee.Initialize(project=args.project)
    else:
        ee.Initialize()

    extent_ee = vectors.to_ee_geometry(extent_geom)
    aoi_ee = vectors.to_ee_geometry(aoi_geom)

    logger.info("Loading %s for %d", EMBEDDING_COLLECTION, args.year)
    image = embedding_image(ee, args.year, extent_ee)
    classified_image = image.addBands(inside_band(ee, aoi_ee))

    logger.info("Sampling %d points per side at %.1f m", args.n_samples, args.scale)
    sample = classified_image.stratifiedSample(
        numPoints=args.n_samples,
        classBand="inside",
        region=extent_ee,
        scale=args.scale,
        seed=args.seed,
        classValues=[0, 1],
        classPoints=[args.n_samples, args.n_samples],
        dropNulls=True,
        geometries=True,
        tileScale=4,
    )

    sample_size = int(sample.size().getInfo())
    if sample_size < args.k:
        raise SystemExit(
            f"Only {sample_size} samples returned, fewer than --k {args.k}. The extent may be "
            "too small for the sampling scale, or the AOI too small to place points inside."
        )
    logger.info("Sample returned %d points", sample_size)

    # Train every method on embedding bands only. The inside flag stays out of
    # training so no clusterer is handed the boundary it is being evaluated against.
    #
    # Each method's labels are attached to the *same* FeatureCollection under its own
    # property name, so all methods come back in a single paged fetch rather than one
    # fetch per method.
    trained: dict[str, Any] = {}
    labelled = sample
    for method in methods:
        spec = CLUSTER_METHODS[method]
        logger.info("Training %s", spec["label"])
        try:
            clusterer = build_clusterer(
                ee,
                method,
                k=args.k,
                k_min=args.k_min,
                k_max=args.k_max,
                seed=args.seed,
                cobweb_acuity=args.cobweb_acuity,
                cobweb_cutoff=args.cobweb_cutoff,
            )
            trained[method] = clusterer.train(features=sample, inputProperties=EMBEDDING_BANDS)
        except Exception as err:  # noqa: BLE001 — report which method, then stop
            raise SystemExit(
                f"Earth Engine rejected the {method!r} clusterer: {err}\n"
                "If this is a parameter-name mismatch, check the signature against the "
                "current ee.Clusterer docs and adjust build_clusterer(); the other "
                "methods can still be run with --methods."
            ) from err
        labelled = labelled.cluster(trained[method], f"cluster_{method}")

    logger.info("Fetching labelled samples")
    features = fetch_features(ee, labelled, sample_size)

    inside_flags: list[bool] = []
    coordinates_list: list[list[float | None]] = []
    labels_by_method: dict[str, list[int]] = {method: [] for method in methods}
    embedding_rows: list[list[float]] = []

    for feature in features:
        properties = feature.get("properties", {})
        if properties.get("inside") is None:
            continue
        if any(properties.get(f"cluster_{method}") is None for method in methods):
            continue
        row = [properties.get(band) for band in EMBEDDING_BANDS]
        if any(value is None for value in row):
            continue

        coordinates = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        inside_flags.append(bool(int(properties["inside"])))
        coordinates_list.append([coordinates[0], coordinates[1]])
        for method in methods:
            labels_by_method[method].append(int(properties[f"cluster_{method}"]))
        embedding_rows.append([float(value) for value in row])

    if not inside_flags:
        raise SystemExit("No usable samples returned from Earth Engine.")

    inside_n = sum(inside_flags)
    logger.info("Usable samples: %d inside, %d outside", inside_n, len(inside_flags) - inside_n)
    if inside_n == 0 or inside_n == len(inside_flags):
        raise SystemExit(
            "Sampling produced points on only one side of the AOI boundary. Check that the "
            "AOI falls inside the extent and is large enough to hold sample points."
        )

    coords, variance_ratios = pca_2d(np.array(embedding_rows))

    runs: list[dict[str, Any]] = []
    for method in methods:
        cluster_ids = labels_by_method[method]
        k_observed = observed_cluster_count(cluster_ids)
        spec = CLUSTER_METHODS[method]
        logger.info("%s produced %d clusters", spec["label"], k_observed)
        if k_observed > CLUSTER_COUNT_WARN:
            logger.warning(
                "  %d clusters is a lot to read; the palette will cycle. For cobweb, raise "
                "--cobweb-cutoff to merge more aggressively.",
                k_observed,
            )

        cluster_image = image.cluster(trained[method]).rename("cluster")
        layer_info: dict[str, Any] = {
            "cluster_tile_url": None,
            "tile_url_generated_utc": None,
            "export": None,
        }
        try:
            mapid = cluster_image.getMapId(
                {"min": 0, "max": max(0, k_observed - 1), "palette": cluster_palette(k_observed)}
            )
            fetcher = mapid.get("tile_fetcher")
            layer_info["cluster_tile_url"] = (
                fetcher.url_format if fetcher is not None else mapid.get("tile_url")
            )
            layer_info["tile_url_generated_utc"] = dt.datetime.now(dt.UTC).isoformat(
                timespec="seconds"
            )
        except Exception as err:  # noqa: BLE001 — tile preview is best-effort
            logger.warning("  could not build cluster tile URL for %s: %s", method, err)

        if args.export_clusters != "none":
            layer_info["export"] = submit_cluster_export(
                ee,
                cluster_image,
                extent_ee,
                f"embed_clusters_{slug}_{args.year}_{method}_k{k_observed}",
                args.export_clusters,
                args.scale,
                args.crs,
                args.folder,
                args.gcs_bucket,
                args.gcs_prefix,
                args.dry_run,
            )

        runs.append(
            build_run(
                method=method,
                inside_flags=inside_flags,
                cluster_ids=cluster_ids,
                layer_info=layer_info,
                k_requested=args.k,
            )
        )

    payload = build_clusters_payload(
        name=name,
        slug=slug,
        year=args.year,
        scale_m=args.scale,
        seed=args.seed,
        inside_flags=inside_flags,
        coords=coords,
        variance_ratios=variance_ratios,
        extent_geom=extent_geom,
        aoi_geom=aoi_geom,
        runs=runs,
        default_method=default_method,
    )
    payload["extent"]["source"] = extent_source
    payload["aoi"]["source"] = str(args.aoi)

    clusters_path = out_dir / "clusters.json"
    clusters_path.write_text(json.dumps(payload, indent=2))

    samples_path = out_dir / "samples.csv"
    with samples_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["lon", "lat", "inside", "pc1", "pc2", *[f"cluster_{method}" for method in methods]]
        )
        for index, (lon, lat) in enumerate(coordinates_list):
            writer.writerow(
                [
                    lon,
                    lat,
                    int(inside_flags[index]),
                    round(float(coords[index][0]), 6),
                    round(float(coords[index][1]), 6),
                    *[labels_by_method[method][index] for method in methods],
                ]
            )

    (out_dir / "extent.geojson").write_text(
        json.dumps(vectors.feature_collection(extent_geom, {"role": "imagery_extent"}), indent=2)
    )
    (out_dir / "aoi.geojson").write_text(
        json.dumps(vectors.feature_collection(aoi_geom, {"role": "area_of_interest"}), indent=2)
    )

    logger.info("─" * 60)
    logger.info("Inside/outside separability by method (Jensen-Shannon divergence):")
    for run in sorted(
        payload["runs"],
        key=lambda item: item["separability"]["jensen_shannon_divergence"],
        reverse=True,
    ):
        logger.info(
            "  %-42s k=%-3d JSD %.4f%s",
            run["label"],
            run["k_observed"],
            run["separability"]["jensen_shannon_divergence"],
            "   <- viewer default" if run["method"] == default_method else "",
        )
    logger.info("")
    logger.info("Cluster detail for the default method (%s):", default_method)
    default_run = next(run for run in payload["runs"] if run["method"] == default_method)
    for entry in default_run["clusters"]:
        logger.info(
            "  cluster %d: inside %5.1f%% / outside %5.1f%% (%.0f%% of its pixels inside)",
            entry["cluster"],
            entry["inside_share"] * 100,
            entry["outside_share"] * 100,
            (entry["inside_fraction"] or 0) * 100,
        )
    logger.info("%s", default_run["separability"]["interpretation"])
    logger.info("Chart payload: %s", clusters_path)
    logger.info("Samples: %s", samples_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
