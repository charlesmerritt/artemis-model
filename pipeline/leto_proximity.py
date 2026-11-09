"""LETO's ``proximity_merge`` and final unit identity, on a county's polygons.

The geometry around the two upstream ports (:mod:`pipeline.leto_small_stands`,
:mod:`pipeline.leto_management_units`), as upstream's stage 2 (``apply_proximity_merge``) and
``spatial_management_units.finalize_management_outputs`` drive them:

1. :func:`plan_groups`: under ``proximity_merge``, every parent stand's polygon, its acres,
   Harris owner code, dominant forest type and mean age go to
   :func:`~pipeline.leto_small_stands.plan_proximity_merges` with the parent pairs within
   :attr:`ProximitySettings.maximum_distance_meters` (upstream's ``GenerateNearTable``). Each
   parent stand gets its proximity group (``BIO_MU_ID``); no geometry changes.
2. :func:`final_identity`: after the riparian split, every single-part piece takes one unit ID
   per proximity group and compatibility class (:data:`COMPATIBILITY`), and a deterministic
   part number. With proximity off, every piece is its own unit, as before.
3. :func:`dissolve_units`: one feature per unit ID, multipart where its pieces are apart.
   :func:`unit_weights`: its parent stands' donor weights combined by its pieces' acres
   (upstream's ``aggregate_management_unit_weights``); :func:`representative` its largest.
4. :func:`chain_qa`: per unit, upstream's span of its pieces (``Proximity_MU_Chain_QA``).

A unit of one parent stand keeps that parent's weights and traits exactly, so with proximity
off the outputs are those of a parent stand, not a float re-weighting of them.

Distances are planar metres in the project CRS (an equal-area Albers), where upstream measures
them geodesically; at 60 m the two differ by well under a metre.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from pipeline.leto_layout import database_groups
from pipeline.leto_management_units import (
    assign_final_mu_ids,
    summarize_chain_run,
    summarize_mu_chain,
)
from pipeline.leto_small_stands import aggregate_management_unit_weights, plan_proximity_merges

# Upstream's compatibility classes present in ARTEMIS's pieces (spatial_management_units
# COMPATIBILITY_CANDIDATES); MGMT_TYPE and WETLAND are never made here.
COMPATIBILITY = ("MGMT_CLASS", "DB_GROUP", "OWN_CODE", "RIPARIAN")


class ProximityMatch(StrEnum):
    """What a proximity candidate must share with the small stand (upstream's ``require_same_*``)."""

    OWNER = "owner"
    FOREST_TYPE = "forest_type"


class Unmatched(StrEnum):
    """A small parent stand with no eligible candidate (upstream's ``retain_when_no_candidate``)."""

    RETAIN = "retain"
    ERROR = "error"


@dataclass(frozen=True)
class ProximitySettings:
    """A region's ``small_stands`` proximity limits and ``proximity_chain_qa`` warning ratio."""

    maximum_distance_meters: float = 60.0
    maximum_combined_acres: float = 300.0
    match: tuple[ProximityMatch, ...] = (ProximityMatch.OWNER, ProximityMatch.FOREST_TYPE)
    unmatched: Unmatched = Unmatched.RETAIN
    warning_chain_ratio: float = 2.0

    @classmethod
    def from_config(cls, small_stands: dict | None, segmentation: dict | None,
                    chain_qa: dict | None = None) -> ProximitySettings:
        """Upstream's ``load_config`` defaults (``config.py``), its keys and its checks."""
        small, seg = dict(small_stands or {}), dict(segmentation or {})
        match = tuple(m for m, key in ((ProximityMatch.OWNER, "require_same_ownership"),
                                       (ProximityMatch.FOREST_TYPE, "require_same_forest_type"))
                      if bool(small.get(key, True)))
        settings = cls(
            maximum_distance_meters=float(small.get("maximum_distance_meters", 60.0)),
            maximum_combined_acres=float(small.get("maximum_combined_acres",
                                                   seg.get("maximum_stand_acres", 300.0))),
            match=match,
            unmatched=Unmatched.RETAIN if bool(small.get("retain_when_no_candidate", True))
            else Unmatched.ERROR,
            warning_chain_ratio=float((chain_qa or {}).get("warning_chain_ratio", 2.0)))
        if settings.warning_chain_ratio <= 0:
            raise ValueError("proximity_chain_qa.warning_chain_ratio must be positive.")
        return settings

    def check(self, minimum_acres: float) -> None:
        """Upstream's check under ``proximity_merge``: the cap holds at least one stand."""
        if self.maximum_combined_acres < minimum_acres:
            raise ValueError("small_stands.maximum_combined_acres must be at least "
                             "segmentation.minimum_stand_acres.")

    def record(self) -> dict:
        return {"maximum_distance_meters": self.maximum_distance_meters,
                "maximum_combined_acres": self.maximum_combined_acres,
                "match": [m.value for m in self.match], "unmatched": self.unmatched.value,
                "warning_chain_ratio": self.warning_chain_ratio}


# ── 1. proximity groups of parent stands ───────────────────────────────────────────────


@dataclass(frozen=True)
class ProximityPlan:
    groups: pd.Series            # SEG_ID -> BIO_MU_ID, every parent stand
    merges: pd.DataFrame         # upstream's Proximity merge rows, one per accepted pair
    qa: dict                     # upstream's run counters


def candidates(parents: gpd.GeoDataFrame, sources: np.ndarray, maximum_distance: float
               ) -> pd.DataFrame:
    """Every parent within ``maximum_distance`` metres of a source parent (upstream's near table).

    Only small parents are ever sources, so pairs are taken from them alone.
    """
    geoms = parents.geometry.to_numpy()
    ids = parents["SEG_ID"].to_numpy(dtype=np.int64)
    left, right = shapely.STRtree(geoms).query(geoms[sources], predicate="dwithin",
                                               distance=maximum_distance)
    left = sources[left]
    keep = left != right
    left, right = left[keep], right[keep]
    return pd.DataFrame({"SOURCE_SEG_ID": ids[left], "TARGET_SEG_ID": ids[right],
                         "DISTANCE_METERS": shapely.distance(geoms[left], geoms[right])})


def plan_groups(parents: gpd.GeoDataFrame, traits: pd.DataFrame, *, minimum_acres: float,
                settings: ProximitySettings) -> ProximityPlan:
    """Upstream's ``apply_proximity_merge``: each parent stand's proximity group.

    ``parents``: one polygon per ``SEG_ID`` with ``OWN_CODE`` and geodesic ``Acres``, in a
    metric CRS. ``traits``, by ``SEG_ID``: ``FORTYPCD_DOM`` and ``STDAGE_MEAN``.
    """
    units = parents[["SEG_ID", "Acres", "OWN_CODE"]].join(
        traits[["FORTYPCD_DOM", "STDAGE_MEAN"]], on="SEG_ID")
    sources = np.flatnonzero(parents["Acres"].to_numpy(dtype=float) < minimum_acres)
    mapping, merges, qa = plan_proximity_merges(
        units, candidates(parents, sources, settings.maximum_distance_meters),
        minimum_acres=minimum_acres,
        maximum_distance_meters=settings.maximum_distance_meters,
        maximum_combined_acres=settings.maximum_combined_acres,
        require_same_ownership=ProximityMatch.OWNER in settings.match,
        require_same_forest_type=ProximityMatch.FOREST_TYPE in settings.match,
        retain_when_no_candidate=settings.unmatched is Unmatched.RETAIN)
    return ProximityPlan(mapping.set_index("SEG_ID")["MU_ID"].rename("BIO_MU_ID"), merges, qa)


# ── 2. final identity of the riparian split's pieces ───────────────────────────────────


def _compatibility(pieces: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return pieces.assign(MGMT_CLASS=pieces["unit_class"].astype(str),
                         DB_GROUP=database_groups(pieces).to_numpy())


def final_identity(pieces: gpd.GeoDataFrame, fips: str, *, groups: pd.Series | None
                   ) -> gpd.GeoDataFrame:
    """Each single-part piece's ``unit_id`` (``<FIPS>-<MU_ID>``), ``BIO_MU_ID`` and ``MU_PART``.

    ``pieces`` need ``SEG_ID``, ``RIPARIAN``, ``OWN_CODE``, ``owner_class`` and ``unit_class``.
    With ``groups`` (``SEG_ID`` -> ``BIO_MU_ID``, under ``proximity_merge``) a unit is one
    proximity group's pieces of one compatibility class (:data:`COMPATIBILITY`), numbered as
    upstream does. Without, every piece is its own unit, numbered by parent stand then class.
    Returned in unit order, then part.
    """
    ordered = pieces.sort_values(["SEG_ID", "RIPARIAN"], kind="stable").reset_index(drop=True)
    frame = _compatibility(ordered).assign(SOURCE_OID=np.arange(1, len(ordered) + 1))
    if groups is None:
        frame["MU_ID"] = frame["SOURCE_OID"]
    else:
        frame["MU_ID"] = frame["SEG_ID"].map(groups).astype("int64")
    frame["BIO_MU_ID"] = frame["MU_ID"]
    frame = assign_final_mu_ids(frame, compatibility_fields=list(COMPATIBILITY),
                                proximity_enabled=groups is not None)
    frame = frame.sort_values(["MU_ID", "MU_PART"], kind="stable").reset_index(drop=True)
    frame.insert(0, "unit_id", fips + "-" + frame["MU_ID"].astype(str))
    return gpd.GeoDataFrame(frame.drop(columns=["SOURCE_OID", "MGMT_CLASS"]),
                            geometry="geometry", crs=pieces.crs)


# ── 3. units ───────────────────────────────────────────────────────────────────────────


def dissolve_units(segments: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One feature per ``unit_id``: its first part's row, its pieces' union and summed ``Acres``.

    ``SEG_ID`` is its first part's parent stand; ``SEG_IDS`` lists them all, ``PART_COUNT``
    counts its pieces.
    """
    first = segments.sort_values(["unit_id", "MU_PART"], kind="stable").drop_duplicates("unit_id")
    by_unit = segments.groupby("unit_id", sort=False)
    geometry = by_unit.geometry.agg(lambda g: shapely.union_all(g.to_numpy()))
    seg_ids = by_unit["SEG_ID"].agg(lambda s: ",".join(str(v) for v in sorted(set(s.astype(int)))))
    units = first.set_index("unit_id").drop(columns=["MU_PART"])
    units = units.assign(Acres=by_unit["Acres"].sum(), SEG_IDS=seg_ids,
                         PART_COUNT=by_unit.size(), geometry=geometry)
    units = units.loc[pd.unique(segments["unit_id"])].reset_index()
    return gpd.GeoDataFrame(units, geometry="geometry", crs=segments.crs)


def unit_traits(segments: gpd.GeoDataFrame, parent: pd.DataFrame) -> pd.DataFrame:
    """Each unit's parent-stand traits weighted by its pieces' acres, by ``unit_id``.

    ``parent``, by ``SEG_ID``: continuous traits and ``FORTYPCD``; a unit's ``FORTYPCD`` is the
    one holding most of its acres, ties to the lower code (upstream's ``FORTYPCD_DOM``).
    """
    rows = parent.reindex(segments["SEG_ID"].to_numpy()).reset_index(drop=True)
    acres = segments["Acres"].to_numpy(dtype=float)
    unit = segments["unit_id"].to_numpy()
    continuous = [c for c in parent.columns if c != "FORTYPCD"]
    weighted = rows[continuous].mul(acres, axis=0).groupby(unit).sum()
    out = weighted.div(pd.Series(acres).groupby(unit).sum(), axis=0)
    types = (pd.DataFrame({"unit": unit, "FORTYPCD": rows["FORTYPCD"], "acres": acres})
             .groupby(["unit", "FORTYPCD"], as_index=False)["acres"].sum()
             .sort_values(["unit", "acres", "FORTYPCD"], ascending=[True, False, True])
             .drop_duplicates("unit").set_index("unit")["FORTYPCD"])
    out["FORTYPCD"] = types
    sole = _one_parent(segments)
    out.loc[sole.index, parent.columns] = parent.reindex(sole.to_numpy())[list(parent.columns)
                                                                         ].to_numpy()
    return out.reindex(pd.unique(unit)).rename_axis("unit_id")


def _one_parent(segments: pd.DataFrame) -> pd.Series:
    """Each unit's sole parent stand, for the units that have one (by ``unit_id``)."""
    parents = segments.groupby("unit_id")["SEG_ID"]
    return parents.first()[parents.nunique() == 1]


def unit_weights(parent_weights: pd.DataFrame, segments: pd.DataFrame) -> pd.DataFrame:
    """Each unit's donor weights: its parent stands' combined by its pieces' acres.

    ``parent_weights``: ``SEG_ID``, ``TREEMAP_VALUE``, ``PLT_CN``, ``WEIGHT`` (unfiltered).
    Upstream combines by ``PLT_CN``; a plot's ``TREEMAP_VALUE`` is its lowest. Returns
    ``unit_id``, ``MU_ID``, ``PLT_CN``, ``TREEMAP_VALUE``, ``WEIGHT`` and upstream's
    ``DONOR_ACRES``, ``MU_ACRES`` and ``SOURCE_SEG_IDS``.
    """
    combined = aggregate_management_unit_weights(parent_weights[["SEG_ID", "PLT_CN", "WEIGHT"]],
                                                 segments[["SEG_ID", "MU_ID", "Acres"]])
    unit_of = segments.drop_duplicates("MU_ID").set_index("MU_ID")["unit_id"]
    combined.insert(0, "unit_id", combined["MU_ID"].map(unit_of))
    by_plot = parent_weights.groupby(["SEG_ID", "PLT_CN"])["WEIGHT"].sum()
    sole = combined["unit_id"].map(_one_parent(segments))
    exact = sole.notna()
    combined.loc[exact, "WEIGHT"] = by_plot.reindex(
        pd.MultiIndex.from_arrays([sole[exact].astype("int64"), combined.loc[exact, "PLT_CN"]])
    ).to_numpy()
    combined["TREEMAP_VALUE"] = combined["PLT_CN"].map(
        parent_weights.groupby("PLT_CN")["TREEMAP_VALUE"].min()).astype("int64")
    return combined[["unit_id", "MU_ID", "PLT_CN", "TREEMAP_VALUE", "WEIGHT", "DONOR_ACRES",
                     "MU_ACRES", "SOURCE_SEG_IDS"]]


def representative(weights: pd.DataFrame) -> pd.DataFrame:
    """Each unit's largest donor, ties to the lower ``TREEMAP_VALUE``, by ``unit_id``."""
    top = (weights.sort_values(["unit_id", "WEIGHT", "TREEMAP_VALUE"],
                               ascending=[True, False, True])
           .drop_duplicates("unit_id").set_index("unit_id"))
    return top[["TREEMAP_VALUE", "PLT_CN", "WEIGHT"]]


# ── 4. chain QA ────────────────────────────────────────────────────────────────────────


def chain_qa(segments: gpd.GeoDataFrame, settings: ProximitySettings
             ) -> tuple[pd.DataFrame, dict]:
    """Upstream's ``Proximity_MU_Chain_QA`` per unit (by ``unit_id``) and its run counters."""
    rows = []
    for unit_id, unit in segments.groupby("unit_id", sort=False):
        geoms = unit.geometry.to_numpy()
        pairs = [{"boundary_distance_m": shapely.distance(geoms[a], geoms[b]),
                  "centroid_distance_m": shapely.distance(shapely.centroid(geoms[a]),
                                                          shapely.centroid(geoms[b]))}
                 for a, b in combinations(range(len(geoms)), 2)]
        row = summarize_mu_chain(unit, pairs,
                                 maximum_distance_meters=settings.maximum_distance_meters,
                                 warning_chain_ratio=settings.warning_chain_ratio)
        rows.append({"unit_id": unit_id, **row})
    table = pd.DataFrame(rows)
    return table, summarize_chain_run(table)
