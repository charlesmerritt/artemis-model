"""County-by-county improvement of TreeMap 2022 and the Harris/NWOS 2022 ownership raster.

The five-county pilot (Baker, Columbia, Hamilton, Suwannee, Union) runs one county at a
time. Each county is saved on its own, and the counties are then stitched into the AOI.
Adding a county is one more FIPS on the command line, and that is the path to
statewide. Deck: ``docs/improved-rasters-fl/presentation.html``.

Per county:

1. **Grid.** The county's bounding box on TreeMap 2022's own 30 m EPSG:5070 grid, padded
   by ``PAD_PX``. The padding lets a patch that crosses the county line keep its full
   area for the minimum-patch-area test and its full ring for the donor search. Outputs
   are cropped back to the unpadded box and masked to the county.
2. **Holes.** Land (LANDFIRE EVT 2022 is not water) where TreeMap has no plot.
3. **Methods.** Each method in :mod:`add_back_methods` proposes hole pixels. Bookends
   (LANDFIRE 2016/2024 strata plus the AlphaEarth gate) always runs. Obata and Hansen
   run when their rasters are supplied (``--obata-tif``, ``--hansen-tif``); otherwise
   they are pending.
4. **Decision.** ``ConsensusRule`` combines the proposals, then the 5 ac minimum patch
   area applies.
5. **Vegetation.** Each accepted patch takes the modal TreeMap plot (``TM_ID``) in the
   nearest ring that has any (:func:`impute_establishment.donor_assignments`). Its
   bookend stratum sets the establishment mode (``config/establishment.yaml``): a cut
   or regrowing patch (S1, S3, S4) is ``scaled_young``, provenance 4, and takes only
   type and species mix from the donor; standing forest (S2) is ``donor_as_is``,
   provenance 2.
6. **Ownership.** Improved-forest pixels NWOS does not carry as forest take the nearest
   known owner (:mod:`ownership_repair`).

Outputs, one folder per county under ``/mnt/d/improved-rasters/counties/<fips>_<name>/``:
``treemap2022_{published,improved,provenance}.tif``,
``nwos2022_{published,improved,provenance}.tif``, ``add_back_method_bits.tif``,
``bookend_strata.tif``, ``establishment_patches.csv`` and ``summary.json``. ``stitch``
writes the same rasters for the AOI under ``aoi_5county/``, plus
``establishment_tree_lists.csv`` keyed by ``(TM_ID, establishment_mode)`` and, in its
``summary.json``, the live basal area and TPA the added-back acres carry with mature
donors vs. as established.

Dating and aging the add-back pixels (ADR 0003) is a second pass over a run folder:
:mod:`pipeline.s1_initial_state.add_back_stand_age`.

A consumer joining ``treemap2022_improved.tif`` to TreeMap's tree table must check the
provenance first: a provenance-4 pixel's ``TM_ID`` names its donor for forest type and
species mix only, and its trees are the ``scaled_young`` rows of the establishment list.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from rasterio.transform import Affine
from rasterio.windows import Window

from pipeline.ids import as_id_series, read_id_csv
from pipeline.spatial_ref import project_crs
from pipeline.s1_initial_state.add_back_methods import (
    METHOD_PRIORITY,
    AddBackMethod,
    ConsensusRule,
    MethodMasks,
    method_bits,
    priority_attribution,
)
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL, apply_mmu
from pipeline.s1_initial_state.impute_establishment import (
    EstablishmentMode,
    donor_assignments,
    establishment_effect,
    label_patches,
    load_establishment_policy,
    mode_for_stratum,
    scaled_establishment_lists,
)
from pipeline.s1_initial_state.ownership_repair import (
    MAX_DISTANCE_PX,
    NODATA as NWOS_NODATA,
    OwnershipProvenance,
    OwnershipRepairScope,
    repair_ownership,
)
from pipeline.s1_initial_state.statewide_repair import (
    FLGrid,
    GatedScores,
    legend_code_sets,
    scored_add_back,
    stratify_block,
)

AOI_COUNTIES = {"12003": "Baker", "12023": "Columbia", "12047": "Hamilton",
                "12121": "Suwannee", "12125": "Union"}
OUT_ROOT = Path("/mnt/d/improved-rasters")
AOI_NAME = "aoi_5county"
PAD_PX = 64          # wider than the widest donor ring (63 px) and any sub-MMU patch
OUTSIDE = 255        # uint8 nodata outside the county
PIXEL = 30.0

OBATA_YEARS = (2010, 2022)   # cut dated inside the run's window, capped at the TreeMap vintage
HANSEN_LOSSYEAR = (1, 22)    # 2001-2022, stored as years since 2000
HANSEN_TREECOVER_MIN = 30    # only ground that carried canopy in 2000


class Stage(StrEnum):
    ALL = "all"          # run each county, then stitch
    STITCH = "stitch"    # stitch the county outputs already saved


class TreeMapProvenance(IntEnum):
    WATER = 0             # LANDFIRE 2022 water; never a hole
    PUBLISHED = 1         # TreeMap 2022 as published
    ADDED_BACK = 2        # accepted hole, donor_as_is: the donor plot's own tree rows
    UNMAPPED_LAND = 3     # land TreeMap does not map and no accepted method adds back
    ADDED_BACK_YOUNG = 4  # accepted hole, scaled_young: the TM_ID gives type and species mix only;
                          # initialize from the scaled establishment list, never the donor's rows

    @classmethod
    def added_back(cls, provenance: np.ndarray) -> np.ndarray:
        """Pixels added back under either establishment mode."""
        return np.isin(provenance, (cls.ADDED_BACK, cls.ADDED_BACK_YOUNG))


PROVENANCE_BY_MODE = {EstablishmentMode.SCALED_YOUNG: TreeMapProvenance.ADDED_BACK_YOUNG,
                      EstablishmentMode.DONOR_AS_IS: TreeMapProvenance.ADDED_BACK}
PROVENANCE_DESCRIPTION = ("0 water, 1 published, 2 added back (donor as is), 3 unmapped land, "
                          "4 added back young (scaled establishment list, not the donor's trees)")


@dataclass(frozen=True)
class Inputs:
    treemap: Path = Path("/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS.tif")
    tree_table: Path = Path("/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv")
    landfire: Path = Path("/mnt/d/landfire")
    # The statewide AlphaEarth S3/S4 scores and fitted thresholds (score_holes_statewide),
    # and NWOS already warped onto the Florida grid (prepare_ownership_fl), from R2.
    scored: Path = OUT_ROOT / "inputs/hole_prob_similarity_statewide.tif"
    model: Path = OUT_ROOT / "inputs/hole_model.json"
    ownership: Path = OUT_ROOT / "inputs/us_forest_ownership_fl.tif"
    counties: Path = Path("/mnt/d/county_p010g.shp_nt00934/countyp010g.shp")
    obata: Path | None = None    # geepipe lastDist (disturbance year) on the TreeMap grid
    hansen: Path | None = None   # band 1 lossyear, band 2 treecover2000, on the TreeMap grid

    @property
    def vat(self) -> Path:
        """TreeMap's value attribute table: raster ``Value`` (== ``TM_ID``) -> ``FORTYPCD``, ..."""
        return self.treemap.with_name(self.treemap.name + ".vat.dbf")

    def evt(self, year: int) -> tuple[Path, Path]:
        base = self.landfire / f"LF{year}_EVT_CONUS" / f"LF{year}_EVT_CONUS"
        return base / "Tif" / f"LF{year}_EVT_CONUS.tif", base / "CSV_Data" / f"LF{year}_EVT.csv"


