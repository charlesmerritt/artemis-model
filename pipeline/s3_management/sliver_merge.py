"""
Sliver resolution for ARTEMIS management units.

Turns a fragmented candidate-unit layer into a clean "state-zero" management-unit
map where every polygon is a single, operationally runnable stand. This is the step
the harvest scheduler blocks on: FVS simulates *stands*, so every management unit fed
to it must be one contiguous polygon at or above a minimum operational size.

Procedure (following the LETO ArcGIS prototype, `scripts/LETO.V1.1.txt`, function
``multipart_to_singlepart_and_delete_small``):

    1. Explode multipart polygons to singlepart
       (LETO: ``MultipartToSinglepart``).
    2. Resolve polygons below the minimum stand size
       (LETO threshold: **5 acres**), via one of two policies:
         - ``"drop"``   — delete sub-threshold polygons, exactly as the LETO delineation
                          script does. Clean single-part geometry. **This is the default:**
                          it is LETO's own sliver-elimination step for the state-zero map.
         - ``"merge"``  — dissolve each sliver into a neighbouring unit (longest shared
                          boundary, then nearest-unit fallback). Conserves forest area and
                          leaves no gaps, but produces spatially multipart units. Available
                          as an area-conserving alternative.

**Default is ``drop`` (LETO delineation style).** LETO eliminates sub-5-acre pieces at
delineation time; the forest they cover is picked up downstream by LETO's *second* script,
which imputes tree lists for tree-less/edge units from the nearest runnable unit
(``GenerateNearTable``) — a separate FVS-input step, not this module. The 5-acre threshold
is carried over from LETO unchanged.

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


def _dissolve_by_edges(gdf: gpd.GeoDataFrame, edges: list[tuple[int, int]]) -> gpd.GeoDataFrame:
    """Union-find over the given (i, j) edges, then dissolve each component into one
    polygon that keeps the attributes of its largest-area member."""
    uf = _UnionFind(range(len(gdf)))
    for i, j in edges:
        uf.union(i, j)

    components: dict[int, list[int]] = {}
    for i in range(len(gdf)):
        components.setdefault(uf.find(i), []).append(i)

    geoms = gdf.geometry.to_numpy()
    rows = []
    for members in components.values():
        rep = max(members, key=lambda m: geoms[m].area)
        row = gdf.iloc[rep].copy()
        if len(members) > 1:
            row["geometry"] = unary_union([geoms[m] for m in members]).buffer(0)
        rows.append(row)
    return gpd.GeoDataFrame(rows, columns=gdf.columns, crs=gdf.crs).reset_index(drop=True)


def _shared_boundary_edges(gdf: gpd.GeoDataFrame, min_acres: float):
    """For each sliver, an edge to the polygon it shares the longest boundary with.
    Returns (edges, orphan_positions) where orphans share no boundary with anything."""
    is_sliver = (area_acres(gdf) < min_acres).to_numpy()
    edges: list[tuple[int, int]] = []
    orphans: list[int] = []
    if not is_sliver.any():
        return edges, orphans

    sindex = gdf.sindex
    geoms = gdf.geometry.to_numpy()
    for i in range(len(gdf)):
        if not is_sliver[i]:
            continue
        best_j, best_len = None, 0.0
        for j in sindex.query(geoms[i], predicate="intersects"):
            if j == i:
                continue
            shared = _shared_boundary_length(geoms[i], geoms[j])
            if shared > best_len:
                best_len, best_j = shared, int(j)
        if best_j is None:
            orphans.append(i)
        else:
            edges.append((i, best_j))
    return edges, orphans


def _nearest_edges(gdf: gpd.GeoDataFrame, min_acres: float) -> list[tuple[int, int]]:
    """For each remaining sliver, an edge to the nearest *non-sliver* unit (by distance).
    Mirrors LETO's ``GenerateNearTable`` nearest-runnable assignment for isolated pieces."""
    import numpy as np
    import shapely

    is_sliver = (area_acres(gdf) < min_acres).to_numpy()
    sliver_pos = np.where(is_sliver)[0]
    non_pos = np.where(~is_sliver)[0]
    if len(sliver_pos) == 0 or len(non_pos) == 0:
        return []

    geoms = gdf.geometry.to_numpy()
    tree = shapely.STRtree(geoms[non_pos])
    nearest_local = tree.nearest(geoms[sliver_pos])
    return [(int(sliver_pos[k]), int(non_pos[nearest_local[k]])) for k in range(len(sliver_pos))]


