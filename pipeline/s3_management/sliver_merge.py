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
         - ``"merge"``  — dissolve each sliver into its *best* neighbour (see below).
                          Conserves forest area and leaves no gaps, but produces spatially
                          multipart units. Available as an area-conserving alternative.

Best neighbour
--------------
Under ``"merge"``, a sliver may only join a candidate of the **same ``unit_class``**. This
is a hard constraint, not a preference: ``managed`` forest is available for a harvest
regime and ``riparian`` (BMP stream buffer) forest is grown but never harvested, so
dissolving one into the other puts unharvestable acres into a harvest unit and destroys
the managed/riparian area partition that ``sketch_management_units.py`` establishes. It
matters at pilot scale — riparian buffers are long thin strips, so most of that layer is
sliver-sized by construction. Inputs with no ``unit_class`` column are treated as a single
class, which is what the pre-riparian layers were.

Within a class, the neighbour is chosen by ranking every polygon the sliver shares a
boundary with on ``(same parcel, shared boundary length)``, taking the maximum:

    1. **Same parcel wins outright.** A stand that spans two ownerships is not a stand —
       it cannot be prescribed, sold, or harvested as one unit — so a same-parcel
       neighbour is preferred over a different-parcel one no matter how short the shared
       edge is. The parcel column is auto-detected from ``PARCEL_ID_COLUMNS``; inputs
       without one fall through to boundary length alone.
    2. **Longest shared boundary breaks the tie**, which is ArcGIS ``Eliminate`` semantics
       and keeps the merged unit as compact as the fragmentation allows.

Fragments that share no same-class boundary with anything (cut loose by an erased road or
stream buffer) have no ranking evidence at all, so they fall to a nearest-unit pass
instead — LETO's ``GenerateNearTable`` nearest-runnable assignment, restricted to the
sliver's own class. Parcel preference deliberately does *not* apply there: with no shared
edge, distance is the only real signal, and honouring a parcel ID across a long gap would
invent a wildly non-contiguous stand.

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

# Parcel-identifier columns carried through by sketch_management_units.py, most specific
# first. Used to prefer a same-parcel neighbour when merging; absent columns are ignored.
PARCEL_ID_COLUMNS = ("PARCELID", "NPARNO")

# Column partitioning units into managed vs riparian. Merging never crosses it.
UNIT_CLASS_COLUMN = "unit_class"


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


def resolve_parcel_column(gdf: gpd.GeoDataFrame, parcel_col: str | None = None) -> str | None:
    """
    Return the column to use as the parcel/owner key, or None if the input has none.

    An explicit `parcel_col` must exist (a typo silently degrading to boundary-only
    merging is worse than an error); otherwise the first present `PARCEL_ID_COLUMNS`
    entry is used.
    """
    if parcel_col is not None:
        if parcel_col not in gdf.columns:
            raise ValueError(
                f"parcel_col {parcel_col!r} not in input columns: {list(gdf.columns)}"
            )
        return parcel_col
    return next((col for col in PARCEL_ID_COLUMNS if col in gdf.columns), None)


def _class_labels(gdf: gpd.GeoDataFrame):
    """Per-row `unit_class` codes used to forbid cross-class merges, or None when the input
    carries no such column (pre-riparian layers are a single implicit class).

    Integer codes rather than raw labels so that missing values compare consistently in
    both the boundary and nearest stages — object None and float NaN disagree under `!=`.
    All unlabelled rows share the -1 sentinel, i.e. they form one class among themselves.
    """
    if UNIT_CLASS_COLUMN not in gdf.columns:
        return None
    codes, _ = pd.factorize(gdf[UNIT_CLASS_COLUMN], use_na_sentinel=True)
    return codes


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