@dataclass(frozen=True)
class Grid:
    transform: Affine
    shape: tuple[int, int]


def county_grid(bounds, treemap_transform: Affine, pad: int = PAD_PX) -> Grid:
    """The county's bounding box snapped to the TreeMap grid, padded by ``pad`` pixels."""
    window, t = FLGrid(treemap_transform.c, treemap_transform.f).window(bounds)
    transform = Affine(PIXEL, 0, t.c - pad * PIXEL, 0, -PIXEL, t.f + pad * PIXEL)
    return Grid(transform, (int(window.height) + 2 * pad, int(window.width) + 2 * pad))


def read_on_grid(path: Path, grid: Grid, *, fill, indexes: int | list[int] = 1) -> np.ndarray:
    """Read ``path`` onto ``grid`` exactly: no resampling, ``fill`` past its edge.

    Raises when the source is not on the same 30 m lattice. A half-pixel shift must
    fail here rather than silently move every pixel 15 m.
    """
    height, width = grid.shape
    with rasterio.open(path) as src:
        if src.crs.to_epsg() != 5070 or (src.transform.a, src.transform.e) != (PIXEL, -PIXEL):
            raise ValueError(f"{path.name} is not a 30 m EPSG:5070 raster")
        col = (grid.transform.c - src.transform.c) / PIXEL
        row = (src.transform.f - grid.transform.f) / PIXEL
        if abs(col - round(col)) > 1e-6 or abs(row - round(row)) > 1e-6:
            raise ValueError(f"{path.name} is off the grid by ({col % 1:.3f}, {row % 1:.3f}) px")
        col, row = round(col), round(row)
        bands = [indexes] if isinstance(indexes, int) else list(indexes)
        out = np.full((len(bands), height, width), fill, dtype=src.dtypes[0])
        r0, c0 = max(row, 0), max(col, 0)
        r1, c1 = min(row + height, src.height), min(col + width, src.width)
        if r1 > r0 and c1 > c0:
            data = src.read(bands, window=Window(c0, r0, c1 - c0, r1 - r0))
            out[:, r0 - row:r1 - row, c0 - col:c1 - col] = data
    return out[0] if isinstance(indexes, int) else out


