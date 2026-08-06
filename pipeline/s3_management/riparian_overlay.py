"""
Riparian overlay (Phase 3.3) — the last step in stand delineation.

Runs **after** the stand map is settled: parcels intersected with forest, large units split,
slivers resolved. At that point the operational stands exist, and this step overlays the
BMP buffer layer onto them, cuts them along the buffer boundaries, and classifies the
buffered pieces as untouchable.

Ordering is the point. Applying the buffers earlier makes hydrography *shape* the stands —
every stream carves the parcel before the stand map is cleaned, and the leftovers hit
sliver resolution. Applying them last makes the buffer an *annotation* on stands that
already exist, so each riparian polygon is traceable to the stand it came out of
(``parent_unit_id``).

**Stands are contiguous.** A buffer running through a stand produces *two stands*, one on
each side, plus the riparian strip between them — not one stand with a hole in it, and not
one multipart stand straddling the water. :func:`overlay_riparian` explodes to singlepart
and :func:`check_contiguity` enforces it, because `gpd.overlay` returns a MultiPolygon per
input row by default and that silently produces exactly the wrong thing.

Every output piece is a stand in its own right, so a piece that lands below the minimum
stand size is an ordinary sliver and goes through the ordinary policy in
`sliver_merge.py` — there is no special "remnant" concept.

Usage:
    uv run python -m pipeline.s3_management.riparian_overlay \\
        --stands data/interim/management_units_5co/12125/management_units_state0.gpkg \\
        --buffers data/interim/management_units_5co/12125/riparian_buffers.gpkg \\
        --output data/interim/management_units_5co/12125/management_units_final.gpkg
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

MANAGED, RIPARIAN = "managed", "riparian"
UNIT_CLASS_COL = "unit_class"
BUFFER_CLASS_COL = "buffer_class"
PARENT_ID_COL = "parent_unit_id"

# Managed + riparian must account for the stand area to within this much. Not zero: overlay
# and buffer(0) cleaning move vertices by floating-point amounts across tens of thousands
# of polygons.
AREA_TOLERANCE_HA = 0.01


def explode_to_stands(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    One row per contiguous polygon.

    A stand is a contiguous piece of ground. `gpd.overlay` returns one row per input
    feature, so a stand cut in two by a stream comes back as a single MultiPolygon row —
    one "stand" on both banks of a river, which is not a stand.
    """
    exploded = gdf.explode(index_parts=False, ignore_index=True)
    return exploded[~exploded.geometry.is_empty & (exploded.geometry.area > 0)].reset_index(drop=True)


def check_contiguity(gdf: gpd.GeoDataFrame, context: str = "") -> None:
    """Raise if any unit is multipart. Stands are contiguous; this is not negotiable."""
    multipart = gdf.geometry.geom_type.str.startswith("Multi")
    if multipart.any():
        where = f" ({context})" if context else ""
        raise ValueError(
            f"{int(multipart.sum())} unit(s) are multipart{where}. A stand is one contiguous "
            f"polygon — a unit spanning both banks of a stream is two stands, not one. "
            f"Explode with explode_to_stands() before this check."
        )