def merge_slivers_to_neighbors(
    gdf: gpd.GeoDataFrame,
    min_acres: float = MIN_STAND_ACRES,
    drop_orphans: bool = False,
    nearest_fallback: bool = True,
    max_passes: int = 4,
) -> gpd.GeoDataFrame:
    """
    Dissolve every sub-threshold sliver into a real neighbouring unit, producing a
    complete (gap-free), area-conserving state-zero map.

    Two stages:
      1. **Shared-boundary merge** (ArcGIS "Eliminate" semantics) — each sliver joins the
         unit it shares the longest boundary with; sliver chains/clusters are resolved
         together via union-find, repeated until stable. This alone leaves *isolated*
         slivers (fragments separated from every unit by an erased buffer) unresolved.
      2. **Nearest-unit fallback** (``nearest_fallback=True``, default) — each remaining
         sliver is absorbed into its nearest non-sliver unit, mirroring LETO's
         ``GenerateNearTable`` nearest-runnable assignment. This can create spatially
         multipart units (a main body plus a detached piece), which FVS treats as one
         stand. Set ``nearest_fallback=False`` to keep only boundary merges.

    Each resulting unit inherits the attributes of its largest member. Slivers that still
    cannot be resolved (e.g. no non-sliver unit exists at all) stay put unless
    ``drop_orphans=True``.
    """
    gdf = gdf.reset_index(drop=True).copy()
    if len(gdf) == 0:
        return gdf
    if not (area_acres(gdf) < min_acres).any():
        return gdf

    # Stage 1: shared-boundary passes until no further progress.
    for _ in range(max_passes):
        edges, _ = _shared_boundary_edges(gdf, min_acres)
        if not edges:
            break
        n_before = len(gdf)
        gdf = _dissolve_by_edges(gdf, edges)
        if len(gdf) >= n_before:  # nothing collapsed this pass
            break

    # Stage 2: nearest-unit fallback for isolated residual slivers.
    if nearest_fallback:
        edges = _nearest_edges(gdf, min_acres)
        if edges:
            gdf = _dissolve_by_edges(gdf, edges)

    if drop_orphans:
        keep = area_acres(gdf) >= min_acres
        n_drop = int((~keep).sum())
        if n_drop:
            logger.info("Dropping %d residual sliver(s) with no mergeable unit", n_drop)
        gdf = gdf[keep].reset_index(drop=True)

    return _refresh_area_columns(gdf)


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
    policy: str = "drop",
    min_acres: float = MIN_STAND_ACRES,
    explode: bool = True,
    drop_orphans: bool = False,
    nearest_fallback: bool = True,
) -> gpd.GeoDataFrame:
    """
    Full sliver-resolution procedure: explode multipart → apply policy.

    policy:
        "drop"  — delete sub-threshold polygons (LETO delineation behaviour). Default.
        "merge" — dissolve slivers into a neighbouring unit (longest shared boundary, then
                  nearest-unit fallback for isolated pieces). Area-conserving alternative.
    """
    if policy not in {"merge", "drop"}:
        raise ValueError(f"policy must be 'merge' or 'drop', got {policy!r}")

    work = explode_to_singlepart(gdf) if explode else gdf
    n_before = len(work)
    n_slivers = int(flag_slivers(work, min_acres).sum())
    logger.info("Resolving slivers: %d polygons, %d below %.1f ac, policy=%s",
                n_before, n_slivers, min_acres, policy)

    if policy == "merge":
        result = merge_slivers_to_neighbors(work, min_acres, drop_orphans=drop_orphans,
                                            nearest_fallback=nearest_fallback)
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
    parser.add_argument("--policy", choices=["merge", "drop"], default="drop",
                        help="drop = LETO sliver elimination (default); merge = area-conserving")
    parser.add_argument("--min-acres", type=float, default=MIN_STAND_ACRES)
    parser.add_argument("--drop-orphans", action="store_true",
                        help="Drop any slivers that still cannot be merged (merge policy only)")
    parser.add_argument("--no-nearest-fallback", action="store_true",
                        help="Skip the nearest-unit fallback; keep only shared-boundary merges")
    parser.add_argument("--target-crs", type=str, default="EPSG:5070",
                        help="Reproject to this CRS before resolving (default EPSG:5070)")
    args = parser.parse_args()

    gdf = gpd.read_file(args.input, layer=args.layer)
    if args.target_crs and str(gdf.crs) != args.target_crs:
        gdf = gdf.to_crs(args.target_crs)

    result = resolve_slivers(gdf, policy=args.policy, min_acres=args.min_acres,
                             drop_orphans=args.drop_orphans,
                             nearest_fallback=not args.no_nearest_fallback)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(args.output, driver="GPKG")
    logger.info("Wrote %d units to %s", len(result), args.output)


if __name__ == "__main__":
    main()