def obata_rule(last_disturbance_year: np.ndarray) -> np.ndarray:
    return (last_disturbance_year >= OBATA_YEARS[0]) & (last_disturbance_year <= OBATA_YEARS[1])


def hansen_rule(lossyear: np.ndarray, treecover2000: np.ndarray) -> np.ndarray:
    return ((lossyear >= HANSEN_LOSSYEAR[0]) & (lossyear <= HANSEN_LOSSYEAR[1])
            & (treecover2000 >= HANSEN_TREECOVER_MIN))


def stamp_donors(ids: np.ndarray, labels: np.ndarray, assignments: pd.DataFrame) -> np.ndarray:
    """Write each patch's donor ``TM_ID`` into ``ids`` in place; return the recovered mask.

    A patch with no reachable donor stays a hole: stamping it would write ``TM_ID`` 0
    (no plot) as if recovered, which FVS cannot initialize.
    """
    donor_per_patch = np.zeros(int(labels.max()) + 1, dtype=np.uint32)
    for patch_id, value in assignments.get("donor_tm_id", pd.Series(dtype=object)).items():
        if pd.notna(value):
            donor_per_patch[patch_id] = int(value)
    stamped = donor_per_patch[labels]
    recovered = stamped > 0
    ids[recovered] = stamped[recovered]
    return recovered


PATCH_COLUMNS = ["pixels", "acres", "stratum", "donor_tm_id", "est_year_low", "est_year_high",
                 "establishment_mode"]


def mode_provenance(labels: np.ndarray, assignments: pd.DataFrame) -> np.ndarray:
    """Per pixel, the added-back provenance code of its patch's establishment mode (0 off-patch)."""
    per_patch = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    for patch_id, mode in assignments.get("establishment_mode", pd.Series(dtype=object)).items():
        per_patch[patch_id] = PROVENANCE_BY_MODE[EstablishmentMode(mode)]
    return per_patch[labels]


@dataclass
class BlockResult:
    improved: np.ndarray               # uint32 TM_ID, 0 where no plot
    provenance: np.ndarray             # uint8 TreeMapProvenance
    method_bits: np.ndarray            # uint8 METHOD_BIT of every run method proposing the pixel
    added_back: np.ndarray             # bool: accepted and donor-filled
    ownership: np.ndarray              # uint8 Harris 0-8 after repair
    ownership_provenance: np.ndarray   # uint8 OwnershipProvenance
    assignments: pd.DataFrame          # patch -> donor
    labels: np.ndarray                 # accepted-patch labels, indexing ``assignments``
    masks: MethodMasks = field(repr=False)