def _shared_boundary_edges(gdf: gpd.GeoDataFrame, min_acres: float, parcel_col: str | None = None):
    """For each sliver, an edge to its best same-class boundary-sharing neighbour, ranked
    on ``(same parcel, shared boundary length)``. Returns (edges, orphan_positions) where
    orphans share no same-class boundary with anything."""
    is_sliver = (area_acres(gdf) < min_acres).to_numpy()
    edges: list[tuple[int, int]] = []
    orphans: list[int] = []
    if not is_sliver.any():
        return edges, orphans

    parcels = gdf[parcel_col].to_numpy() if parcel_col else None
    classes = _class_labels(gdf)
    sindex = gdf.sindex
    geoms = gdf.geometry.to_numpy()
    for i in range(len(gdf)):
        if not is_sliver[i]:
            continue
        # Rank key is (same_parcel, shared_length); a missing/NaN parcel id never matches,
        # so unattributed rows rank as different-parcel rather than clumping together.
        same_parcel_possible = parcels is not None and not pd.isna(parcels[i])
        best_j, best_key = None, (0, 0.0)
        for j in sindex.query(geoms[i], predicate="intersects"):
            if j == i:
                continue
            if classes is not None and classes[j] != classes[i]:
                continue  # hard constraint: never dissolve across managed/riparian
            shared = _shared_boundary_length(geoms[i], geoms[j])
            if shared <= 0.0:  # point contact only: not a mergeable neighbour
                continue
            same_parcel = int(same_parcel_possible and parcels[j] == parcels[i])
            key = (same_parcel, shared)
            if key > best_key:
                best_key, best_j = key, int(j)
        if best_j is None:
            orphans.append(i)
        else:
            edges.append((i, best_j))
    return edges, orphans


def _nearest_edges(gdf: gpd.GeoDataFrame, min_acres: float) -> list[tuple[int, int]]:
    """For each remaining sliver, an edge to the nearest *non-sliver* unit of its own class
    (by distance). Mirrors LETO's ``GenerateNearTable`` nearest-runnable assignment for
    isolated pieces. A class with no runnable unit yields no edges, so its slivers survive
    as orphans rather than being pulled across the managed/riparian line."""
    import numpy as np
    import shapely

    is_sliver = (area_acres(gdf) < min_acres).to_numpy()
    classes = _class_labels(gdf)
    geoms = gdf.geometry.to_numpy()

    if classes is None:
        groups = [np.arange(len(gdf))]
    else:
        groups = [np.where(classes == code)[0] for code in np.unique(classes)]

    edges: list[tuple[int, int]] = []
    for members in groups:
        sliver_pos = members[is_sliver[members]]
        non_pos = members[~is_sliver[members]]
        if len(sliver_pos) == 0 or len(non_pos) == 0:
            continue
        tree = shapely.STRtree(geoms[non_pos])
        nearest_local = tree.nearest(geoms[sliver_pos])
        edges.extend(
            (int(sliver_pos[k]), int(non_pos[nearest_local[k]])) for k in range(len(sliver_pos))
        )
    return edges


