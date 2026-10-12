"""Pixel-class landscapes over any TreeMap/ownership raster pair, carved by the BMP buffers.

The 2026-08-10 -> 2026-08-24 recipe, unchanged, made to take its rasters as arguments:
a scheduling unit is `TreeMap plot x county x Harris owner class`, the BMP stream buffers cut
every unit they cross, and each 8-connected run of buffered forest becomes a riparian stand
split only at county and owner lines, carrying its majority plot.

One addition for the improved rasters. A provenance-4 pixel ("added back young") carries its
donor's `TM_ID` for forest type and species mix only (AGENTS.md, ADR 0002), so it is its own
class, `YTM<tm_id>`, and reads the FVS stand `Y<tm_id>`: the donor plot's site record with
the scaled establishment list for trees, at the list's establishment age. A provenance-2 pixel
(donor as is) is the donor plot, like any published pixel.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

# Harris et al. 2025 classes that carry a TPO owner group (2026-08-10 `OWNER_CLASSES`).
# 0 unknown, 1 non-forest and 2 water are dropped from scheduling.
OWNER_CODES = (3, 4, 5, 6, 7, 8)
PROVENANCE_ADDED_BACK_YOUNG = 4
YOUNG_PREFIX = "Y"


class PixelOrigin(StrEnum):
    TREEMAP = "treemap"              # the TM_ID's own FIA plot
    SCALED_YOUNG = "scaled_young"    # the donor's scaled establishment list


def stand_key(tm_id: int, origin: PixelOrigin) -> str:
    prefix = YOUNG_PREFIX if origin is PixelOrigin.SCALED_YOUNG else ""
    return f"{prefix}TM{tm_id}"


def unit_id(tm_id: int, origin: PixelOrigin, county: str, owner_code: int) -> str:
    return f"{stand_key(tm_id, origin)}_{county}_O{owner_code}"


def young_stand_cn(tm_id) -> str:
    return f"{YOUNG_PREFIX}{tm_id}"


def _origin(provenance: np.ndarray | None, mask: np.ndarray) -> np.ndarray:
    if provenance is None:
        return np.zeros(int(mask.sum()), dtype=bool)
    return provenance[mask] == PROVENANCE_ADDED_BACK_YOUNG


def _origin_labels(young: pd.Series) -> pd.Series:
    return young.map({True: PixelOrigin.SCALED_YOUNG, False: PixelOrigin.TREEMAP})


def pixel_pieces(tm: np.ndarray, nodata, provenance: np.ndarray | None, county: np.ndarray,
                 owner: np.ndarray, smz: np.ndarray, county_names: dict[int, str],
                 smz_names: dict[int, str]) -> pd.DataFrame:
    """Pixel counts per (tm_id, origin, county, owner_code, buffer_class); `""` = no buffer."""
    valid = (tm != nodata) & (county > 0)
    df = pd.DataFrame({
        "tm_id": tm[valid].astype("int64"),
        "young": _origin(provenance, valid),
        "county_code": county[valid],
        "owner_code": owner[valid].astype("int64"),
        "smz_code": smz[valid],
    })
    counts = df.value_counts().rename("pixel_count").reset_index()
    counts["origin"] = _origin_labels(counts.pop("young"))
    counts["county"] = counts.pop("county_code").map(county_names)
    counts["buffer_class"] = counts.pop("smz_code").map(smz_names).fillna("")
    return counts


def upland_remainders(pieces: pd.DataFrame) -> pd.DataFrame:
    """Each attributed unit minus the pixels any buffer took."""
    keep = pieces[pieces["owner_code"].isin(OWNER_CODES) & (pieces["buffer_class"] == "")]
    up = (keep.groupby(["tm_id", "origin", "county", "owner_code"], as_index=False)
          ["pixel_count"].sum())
    up["unit_id"] = [unit_id(t, o, c, w) for t, o, c, w in
                     zip(up["tm_id"], up["origin"], up["county"], up["owner_code"])]
    up["unit_class"] = "managed"
    return up


def riparian_pixels(tm: np.ndarray, nodata, provenance: np.ndarray | None, county: np.ndarray,
                    owner: np.ndarray, smz: np.ndarray,
                    county_names: dict[int, str]) -> pd.DataFrame:
    """Every buffered, attributed forest pixel with its 8-connected corridor label."""
    from scipy import ndimage

    attributed = (tm != nodata) & (county > 0) & np.isin(owner, OWNER_CODES)
    riparian = attributed & (smz > 0)
    labels, _ = ndimage.label(riparian, structure=np.ones((3, 3), dtype=bool))
    return pd.DataFrame({
        "corridor_id": labels[riparian],
        "tm_id": tm[riparian].astype("int64"),
        "origin": _origin_labels(pd.Series(_origin(provenance, riparian))).to_numpy(),
        "county": pd.Series(county[riparian]).map(county_names).to_numpy(),
        "owner_code": owner[riparian].astype("int64"),
    })


def riparian_stands(flat: pd.DataFrame) -> pd.DataFrame:
    """One stand per (corridor, county, owner), carrying its majority class.

    The class is coded `tm_id * 2 + young` so that, on the published raster where nothing is
    young, a tie breaks to the smallest TM_ID exactly as 2026-08-24's `s.mode().iat[0]` did.
    """
    code = flat["tm_id"] * 2 + (flat["origin"] == PixelOrigin.SCALED_YOUNG).astype("int64")
    key = ["corridor_id", "county", "owner_code"]
    stands = (flat.assign(code=code).groupby(key)
              .agg(pixel_count=("code", "size"), majority=("code", lambda s: s.mode().iat[0]))
              .reset_index())
    stands["tm_id"] = stands["majority"] // 2
    stands["origin"] = _origin_labels((stands.pop("majority") % 2).astype(bool))
    stands["unit_id"] = ("RIP" + stands["corridor_id"].astype(str) + "_" + stands["county"]
                         + "_O" + stands["owner_code"].astype(str))
    stands["unit_class"] = "riparian"
    return stands


def stand_plot(stands: pd.DataFrame, xwalk: pd.DataFrame) -> pd.DataFrame:
    """`PLT_CN` (the FVS stand each class reads) and the donor's `FORTYPCD`.

    `xwalk` maps `tm_id` to the plot's exact `PLT_CN` string and `FORTYPCD`. A scaled-young
    class reads `Y<tm_id>`, never the donor plot; the donor still sets the forest type.
    """
    out = stands.merge(xwalk[["tm_id", "PLT_CN", "FORTYPCD"]], on="tm_id", how="inner")
    young = out["origin"] == PixelOrigin.SCALED_YOUNG
    out.loc[young, "PLT_CN"] = out.loc[young, "tm_id"].map(young_stand_cn)
    return out


def young_fvs_inputs(lists: pd.DataFrame, donor_stands: pd.DataFrame
                     ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """FVS StandInit and TreeInit rows for every scaled-young establishment list.

    The stand is the donor plot's StandInit row (location, site, plot design) under
    `STAND_CN = Y<TM_ID>`, aged at the list's `establishment_age`. Each list row becomes one
    live tree record whose `TREE_COUNT` expands, through the donor's microplot design, to the
    list's `TPA_UNADJ`. Scaled trees are saplings; one at or above the design's DBH
    breakpoint would expand on the subplot instead, so it is refused rather than mis-scaled.
    """
    young = lists[lists["establishment_mode"] == str(PixelOrigin.SCALED_YOUNG)].copy()
    first = young.drop_duplicates("TM_ID")[["TM_ID", "PLT_CN", "establishment_age"]]
    stand = first.merge(donor_stands, left_on="PLT_CN", right_on="STAND_CN", how="inner",
                        validate="many_to_one")
    if len(stand) != len(first):
        missing = sorted(set(first["PLT_CN"]) - set(donor_stands["STAND_CN"]))
        raise KeyError(f"donor plots without a StandInit row: {missing[:5]}")
    stand["STAND_CN"] = stand["TM_ID"].map(young_stand_cn)
    stand["STAND_ID"] = stand["STAND_CN"]
    stand["AGE"] = stand.pop("establishment_age").astype(float)
    stand = stand.drop(columns=["TM_ID", "PLT_CN"])

    design = stand.set_index("STAND_CN")[["INV_PLOT_SIZE", "NUM_PLOTS", "BRK_DBH"]]
    trees = young.assign(STAND_CN=young["TM_ID"].map(young_stand_cn)).join(design, on="STAND_CN")
    if (trees["DIA"] >= trees["BRK_DBH"]).any():
        raise ValueError("a scaled-young tree at or above the DBH breakpoint would be "
                         "expanded on the subplot, not the microplot")
    trees = pd.DataFrame({
        "STAND_CN": trees["STAND_CN"],
        "STAND_ID": trees["STAND_CN"],
        "PLOT_ID": 1,
        "TREE_ID": trees.groupby("STAND_CN").cumcount() + 1,
        "TREE_COUNT": trees["TPA_UNADJ"] * trees["NUM_PLOTS"] / trees["INV_PLOT_SIZE"],
        "HISTORY": 1,
        "SPECIES": trees["SPCD"].astype("int64").map(str),
        "DIAMETER": trees["DIA"],
        "HT": trees["HT"],
    }).reset_index(drop=True)
    return stand.reset_index(drop=True), trees