def improve_block(tm_ids: np.ndarray, land: np.ndarray, masks: MethodMasks, strata: np.ndarray,
                  nwos: np.ndarray, *, rule: ConsensusRule, min_acres: float,
                  stratum_modes: Mapping[int, EstablishmentMode],
                  scope: OwnershipRepairScope = OwnershipRepairScope.ALL_FOREST,
                  reach: float = MAX_DISTANCE_PX) -> BlockResult:
    """Decide, fill and re-own the holes of one block. ``tm_ids`` is 0 where TreeMap has no plot.

    ``stratum_modes`` maps a patch's bookend stratum to its establishment mode; a stratum
    it does not list is ``scaled_young`` (:func:`impute_establishment.mode_for_stratum`).
    """
    hole = (tm_ids == 0) & land
    accepted = apply_mmu(masks.combine(rule) & hole, min_acres) & hole
    labels, n_patches = label_patches(accepted)
    improved = tm_ids.copy()
    if n_patches:
        assignments, _ = donor_assignments(improved, labels, strata=strata)
        assignments["establishment_mode"] = [
            str(mode_for_stratum(s, stratum_modes)) for s in assignments["stratum"]]
    else:  # donor_assignments cannot index an empty frame
        assignments = pd.DataFrame(columns=PATCH_COLUMNS).rename_axis("patch_id")
    added_back = stamp_donors(improved, labels, assignments)

    provenance = np.full(tm_ids.shape, TreeMapProvenance.WATER, dtype=np.uint8)
    provenance[hole] = TreeMapProvenance.UNMAPPED_LAND
    provenance[tm_ids > 0] = TreeMapProvenance.PUBLISHED
    provenance[added_back] = mode_provenance(labels, assignments)[added_back]

    ownership, ownership_provenance = repair_ownership(
        nwos, improved > 0, added_back, scope=scope, max_distance_px=reach)
    return BlockResult(improved, provenance, method_bits(masks) * hole, added_back,
                       ownership, ownership_provenance, assignments, labels, masks)


# ── one county ──────────────────────────────────────────────────────────────────────────


def load_county(counties: Path, fips: str):
    import geopandas as gpd

    rows = gpd.read_file(counties, where=f"ADMIN_FIPS = '{fips}'")
    if rows.empty:
        raise ValueError(f"county {fips} not found in {counties}")
    return rows.to_crs(5070).geometry.union_all()


def county_dir(out_root: Path, fips: str) -> Path:
    return out_root / "counties" / f"{fips}_{AOI_COUNTIES.get(fips, fips).lower()}"


def _method_masks(inputs: Inputs, grid: Grid, strata, hole, scored, gate) -> MethodMasks:
    masks: dict[AddBackMethod, np.ndarray | None] = {
        AddBackMethod.BOOKENDS: scored_add_back(strata, hole, scored, gate, min_acres=0.0),
    }
    if inputs.obata is not None:
        masks[AddBackMethod.OBATA_DISTURBANCE] = obata_rule(read_on_grid(inputs.obata, grid, fill=0)) & hole
    if inputs.hansen is not None:
        loss, cover = read_on_grid(inputs.hansen, grid, fill=0, indexes=[1, 2])
        masks[AddBackMethod.HANSEN_LOSS] = hansen_rule(loss, cover) & hole
    return MethodMasks(masks)


def _acres(mask: np.ndarray) -> float:
    return round(float(mask.sum()) * ACRES_PER_PIXEL, 1)


def _write(path: Path, values: np.ndarray, transform: Affine, nodata, description: str = "") -> None:
    """Write a single-band Cloud Optimized GeoTIFF; overviews are nearest (the values are classes)."""
    from rasterio.io import MemoryFile
    from rasterio.shutil import copy as rio_copy

    profile = dict(driver="GTiff", height=values.shape[0], width=values.shape[1], count=1,
                   dtype=values.dtype, crs=project_crs(), transform=transform, nodata=nodata)
    with MemoryFile() as mem:
        with mem.open(**profile) as tmp:
            tmp.write(values, 1)
            if description:
                tmp.update_tags(description=description)
        with mem.open() as tmp:
            rio_copy(tmp, path, driver="COG", compress="DEFLATE", blocksize=256,
                     overview_resampling="NEAREST", resampling="NEAREST")


