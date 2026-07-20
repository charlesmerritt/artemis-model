"""
Sliver resolution for ARTEMIS management units.

Turns a fragmented candidate-unit layer into a clean "state-zero" management-unit
map where every polygon is a single, operationally runnable stand. This is the step
the harvest scheduler blocks on: FVS simulates *stands*, so every management unit fed
to it must be one contiguous polygon at or above a minimum operational size.

Procedure (adapted from the LETO ArcGIS prototype, `scripts/LETO.V1.1.txt`):

    1. Explode multipart polygons to singlepart
       (LETO: ``MultipartToSinglepart``).
    2. Resolve polygons below the minimum stand size
       (LETO threshold: **5 acres**), via one of two policies:
         - ``"merge"``  — dissolve each sliver into the adjacent unit with which it
                          shares the longest boundary (ArcGIS "Eliminate" semantics).
                          Preserves forest area and leaves no gaps → the default.
         - ``"drop"``   — delete sub-threshold polygons, matching the LETO prototype
                          exactly. Faster, but discards forest area and can leave gaps.

**Why merge is the default (decision for review):** LETO's prototype *deletes* sub-5-acre
pieces. For a complete, gap-free state-zero unit map that conserves the modelled forest
area, merging slivers into their best neighbour is preferred; ``drop`` reproduces the
prototype's behaviour when that is wanted. The 5-acre threshold is carried over from LETO
unchanged.

All geometry work assumes a **projected CRS in metres** (ARTEMIS uses EPSG:5070); a
geographic CRS raises, because area/length in degrees is meaningless here.

Usage:
    uv run python -m pipeline.s3_management.sliver_merge \\
        --input data/interim/management_units_5co/12125/candidate_management_units.gpkg \\
        --output data/interim/management_units_5co/12125/management_units_state0.gpkg \\
        --policy merge --min-acres 5
"""

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

logger = logging.getLogger(__name__)

# 1 international acre in square metres. Matches LETO's ACRES_US area unit.
SQ_M_PER_ACRE = 4046.8564224

# LETO minimum operational stand size (LETO.V1.1 `multipart_to_singlepart_and_delete_small`).
MIN_STAND_ACRES = 5.0


def area_acres(gdf: gpd.GeoDataFrame) -> pd.Series:
    """Return polygon areas in acres. Requires a projected (metre) CRS."""
    if gdf.crs is None or gdf.crs.is_geographic:
        raise ValueError(
            "sliver_merge needs a projected CRS in metres (e.g. EPSG:5070); "
            f"got {gdf.crs}. Reproject with gdf.to_crs('EPSG:5070') first."
        )
    return gdf.geometry.area / SQ_M_PER_ACRE