def merge_slivers_to_neighbors(
    gdf: gpd.GeoDataFrame,
    min_acres: float = MIN_STAND_ACRES,
    drop_orphans: bool = False,
    nearest_fallback: bool = True,
    max_passes: int = 4,
    parcel_col: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Dissolve every sub-threshold sliver into its best neighbouring unit, producing a
    complete (gap-free), area-conserving state-zero map.

    Candidates are always restricted to the sliver's own ``unit_class`` (see the module
    docstring); the ranking below only ever chooses *within* that class.

    Two stages:
      1. **Best-neighbour merge** — each sliver joins the same-class boundary-sharing unit
         that ranks highest on ``(same parcel, shared boundary length)``: a same-parcel
         neighbour first, then ArcGIS "Eliminate" semantics (longest shared edge) among
         equals. `parcel_col` defaults to the first `PARCEL_ID_COLUMNS` entry present, and
         inputs carrying no parcel column rank on boundary length alone. Sliver
         chains/clusters are resolved together via union-find, repeated until stable. This
         stage alone leaves *isolated* slivers (fragments separated from every same-class
         unit by an erased buffer) unresolved.
      2. **Nearest-unit fallback** (``nearest_fallback=True``, default) — each remaining
         sliver is absorbed into the nearest non-sliver unit of its own class, mirroring
         LETO's ``GenerateNearTable`` nearest-runnable assignment. Parcel preference does
         not apply here: with no shared edge there is no adjacency evidence, and distance is
         the only signal worth trusting. This can create spatially multipart units (a main
         body plus a detached piece), which FVS treats as one stand. Set
         ``nearest_fallback=False`` to keep only boundary merges.

    Each resulting unit inherits the attributes of its largest member — safe for
    ``unit_class`` precisely because a merged component is class-pure by construction.
    Slivers that still cannot be resolved (e.g. their class has no non-sliver unit at all)
    stay put unless ``drop_orphans=True``.
    """
    gdf = gdf.reset_index(drop=True).copy()
    # Route the trivial cases through _refresh_area_columns too, so every return path
    # yields the same schema (area_acres / refreshed size_class), not just the merged path.
    if len(gdf) == 0 or not (area_acres(gdf) < min_acres).any():
        return _refresh_area_columns(gdf)

    parcel_col = resolve_parcel_column(gdf, parcel_col)
    logger.info(
        "Best-neighbour merge within %s, ranking on %s",
        UNIT_CLASS_COLUMN if UNIT_CLASS_COLUMN in gdf.columns else "a single implicit class",
        f"(same {parcel_col}, shared boundary)" if parcel_col else "shared boundary only",
    )

    # Stage 1: best-neighbour passes until no further progress.
    for _ in range(max_passes):
        edges, _ = _shared_boundary_edges(gdf, min_acres, parcel_col=parcel_col)
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
    gdf = gdf.copy()
    if len(gdf) == 0:
        # Preserve the schema on empty inputs too.
        if "area_acres" not in gdf.columns:
            gdf["area_acres"] = pd.Series(dtype="float64")
        return gdf
    gdf["area_acres"] = area_acres(gdf)
    if "unit_area_ha" in gdf.columns:
        gdf["unit_area_ha"] = gdf.geometry.area / 10_000
        # A merged unit inherits its largest member's size_class, which is now stale — the
        # geometry grew. Reclassify from the refreshed area so the label matches the unit.
        if "size_class" in gdf.columns:
            from pipeline.s3_management.sketch_management_units import classify_unit_size
            gdf["size_class"] = gdf["unit_area_ha"].apply(classify_unit_size)
    return gdf


def resolve_slivers(
    gdf: gpd.GeoDataFrame,
    policy: str = "drop",
    min_acres: float = MIN_STAND_ACRES,
    explode: bool = True,
    drop_orphans: bool = False,
    nearest_fallback: bool = True,
    parcel_col: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Full sliver-resolution procedure: explode multipart → apply policy.

    policy:
        "drop"  — delete sub-threshold polygons (LETO delineation behaviour). Default.
        "merge" — dissolve slivers into their best neighbour (same parcel, then longest
                  shared boundary, then nearest-unit fallback for isolated pieces).
                  Area-conserving alternative.
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
                                            nearest_fallback=nearest_fallback,
                                            parcel_col=parcel_col)
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
    parser.add_argument("--parcel-col", type=str, default=None,
                        help=f"Parcel/owner column to prefer when merging "
                             f"(default: first of {', '.join(PARCEL_ID_COLUMNS)} present)")
    parser.add_argument("--target-crs", type=str, default="EPSG:5070",
                        help="Reproject to this CRS before resolving (default EPSG:5070)")
    args = parser.parse_args()

    gdf = gpd.read_file(args.input, layer=args.layer)
    if args.target_crs and str(gdf.crs) != args.target_crs:
        gdf = gdf.to_crs(args.target_crs)

    result = resolve_slivers(gdf, policy=args.policy, min_acres=args.min_acres,
                             drop_orphans=args.drop_orphans,
                             nearest_fallback=not args.no_nearest_fallback,
                             parcel_col=args.parcel_col)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(args.output, driver="GPKG")
    logger.info("Wrote %d units to %s", len(result), args.output)


if __name__ == "__main__":
    main()