def improve_county(fips: str, inputs: Inputs = Inputs(), out_root: Path = OUT_ROOT, *,
                   rule: ConsensusRule = ConsensusRule.UNION, min_acres: float = 5.0,
                   scope: OwnershipRepairScope = OwnershipRepairScope.ALL_FOREST,
                   reach: float = MAX_DISTANCE_PX,
                   stratum_modes: Mapping[int, EstablishmentMode] | None = None) -> dict:
    if stratum_modes is None:
        stratum_modes = load_establishment_policy().stratum_modes
    geom = load_county(inputs.counties, fips)
    with rasterio.open(inputs.treemap) as src:
        tm_transform, tm_nodata = src.transform, src.nodata
    grid = county_grid(geom.bounds, tm_transform)

    tm = read_on_grid(inputs.treemap, grid, fill=tm_nodata)
    tm_ids = np.where(tm == tm_nodata, 0, tm).astype(np.uint32)
    codes = {y: legend_code_sets(pd.read_csv(inputs.evt(y)[1])) for y in (2016, 2022, 2024)}
    evt = {}
    for year in codes:
        with rasterio.open(inputs.evt(year)[0]) as src:
            evt[year] = read_on_grid(inputs.evt(year)[0], grid, fill=src.nodata)
    land = (evt[2022] != codes[2022].water) & (evt[2022] >= 0) & (evt[2022] != 32767)
    hole = (tm_ids == 0) & land
    strata = stratify_block(evt[2016], evt[2024], codes[2016], codes[2024])
    strata[~hole] = 0
    scored = read_on_grid(inputs.scored, grid, fill=0, indexes=[1, 2])
    gate = GatedScores.from_model_json(inputs.model)
    masks = _method_masks(inputs, grid, strata, hole, scored, gate)
    nwos = read_on_grid(inputs.ownership, grid, fill=NWOS_NODATA)

    r = improve_block(tm_ids, land, masks, strata, nwos, rule=rule, min_acres=min_acres,
                      stratum_modes=stratum_modes, scope=scope, reach=reach)

    # Crop the padding away and mask to the county.
    crop = (slice(PAD_PX, -PAD_PX), slice(PAD_PX, -PAD_PX))
    out_transform = grid.transform * Affine.translation(PAD_PX, PAD_PX)
    inside = features.rasterize([(geom, 1)], out_shape=grid.shape, transform=grid.transform,
                                fill=0, dtype="uint8").astype(bool)
    outside = ~inside[crop]

    def county(values: np.ndarray, nodata):
        out = values[crop].copy()
        out[outside] = nodata
        return out

    out_dir = county_dir(out_root, fips)
    out_dir.mkdir(parents=True, exist_ok=True)
    tm_published = np.where(tm_ids > 0, tm_ids, np.uint32(tm_nodata)).astype(np.uint32)
    tm_improved = np.where(r.improved > 0, r.improved, np.uint32(tm_nodata)).astype(np.uint32)
    rasters = {
        "treemap2022_published.tif": (county(tm_published, tm_nodata), tm_nodata,
                                      "TreeMap 2022 TM_ID as published"),
        "treemap2022_improved.tif": (county(tm_improved, tm_nodata), tm_nodata,
                                     "TreeMap 2022 TM_ID with accepted holes donor-filled; "
                                     "on provenance 4 the TM_ID gives forest type and species mix only"),
        "treemap2022_provenance.tif": (county(r.provenance, OUTSIDE), OUTSIDE, PROVENANCE_DESCRIPTION),
        "add_back_method_bits.tif": (county(r.method_bits, OUTSIDE), OUTSIDE,
                                     "bit 1 bookends, 2 obata, 4 hansen (proposals before the rule)"),
        "bookend_strata.tif": (county(strata, OUTSIDE), OUTSIDE,
                               "LANDFIRE 2016/2024 strata S1-S5 over holes; 0 not a hole"),
        "nwos2022_published.tif": (county(nwos, NWOS_NODATA), NWOS_NODATA,
                                   "Harris/NWOS 2022 on the TreeMap grid, as published"),
        "nwos2022_improved.tif": (county(r.ownership, NWOS_NODATA), NWOS_NODATA,
                                  "Harris/NWOS 2022 with improved-forest pixels re-owned"),
        "nwos2022_provenance.tif": (county(r.ownership_provenance, OUTSIDE), OUTSIDE,
                                    "0 not forest, 1 NWOS kept, 2 nearest-owner imputed, 3 unresolved"),
    }
    for name, (values, nodata, description) in rasters.items():
        _write(out_dir / name, values, out_transform, nodata, description)

    # Patches: a patch crossing the county line is listed by each county it touches,
    # with the pixels it has inside this one.
    in_county_labels = r.labels[crop][~outside]
    counts = np.bincount(in_county_labels, minlength=len(r.assignments) + 1)
    patches = r.assignments.copy()
    patches["county_pixels"] = counts[patches.index.to_numpy()]
    patches = patches[patches["county_pixels"] > 0]
    patches.insert(0, "county_fips", fips)
    patches.reset_index().to_csv(out_dir / "establishment_patches.csv", index=False)

    summary = county_summary(fips, r, strata, land, crop, outside, rule, min_acres, scope, reach,
                             patches)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def county_summary(fips, r: BlockResult, strata, land, crop, outside, rule, min_acres,
                   scope, reach, patches: pd.DataFrame) -> dict:
    inside = ~outside

    def c(a):  # crop to the county
        return a[crop] & inside

    prov = r.provenance
    hole = c((prov == TreeMapProvenance.UNMAPPED_LAND) | TreeMapProvenance.added_back(prov))
    added = c(r.added_back)
    proposals = MethodMasks({m: (None if r.masks.masks[m] is None else c(r.masks.masks[m]))
                             for m in METHOD_PRIORITY})
    accepted_only = MethodMasks({m: (None if mk is None else mk & added)
                                 for m, mk in proposals.masks.items()})
    credited = priority_attribution(accepted_only, ConsensusRule.UNION)
    combined = c(r.masks.combine(rule))
    oprov = r.ownership_provenance
    improved_forest = c(r.improved > 0)
    return {
        "county_fips": fips,
        "county": AOI_COUNTIES.get(fips, fips),
        "rule": str(rule),
        "min_acres": min_acres,
        "county_acres": _acres(inside),
        "land_acres": _acres(c(land)),
        "published_forest_acres": _acres(c(prov == TreeMapProvenance.PUBLISHED)),
        "hole_acres": _acres(hole),
        "strata_acres": {f"S{s}": _acres(c(strata == s)) for s in range(1, 6)},
        "methods": {
            str(m): {"status": "pending" if proposals.masks[m] is None else "run",
                     "proposed_acres": None if proposals.masks[m] is None
                     else _acres(proposals.masks[m])}
            for m in METHOD_PRIORITY
        },
        "rule_accepted_before_mmu_acres": _acres(combined),
        "added_back_acres": _acres(added),
        "added_back_acres_by_mode": {str(m): _acres(c(prov == code))
                                     for m, code in PROVENANCE_BY_MODE.items()},
        "added_back_credit_acres": {str(m): None if v is None else round(v * ACRES_PER_PIXEL, 1)
                                    for m, v in credited.items()},
        "dropped_by_mmu_or_no_donor_acres": round(_acres(combined) - _acres(added), 1),
        "improved_forest_acres": _acres(improved_forest),
        "patches": int(len(patches)),
        "unique_donors": int(patches["donor_tm_id"].dropna().nunique()),
        "ownership": {
            "scope": str(scope),
            "reach_px": reach,
            "imputed_acres": _acres(c(r.ownership_provenance == OwnershipProvenance.IMPUTED)),
            "imputed_on_added_back_acres": _acres(
                c(r.ownership_provenance == OwnershipProvenance.IMPUTED) & added),
            "unresolved_acres": _acres(c(oprov == OwnershipProvenance.UNRESOLVED)),
        },
    }