def explode_to_singlepart(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Explode multipart polygons to singlepart (LETO MultipartToSinglepart)."""
    exploded = gdf.explode(index_parts=False, ignore_index=True)
    return exploded


def flag_slivers(gdf: gpd.GeoDataFrame, min_acres: float = MIN_STAND_ACRES) -> pd.Series:
    """Boolean Series: True where a polygon is below the minimum stand size."""
    return area_acres(gdf) < min_acres


def _shared_boundary_length(geom_a, geom_b) -> float:
    """Length of the shared boundary between two polygons (0 if they only touch at a point)."""
    if not geom_a.intersects(geom_b):
        return 0.0
    return geom_a.boundary.intersection(geom_b.boundary).length


class _UnionFind:
    """Minimal union-find so a sliver chained to another sliver still lands on one root."""

    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        root = k
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[k] != root:  # path compression
            self.parent[k], k = root, self.parent[k]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def merge_slivers_to_neighbors(
    gdf: gpd.GeoDataFrame,
    min_acres: float = MIN_STAND_ACRES,
    drop_orphans: bool = False,
) -> gpd.GeoDataFrame:
    """
    Dissolve each sub-threshold sliver into the neighbour with which it shares the
    longest boundary (ArcGIS "Eliminate" semantics).

    A sliver whose longest neighbour is itself a sliver is chained via union-find, so
    connected sliver clusters and their best non-sliver anchor dissolve together in a
    single pass. Each resulting unit inherits the attributes of its largest member.

    Orphan slivers (no touching neighbour) stay put unless ``drop_orphans=True``.
    """
    gdf = gdf.reset_index(drop=True).copy()
    if len(gdf) == 0:
        return gdf

    acres = area_acres(gdf)
    is_sliver = (acres < min_acres).to_numpy()
    if not is_sliver.any():
        return gdf

    sindex = gdf.sindex
    geoms = gdf.geometry.to_numpy()

    uf = _UnionFind(range(len(gdf)))
    orphans: list[int] = []

    for i in range(len(gdf)):
        if not is_sliver[i]:
            continue
        geom = geoms[i]
        best_j, best_len = None, 0.0
        for j in sindex.query(geom, predicate="intersects"):
            if j == i:
                continue
            shared = _shared_boundary_length(geom, geoms[j])
            if shared > best_len:
                best_len, best_j = shared, int(j)
        if best_j is None:
            orphans.append(i)
        else:
            uf.union(i, best_j)

    # Dissolve each connected component; attributes come from its largest member.
    components: dict[int, list[int]] = {}
    for i in range(len(gdf)):
        components.setdefault(uf.find(i), []).append(i)

    rows = []
    for members in components.values():
        rep = max(members, key=lambda m: geoms[m].area)
        row = gdf.iloc[rep].copy()
        if len(members) > 1:
            row["geometry"] = unary_union([geoms[m] for m in members]).buffer(0)
        rows.append(row)

    result = gpd.GeoDataFrame(rows, columns=gdf.columns, crs=gdf.crs).reset_index(drop=True)

    if drop_orphans and orphans:
        keep = area_acres(result) >= min_acres
        n_drop = int((~keep).sum())
        if n_drop:
            logger.info("Dropping %d orphan sliver(s) with no mergeable neighbour", n_drop)
        result = result[keep].reset_index(drop=True)

    return _refresh_area_columns(result)


def drop_slivers(gdf: gpd.GeoDataFrame, min_acres: float = MIN_STAND_ACRES) -> gpd.GeoDataFrame:
    """Delete polygons below the minimum stand size (LETO prototype behaviour)."""
    gdf = gdf.reset_index(drop=True).copy()
    keep = area_acres(gdf) >= min_acres
    n_drop = int((~keep).sum())
    logger.info("Dropping %d polygon(s) below %.1f acres", n_drop, min_acres)
    return _refresh_area_columns(gdf[keep].reset_index(drop=True))


def _refresh_area_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Recompute derived area columns so they match the resolved geometries."""
    if len(gdf) == 0:
        gdf["area_acres"] = pd.Series(dtype="float64")
        return gdf
    gdf = gdf.copy()
    gdf["area_acres"] = area_acres(gdf)
    if "unit_area_ha" in gdf.columns:
        gdf["unit_area_ha"] = gdf.geometry.area / 10_000
    return gdf


def resolve_slivers(
    gdf: gpd.GeoDataFrame,
    policy: str = "merge",
    min_acres: float = MIN_STAND_ACRES,
    explode: bool = True,
    drop_orphans: bool = False,
) -> gpd.GeoDataFrame:
    """
    Full sliver-resolution procedure: explode multipart → apply policy.

    policy:
        "merge" — dissolve slivers into their longest-shared-boundary neighbour (default).
        "drop"  — delete sub-threshold polygons (LETO prototype behaviour).
    """
    if policy not in {"merge", "drop"}:
        raise ValueError(f"policy must be 'merge' or 'drop', got {policy!r}")

    work = explode_to_singlepart(gdf) if explode else gdf
    n_before = len(work)
    n_slivers = int(flag_slivers(work, min_acres).sum())
    logger.info("Resolving slivers: %d polygons, %d below %.1f ac, policy=%s",
                n_before, n_slivers, min_acres, policy)

    if policy == "merge":
        result = merge_slivers_to_neighbors(work, min_acres, drop_orphans=drop_orphans)
    else:
        result = drop_slivers(work, min_acres)

    logger.info("Sliver resolution done: %d → %d polygons", n_before, len(result))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Resolve management-unit slivers into a clean state-zero map")
    parser.add_argument("--input", type=Path, required=True, help="Candidate-unit GeoPackage/GPKG")
    parser.add_argument("--output", type=Path, required=True, help="Output GeoPackage")
    parser.add_argument("--layer", type=str, default=None, help="Input layer name (optional)")
    parser.add_argument("--policy", choices=["merge", "drop"], default="merge")
    parser.add_argument("--min-acres", type=float, default=MIN_STAND_ACRES)
    parser.add_argument("--drop-orphans", action="store_true",
                        help="Drop slivers with no mergeable neighbour (merge policy only)")
    parser.add_argument("--target-crs", type=str, default="EPSG:5070",
                        help="Reproject to this CRS before resolving (default EPSG:5070)")
    args = parser.parse_args()

    gdf = gpd.read_file(args.input, layer=args.layer)
    if args.target_crs and str(gdf.crs) != args.target_crs:
        gdf = gdf.to_crs(args.target_crs)

    result = resolve_slivers(gdf, policy=args.policy, min_acres=args.min_acres,
                             drop_orphans=args.drop_orphans)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(args.output, driver="GPKG")
    logger.info("Wrote %d units to %s", len(result), args.output)


if __name__ == "__main__":
    main()