def overlay_riparian(
    stands: gpd.GeoDataFrame,
    buffers: gpd.GeoDataFrame,
    id_field: str = "unit_id",
) -> tuple[gpd.GeoDataFrame, dict]:
    """
    Overlay the BMP buffers on a settled stand map. Returns ``(units, accounting)``.

    Each stand is cut into the part inside a buffer (``unit_class = "riparian"``, carrying
    ``buffer_class``) and the part outside (``unit_class = "managed"``). Both are exploded
    to contiguous singlepart stands, and each keeps ``parent_unit_id`` pointing at the stand
    it came out of.

    ``SMZ_Pct`` is set to 100 on riparian and 0 on managed — literally true after the cut,
    and it makes the absolute riparian override in `regime_assignment.py` fire through the
    already-tested path rather than needing riparian-specific regime logic.

    Area is conserved exactly: ``Σ managed + Σ riparian == Σ stands``. Nothing is erased
    here; erasure (open water, road artefacts) already happened upstream.
    """
    if id_field not in stands.columns:
        raise ValueError(f"stand map has no {id_field!r} column; got {list(stands.columns)}")

    stand_ha = stands.geometry.area.sum() / 10_000

    if len(buffers) == 0 or len(stands) == 0:
        managed = stands.copy()
        managed[BUFFER_CLASS_COL] = pd.NA
        riparian = stands.iloc[0:0].copy()
        riparian[BUFFER_CLASS_COL] = pd.NA
    else:
        buffer_cols = buffers[[BUFFER_CLASS_COL, "geometry"]]
        riparian = gpd.overlay(stands, buffer_cols, how="intersection", keep_geom_type=True)
        managed = gpd.overlay(stands, buffer_cols, how="difference", keep_geom_type=True)
        managed[BUFFER_CLASS_COL] = pd.NA

    managed = explode_to_stands(managed)
    riparian = explode_to_stands(riparian)

    for frame, unit_class, smz in ((managed, MANAGED, 0.0), (riparian, RIPARIAN, 100.0)):
        frame[PARENT_ID_COL] = frame[id_field].astype(str)
        frame[UNIT_CLASS_COL] = unit_class
        frame["SMZ_Pct"] = smz

    units = gpd.GeoDataFrame(
        pd.concat([managed, riparian], ignore_index=True), crs=stands.crs
    )
    check_contiguity(units, context="riparian overlay output")

    # Re-key: every piece is a new stand, so unit_id must be unique per piece. The stand it
    # came from stays addressable through parent_unit_id.
    units[id_field] = [f"{parent}_{n:03d}" for n, parent in enumerate(units[PARENT_ID_COL])]
    units["unit_area_ha"] = units.geometry.area / 10_000

    managed_ha = units.loc[units[UNIT_CLASS_COL] == MANAGED, "unit_area_ha"].sum()
    riparian_ha = units.loc[units[UNIT_CLASS_COL] == RIPARIAN, "unit_area_ha"].sum()
    accounting = {
        "stand_ha_in": stand_ha,
        "managed_ha": managed_ha,
        "riparian_ha": riparian_ha,
        "residual_ha": stand_ha - (managed_ha + riparian_ha),
        "stands_in": int(len(stands)),
        "stands_out": int(len(units)),
        "riparian_units": int((units[UNIT_CLASS_COL] == RIPARIAN).sum()),
    }
    return units, accounting


def check_area_conserved(accounting: dict, tolerance_ha: float = AREA_TOLERANCE_HA) -> None:
    """
    Raise when the overlay loses or duplicates area.

    The failure this guards is the one the whole riparian change exists to fix: buffered
    acres silently leaving the landscape, which under-counts standing volume and carbon
    and shows up in no summary.
    """
    residual = abs(accounting["residual_ha"])
    if residual > tolerance_ha:
        raise ValueError(
            f"riparian overlay did not conserve area: {accounting['stand_ha_in']:.2f} ha in, "
            f"{accounting['managed_ha']:.2f} + {accounting['riparian_ha']:.2f} ha out "
            f"(residual {residual:.4f} ha > {tolerance_ha}). The overlay only reclassifies "
            f"ground; it must never erase any."
        )


def summarize_overlay(units: gpd.GeoDataFrame) -> pd.DataFrame:
    """Stand count and area by unit class and buffer class."""
    return (
        units.groupby([UNIT_CLASS_COL, BUFFER_CLASS_COL], dropna=False)
        .agg(stands=("unit_id", "count"),
             total_area_ha=("unit_area_ha", "sum"),
             median_area_ha=("unit_area_ha", "median"))
        .reset_index()
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(
        description="Overlay BMP riparian buffers on a settled stand map"
    )
    parser.add_argument("--stands", type=Path, required=True,
                        help="Stand map after sliver resolution (management_units_state0.gpkg)")
    parser.add_argument("--buffers", type=Path, required=True,
                        help="Buffer layer from sketch_management_units (riparian_buffers.gpkg)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--id-field", type=str, default="unit_id")
    args = parser.parse_args()

    stands = gpd.read_file(args.stands)
    buffers = gpd.read_file(args.buffers)
    if buffers.crs != stands.crs:
        buffers = buffers.to_crs(stands.crs)

    units, accounting = overlay_riparian(stands, buffers, id_field=args.id_field)
    check_area_conserved(accounting)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    units.to_file(args.output, driver="GPKG")

    summary = summarize_overlay(units)
    summary.to_csv(args.output.with_name("riparian_overlay_summary.csv"), index=False)
    pd.DataFrame([accounting]).to_csv(
        args.output.with_name("riparian_overlay_accounting.csv"), index=False
    )
    logger.info("%d stands in → %d out (%d riparian); %.2f ha managed, %.2f ha riparian",
                accounting["stands_in"], accounting["stands_out"],
                accounting["riparian_units"], accounting["managed_ha"], accounting["riparian_ha"])
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