# ── the stitch ──────────────────────────────────────────────────────────────────────────


def stitch_rasters(paths: list[Path], out: Path) -> int:
    """Mosaic same-grid county rasters: each pixel from the first county with data there.

    Returns how many pixels a later county also claimed (county polygons share borders,
    so this should be ~0).
    """
    with rasterio.open(paths[0]) as first:
        nodata, dtype = first.nodata, first.dtypes[0]
        tags = first.tags()
    boxes = []
    for p in paths:
        with rasterio.open(p) as s:
            boxes.append((s.transform.c, s.transform.f, s.transform.c + s.width * PIXEL,
                          s.transform.f - s.height * PIXEL))
    west, north = min(b[0] for b in boxes), max(b[1] for b in boxes)
    east, south = max(b[2] for b in boxes), min(b[3] for b in boxes)
    grid = Grid(Affine(PIXEL, 0, west, 0, -PIXEL, north),
                (round((north - south) / PIXEL), round((east - west) / PIXEL)))
    mosaic = np.full(grid.shape, nodata, dtype=dtype)
    overlap = 0
    for p in paths:
        values = read_on_grid(p, grid, fill=nodata)
        has = values != nodata
        free = mosaic == nodata
        overlap += int((has & ~free).sum())
        mosaic[has & free] = values[has & free]
    _write(out, mosaic, grid.transform, nodata, tags.get("description", ""))
    return overlap


TREE_COLUMNS = ["TM_ID", "PLT_CN", "STATUSCD", "TPA_UNADJ", "SPCD", "DIA", "HT", "CR"]


def donor_forest_types(vat: Path, tm_ids) -> pd.Series:
    """``FORTYPCD`` by donor ``TM_ID`` (string index), from TreeMap's VAT.

    The VAT carries ``Value`` as a float; :func:`as_id_series` turns it into the exact
    ``TM_ID`` string (TreeMap 2022's ``Value`` equals its ``TM_ID``). Its float ``PLT_CN``
    is never read.
    """
    import pyogrio

    vat_table = pyogrio.read_dataframe(vat, columns=["Value", "FORTYPCD"], read_geometry=False)
    codes = pd.Series(vat_table["FORTYPCD"].to_numpy(),
                      index=as_id_series(vat_table["Value"], column="TM_ID").to_numpy())
    return codes[codes.index.isin(set(tm_ids))]


def stitch_establishment(patches: pd.DataFrame, inputs: Inputs, out_dir: Path) -> dict:
    """Write ``establishment_tree_lists.csv`` and return the policy and its effect.

    The lists are keyed by ``(TM_ID, establishment_mode)``. The effect compares the live
    basal area and TPA of the added-back acres under their mature donors with what is
    established, AOI-wide and by mode; a patch crossing a county line counts its pixels
    in each county once.
    """
    resolved = patches.dropna(subset=["donor_tm_id"]).rename(columns={"donor_tm_id": "TM_ID"})
    donor_modes = resolved[["TM_ID", "establishment_mode"]].drop_duplicates()
    tree_table = read_id_csv(inputs.tree_table, usecols=TREE_COLUMNS)
    donor_rows = tree_table[tree_table["TM_ID"].isin(set(donor_modes["TM_ID"]))]
    policy = load_establishment_policy()
    lists = scaled_establishment_lists(donor_modes, donor_rows,
                                       donor_forest_types(inputs.vat, donor_modes["TM_ID"]), policy)
    lists.to_csv(out_dir / "establishment_tree_lists.csv", index=False)
    acres = resolved.assign(acres=resolved["county_pixels"] * ACRES_PER_PIXEL)
    return {
        "target_age": policy.target_age,
        "density": str(policy.density),
        "stratum_modes": {f"S{k}": str(v) for k, v in sorted(policy.stratum_modes.items())},
        "donors": int(donor_modes["TM_ID"].nunique()),
        "tree_list_rows": {str(m): int(n) for m, n in lists.groupby("establishment_mode").size().items()},
        "live_effect": establishment_effect(acres, donor_rows, lists),
    }


SUM_KEYS = ("county_acres", "land_acres", "published_forest_acres", "hole_acres",
            "rule_accepted_before_mmu_acres", "added_back_acres",
            "dropped_by_mmu_or_no_donor_acres", "improved_forest_acres", "patches")


def stitch(fips_list: list[str], out_root: Path = OUT_ROOT, inputs: Inputs = Inputs()) -> dict:
    dirs = [county_dir(out_root, f) for f in fips_list]
    out_dir = out_root / AOI_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    overlaps = {}
    for tif in sorted(p.name for p in dirs[0].glob("*.tif")):
        overlaps[tif] = stitch_rasters([d / tif for d in dirs], out_dir / tif)

    summaries = [json.loads((d / "summary.json").read_text()) for d in dirs]
    pd.json_normalize(summaries).to_csv(out_dir / "county_summaries.csv", index=False)
    patches = pd.concat([pd.read_csv(d / "establishment_patches.csv",
                                     dtype={"donor_tm_id": "string", "establishment_mode": "string"})
                         for d in dirs], ignore_index=True)
    patches.to_csv(out_dir / "establishment_patches.csv", index=False)
    establishment = stitch_establishment(patches, inputs, out_dir)

    total = {k: round(sum(s[k] for s in summaries), 1) for k in SUM_KEYS}
    total["counties"] = [f"{s['county_fips']} {s['county']}" for s in summaries]
    total["stitch_overlap_pixels"] = overlaps
    total["added_back_credit_acres"] = {
        m: None if summaries[0]["added_back_credit_acres"][m] is None
        else round(sum(s["added_back_credit_acres"][m] for s in summaries), 1)
        for m in summaries[0]["added_back_credit_acres"]}
    total["added_back_acres_by_mode"] = {
        m: round(sum(s["added_back_acres_by_mode"][m] for s in summaries), 1)
        for m in summaries[0]["added_back_acres_by_mode"]}
    total["establishment"] = establishment
    total["methods"] = {m: summaries[0]["methods"][m]["status"] for m in summaries[0]["methods"]}
    total["rule"] = summaries[0]["rule"]
    (out_dir / "summary.json").write_text(json.dumps(total, indent=2))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--counties", nargs="+", default=list(AOI_COUNTIES),
                        help="county FIPS codes, run one at a time (default: the 5-county AOI)")
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    parser.add_argument("--rule", type=ConsensusRule, default=ConsensusRule.UNION,
                        choices=list(ConsensusRule))
    parser.add_argument("--min-acres", type=float, default=5.0)
    parser.add_argument("--ownership-scope", type=OwnershipRepairScope,
                        default=OwnershipRepairScope.ALL_FOREST, choices=list(OwnershipRepairScope))
    parser.add_argument("--obata-tif", type=Path, default=None,
                        help="geepipe lastDist raster on the TreeMap grid (enables the Obata method)")
    parser.add_argument("--hansen-tif", type=Path, default=None,
                        help="Hansen GFC lossyear/treecover2000 on the TreeMap grid (enables Hansen)")
    parser.add_argument("--stage", type=Stage, default=Stage.ALL, choices=list(Stage))
    args = parser.parse_args()

    inputs = Inputs(obata=args.obata_tif, hansen=args.hansen_tif)
    if args.stage is Stage.ALL:
        for fips in args.counties:
            s = improve_county(fips, inputs, args.out_root, rule=args.rule,
                               min_acres=args.min_acres, scope=args.ownership_scope)
            young = s["added_back_acres_by_mode"][str(EstablishmentMode.SCALED_YOUNG)]
            print(f"{fips} {s['county']:<9} holes {s['hole_acres']:>10,.0f} ac   "
                  f"added back {s['added_back_acres']:>9,.0f} ac ({young:,.0f} scaled young)   "
                  f"forest {s['published_forest_acres']:>10,.0f} -> {s['improved_forest_acres']:>10,.0f} ac")
    total = stitch(args.counties, args.out_root, inputs)
    print(json.dumps(total, indent=2))


if __name__ == "__main__":
    main()
